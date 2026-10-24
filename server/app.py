"""tv-remote server: MCP (Streamable HTTP, JSON response) + OAuth + long-poll relay ไปหาแอปบนทีวี

Claude (มือถือ/เว็บ/Desktop) ──OAuth──> /mcp ──queue──> /tv/poll <── แอป com.pinato.remote บนทีวี

ทุกอย่าง stateless ด้วย HMAC (SECRET_KEY) — redeploy แล้ว token เดิมยังใช้ได้
เปลี่ยน SECRET_KEY = ถอน token ของ Claude ทุกตัว

env:
  SECRET_KEY        สุ่มยาว ใช้เซ็น client_id / code / token
  OWNER_PASSWORD    รหัสที่กรอกตอนเชื่อม connector ใน claude.ai
  TV_DEVICE_TOKEN   (ไม่ใส่ก็ได้) token คงที่สำหรับแอปทีวีที่จับคู่ผ่าน adb แบบเก่า
  DEVICE_EPOCH      (ไม่ใส่ก็ได้) เพิ่มเลขนี้ = ถอนทีวีที่จับคู่ด้วยรหัสทุกเครื่อง
  PUBLIC_URL       (ไม่ใส่ก็ได้) default https://$RAILWAY_PUBLIC_DOMAIN
  BLOCKED_DOMAINS   โดเมนเพิ่มเติมที่ห้ามเปิด คั่นด้วย comma
"""
import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
import uuid
from urllib.parse import urlencode, urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

SECRET_KEY = os.environ["SECRET_KEY"].encode()
OWNER_PASSWORD = os.environ["OWNER_PASSWORD"]
TV_DEVICE_TOKEN = os.environ.get("TV_DEVICE_TOKEN", "")
DEVICE_EPOCH = int(os.environ.get("DEVICE_EPOCH", "1"))
PUBLIC_URL = os.environ.get("PUBLIC_URL") or f"https://{os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'localhost:8000')}"
PUBLIC_URL = PUBLIC_URL.rstrip("/")

# ห้ามเปิดเว็บสตรีมผิดลิขสิทธิ์ — เพิ่มโดเมนของคุณเองได้ทาง env BLOCKED_DOMAINS
BLOCKED_DOMAINS = {"ball67.com", "v2h-cdn.com"} | {
    d.strip().lower() for d in os.environ.get("BLOCKED_DOMAINS", "").split(",") if d.strip()
}

# redirect ที่ยอมให้ลงทะเบียน: claude.ai / claude.com + localhost (Claude Code)
ALLOWED_REDIRECTS = [
    re.compile(r"^https://claude\.ai/api/mcp/auth_callback$"),
    re.compile(r"^https://claude\.com/api/mcp/auth_callback$"),
    re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?/.*$"),
]

ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
CODE_TTL = 300

APPS = ["netflix", "youtube", "prime", "appletv", "livetv", "browser", "pinato", "tvhub"]
KEYS = [
    "home", "back", "up", "down", "left", "right", "ok", "play", "pause", "stop",
    "rewind", "forward", "next", "prev", "volume_up", "volume_down", "mute",
    "channel_up", "channel_down", "input", "subtitle", "audio", "guide", "info",
]


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


# ---------- HMAC tokens ----------

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(kind: str, data: dict, ttl: int | None) -> str:
    body = {"k": kind, **data}
    if ttl:
        body["exp"] = int(time.time()) + ttl
    raw = _b64(json.dumps(body, separators=(",", ":")).encode())
    mac = _b64(hmac.new(SECRET_KEY, raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{mac}"


def verify(token: str, kind: str) -> dict | None:
    try:
        raw, mac = token.split(".")
        good = _b64(hmac.new(SECRET_KEY, raw.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, good):
            return None
        body = json.loads(_unb64(raw))
    except Exception:
        return None
    if body.get("k") != kind or ("exp" in body and body["exp"] < time.time()):
        return None
    return body


# ---------- OAuth ----------

def auth_server_meta(_: Request):
    return JSONResponse({
        "issuer": PUBLIC_URL,
        "authorization_endpoint": f"{PUBLIC_URL}/authorize",
        "token_endpoint": f"{PUBLIC_URL}/token",
        "registration_endpoint": f"{PUBLIC_URL}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["tv"],
    })


def resource_meta(_: Request):
    return JSONResponse({
        "resource": f"{PUBLIC_URL}/mcp",
        "authorization_servers": [PUBLIC_URL],
        "scopes_supported": ["tv"],
        "bearer_methods_supported": ["header"],
    })


async def register(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_client_metadata"}, 400)
    uris = body.get("redirect_uris") or []
    if not uris or not all(isinstance(u, str) and any(p.match(u) for p in ALLOWED_REDIRECTS) for u in uris):
        log("register: rejected redirect_uris", uris)
        return JSONResponse({"error": "invalid_redirect_uri"}, 400)
    client_id = sign("client", {"r": uris, "n": body.get("client_name", "")[:60]}, None)
    log("register:", body.get("client_name"), uris)
    return JSONResponse({
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }, 201)


_fails: list[float] = []
_used_codes: dict[str, float] = {}


def _locked() -> bool:
    now = time.time()
    _fails[:] = [t for t in _fails if now - t < 900]
    return len(_fails) >= 5


AUTH_PAGE = """<!doctype html><html lang="th"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TV Remote · เชื่อม Claude</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:420px;margin:12vh auto;padding:0 16px;color:#222}}
input,button{{font-size:17px;padding:12px;width:100%;box-sizing:border-box;margin-top:10px;border-radius:10px;border:1px solid #ccc}}
button{{background:#5b3a8c;color:#fff;border:0}} .err{{color:#b00}} small{{color:#666}}</style>
<h2>📺 TV Remote</h2>
<p><b>{client}</b> ขอสิทธิ์สั่งทีวีที่บ้าน</p>
{err}
<form method="post">
{hidden}
<input type="password" name="password" placeholder="รหัสเจ้าของ" autofocus required>
<button>อนุญาต</button>
</form>
<p><small>ส่งกลับไปที่ {host}</small></p>
</html>"""


def _auth_params(src) -> dict:
    keys = ["client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "response_type", "scope", "resource"]
    return {k: src.get(k, "") for k in keys}


def _check_auth_request(p: dict):
    client = verify(p["client_id"], "client")
    if not client:
        return None, "client_id ไม่ถูกต้อง"
    if p["redirect_uri"] not in client["r"]:
        return None, "redirect_uri ไม่ตรงกับที่ลงทะเบียน"
    if p["response_type"] != "code" or p["code_challenge_method"] != "S256" or not p["code_challenge"]:
        return None, "ต้องใช้ response_type=code + PKCE S256"
    return client, None


def _auth_page(p, client, err=""):
    hidden = "".join(f'<input type="hidden" name="{k}" value="{html.escape(v)}">' for k, v in p.items())
    return HTMLResponse(AUTH_PAGE.format(
        client=html.escape(client.get("n") or "Claude"),
        err=f'<p class="err">{html.escape(err)}</p>' if err else "",
        hidden=hidden,
        host=html.escape(urlsplit(p["redirect_uri"]).netloc),
    ))


async def authorize(request: Request):
    if request.method == "GET":
        p = _auth_params(request.query_params)
        client, err = _check_auth_request(p)
        if err:
            return HTMLResponse(f"<p>{html.escape(err)}</p>", 400)
        return _auth_page(p, client)

    form = await request.form()
    p = _auth_params(form)
    client, err = _check_auth_request(p)
    if err:
        return HTMLResponse(f"<p>{html.escape(err)}</p>", 400)
    if _locked():
        return _auth_page(p, client, "ใส่รหัสผิดหลายครั้ง รอ 15 นาที")
    if not hmac.compare_digest(str(form.get("password", "")).encode(), OWNER_PASSWORD.encode()):
        _fails.append(time.time())
        log("authorize: wrong password")
        await asyncio.sleep(1)
        return _auth_page(p, client, "รหัสไม่ถูกต้อง")
    code = sign("code", {
        "c": hashlib.sha256(p["client_id"].encode()).hexdigest()[:32],
        "r": p["redirect_uri"],
        "ch": p["code_challenge"],
        "j": secrets.token_hex(8),
    }, CODE_TTL)
    log("authorize: granted to", client.get("n"))
    q = {"code": code}
    if p["state"]:
        q["state"] = p["state"]
    return RedirectResponse(f"{p['redirect_uri']}{'&' if '?' in p['redirect_uri'] else '?'}{urlencode(q)}", 302)


def _issue(client_id: str):
    cid = hashlib.sha256(client_id.encode()).hexdigest()[:32]
    return JSONResponse({
        "access_token": sign("access", {"c": cid}, ACCESS_TTL),
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL,
        "refresh_token": sign("refresh", {"c": cid, "j": secrets.token_hex(8)}, REFRESH_TTL),
        "scope": "tv",
    }, headers={"Cache-Control": "no-store"})


async def token(request: Request):
    f = await request.form()
    gt = f.get("grant_type")
    client_id = f.get("client_id", "")
    if not verify(client_id, "client"):
        return JSONResponse({"error": "invalid_client"}, 401)
    cid = hashlib.sha256(client_id.encode()).hexdigest()[:32]

    if gt == "authorization_code":
        c = verify(f.get("code", ""), "code")
        if not c or c["c"] != cid or c["r"] != f.get("redirect_uri"):
            return JSONResponse({"error": "invalid_grant"}, 400)
        now = time.time()
        for j, t in list(_used_codes.items()):
            if now - t > CODE_TTL:
                del _used_codes[j]
        if c["j"] in _used_codes:
            return JSONResponse({"error": "invalid_grant", "error_description": "code used"}, 400)
        verifier = f.get("code_verifier", "")
        if _b64(hashlib.sha256(verifier.encode()).digest()) != c["ch"]:
            return JSONResponse({"error": "invalid_grant", "error_description": "pkce"}, 400)
        _used_codes[c["j"]] = now
        return _issue(client_id)

    if gt == "refresh_token":
        r = verify(f.get("refresh_token", ""), "refresh")
        if not r or r["c"] != cid:
            return JSONResponse({"error": "invalid_grant"}, 400)
        return _issue(client_id)

    return JSONResponse({"error": "unsupported_grant_type"}, 400)


# ---------- relay ไปทีวี ----------

class TV:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.pending: dict[str, asyncio.Future] = {}
        self.last_poll = 0.0
        self.info: dict = {}

    def online(self) -> bool:
        return time.time() - self.last_poll < 45

    async def call(self, cmd: str, args: dict, timeout=15) -> dict:
        if not self.online():
            raise ToolError("ทีวีไม่ได้ออนไลน์ (ปิดอยู่ / standby / แอป TV Remote ไม่ได้รัน)")
        cid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = fut
        await self.queue.put({"id": cid, "cmd": cmd, "args": args, "t": time.time()})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise ToolError("ทีวีไม่ตอบภายในเวลา")
        finally:
            self.pending.pop(cid, None)


tv = TV()


def _tv_auth(request: Request) -> bool:
    h = request.headers.get("authorization", "")
    if not h.startswith("Bearer "):
        return False
    if TV_DEVICE_TOKEN and hmac.compare_digest(h.encode(), f"Bearer {TV_DEVICE_TOKEN}".encode()):
        return True
    d = verify(h[7:], "device")
    return bool(d and d.get("e") == DEVICE_EPOCH)


# ---------- จับคู่ทีวีด้วยรหัสบนจอ (ไม่ต้องใช้ adb) ----------
# ทีวี: POST /tv/pair/start → โชว์รหัส → POST /tv/pair/poll จนได้ device_token
# คน:   เปิด /pair บนมือถือ → ใส่รหัส + รหัสเจ้าของ

PAIR_TTL = 600
_pairs: dict[str, dict] = {}  # pair_id → {code, exp, token}


def _clean_pairs():
    now = time.time()
    for k, v in list(_pairs.items()):
        if v["exp"] < now:
            del _pairs[k]


async def pair_start(_: Request):
    _clean_pairs()
    if len(_pairs) >= 50:
        return JSONResponse({"error": "busy"}, 429)
    taken = {v["code"] for v in _pairs.values()}
    code = f"{secrets.randbelow(10**6):06d}"
    while code in taken:
        code = f"{secrets.randbelow(10**6):06d}"
    pid = secrets.token_urlsafe(24)
    _pairs[pid] = {"code": code, "exp": time.time() + PAIR_TTL, "token": None}
    log("pair: started", code[:3] + "***")
    return JSONResponse({"pair_id": pid, "code": f"{code[:3]}-{code[3:]}", "url": f"{PUBLIC_URL}/pair",
                         "expires_in": PAIR_TTL})


async def pair_poll(request: Request):
    try:
        pid = (await request.json()).get("pair_id", "")
    except Exception:
        return JSONResponse({"error": "bad request"}, 400)
    _clean_pairs()
    p = _pairs.get(pid)
    if not p:
        return JSONResponse({"status": "expired"}, 410)
    if not p["token"]:
        return JSONResponse({"status": "pending"})
    del _pairs[pid]
    return JSONResponse({"status": "ok", "device_token": p["token"]})


PAIR_PAGE = """<!doctype html><html lang="th"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TV Remote · จับคู่ทีวี</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:420px;margin:10vh auto;padding:0 16px;color:#222}}
input,button{{font-size:18px;padding:12px;width:100%;box-sizing:border-box;margin-top:10px;border-radius:10px;border:1px solid #ccc}}
button{{background:#5b3a8c;color:#fff;border:0}} .err{{color:#b00}} .ok{{color:#060}}</style>
<h2>📺 จับคู่ทีวี</h2>
{msg}
<form method="post">
<input name="code" inputmode="numeric" placeholder="รหัส 6 หลักบนจอทีวี" autocomplete="off" required>
<input type="password" name="password" placeholder="รหัสเจ้าของ" required>
<button>จับคู่</button>
</form>
</html>"""


async def pair_page(request: Request):
    if request.method == "GET":
        return HTMLResponse(PAIR_PAGE.format(msg=""))
    form = await request.form()
    msg = lambda m, cls="err": HTMLResponse(PAIR_PAGE.format(msg=f'<p class="{cls}">{html.escape(m)}</p>'))
    if _locked():
        return msg("ใส่รหัสผิดหลายครั้ง รอ 15 นาที")
    if not hmac.compare_digest(str(form.get("password", "")).encode(), OWNER_PASSWORD.encode()):
        _fails.append(time.time())
        await asyncio.sleep(1)
        return msg("รหัสเจ้าของไม่ถูกต้อง")
    code = re.sub(r"\D", "", str(form.get("code", "")))
    _clean_pairs()
    p = next((v for v in _pairs.values() if v["code"] == code and not v["token"]), None)
    if not p:
        return msg("ไม่พบรหัสนี้ หรือหมดอายุแล้ว — ดูรหัสใหม่บนจอทีวี")
    p["token"] = sign("device", {"d": uuid.uuid4().hex[:12], "e": DEVICE_EPOCH}, None)
    log("pair: approved")
    return msg("จับคู่สำเร็จ ✅ ทีวีจะออนไลน์ในไม่กี่วินาที", "ok")


async def tv_poll(request: Request):
    if not _tv_auth(request):
        return Response(status_code=401)
    tv.last_poll = time.time()
    if request.headers.get("x-tv-info"):
        try:
            tv.info = json.loads(request.headers["x-tv-info"])
        except Exception:
            pass
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            item = await asyncio.wait_for(tv.queue.get(), deadline - time.time())
        except asyncio.TimeoutError:
            break
        tv.last_poll = time.time()
        if time.time() - item["t"] > 15:  # คนสั่งเลิกรอแล้ว อย่าทำย้อนหลัง
            continue
        return JSONResponse({k: item[k] for k in ("id", "cmd", "args")})
    tv.last_poll = time.time()
    return Response(status_code=204)


async def tv_result(request: Request):
    if not _tv_auth(request):
        return Response(status_code=401)
    tv.last_poll = time.time()
    body = await request.json()
    fut = tv.pending.get(body.get("id"))
    if fut and not fut.done():
        fut.set_result(body)
    return Response(status_code=204)


# ---------- MCP tools ----------

class ToolError(Exception):
    pass


def _blocked(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


async def _run(cmd, args):
    r = await tv.call(cmd, args)
    if not r.get("ok"):
        raise ToolError(r.get("message") or "ทีวีทำไม่สำเร็จ")
    return r.get("message") or "ok"


async def t_status(a):
    if not tv.online():
        ago = int(time.time() - tv.last_poll) if tv.last_poll else None
        return f"ทีวีออฟไลน์" + (f" (เห็นล่าสุด {ago} วินาทีก่อน)" if ago else "")
    return "ทีวีออนไลน์ · " + await _run("status", {})


async def t_open_url(a):
    url = str(a.get("url", "")).strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolError("รับเฉพาะลิงก์ http:// หรือ https://")
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise ToolError("ลิงก์มีช่องว่างหรืออักขระควบคุม")
    if _blocked(parts.hostname):
        log("open_url blocked:", parts.hostname)
        raise ToolError("โดเมนนี้ถูกบล็อก (เว็บสตรีมที่ไม่มีลิขสิทธิ์) — ไม่เปิดให้")
    return await _run("open_url", {"url": url})


async def t_open_app(a):
    app = a.get("app")
    if app not in APPS:
        raise ToolError(f"ไม่รู้จักแอป {app}")
    return await _run("open_app", {"app": app})


async def t_volume(a):
    action = a.get("action")
    if action not in ("up", "down", "mute", "unmute", "set", "get"):
        raise ToolError("action ไม่ถูกต้อง")
    args = {"action": action}
    if action == "set":
        level = a.get("level")
        if not isinstance(level, int) or not 0 <= level <= 100:
            raise ToolError("level ต้องเป็นตัวเลข 0–100")
        args["level"] = level
    return await _run("volume", args)


async def t_remote(a):
    keys = a.get("keys")
    if isinstance(keys, str):
        keys = [keys]
    if not keys or len(keys) > 20 or any(k not in KEYS for k in keys):
        raise ToolError("keys ต้องเป็นรายการปุ่มที่รู้จัก (สูงสุด 20 ปุ่ม)")
    return await _run("remote", {"keys": keys})


async def t_power_off(a):
    return await _run("power_off", {})


TOOLS = {
    "tv_status": (t_status, "เช็กว่าทีวีที่บ้านออนไลน์ไหม เปิดจออยู่ไหม เสียงเท่าไหร่", {}, []),
    "tv_open_url": (t_open_url, "เปิดลิงก์เว็บ (http/https) บนทีวีด้วย browser ในทีวี",
                    {"url": {"type": "string", "description": "ลิงก์ http:// หรือ https://"}}, ["url"]),
    "tv_open_app": (t_open_app, "เปิดแอปบนทีวี",
                    {"app": {"type": "string", "enum": APPS,
                             "description": "livetv = ดูทีวีช่องปกติ, browser = Vewd, pinato = PinaTo Board"}}, ["app"]),
    "tv_volume": (t_volume, "ปรับเสียงทีวี",
                  {"action": {"type": "string", "enum": ["up", "down", "mute", "unmute", "set", "get"]},
                   "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "ใช้กับ action=set"}},
                  ["action"]),
    "tv_remote": (t_remote, "กดปุ่มรีโมททีวีตามลำดับ (ต้องตั้ง Sony IP Control PSK บนทีวีก่อน)",
                  {"keys": {"type": "array", "items": {"type": "string", "enum": KEYS}, "maxItems": 20}}, ["keys"]),
    "tv_power_off": (t_power_off, "ปิดทีวี (เข้า standby) — เปิดกลับจากที่นี่ไม่ได้", {}, []),
}


def _tool_list():
    return [{
        "name": n,
        "description": d,
        "inputSchema": {"type": "object", "properties": props, "required": req},
    } for n, (_, d, props, req) in TOOLS.items()]


async def _handle_rpc(msg: dict):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tv-remote", "version": "0.1.0"},
            "instructions": "สั่งทีวี Sony BRAVIA ที่บ้านของผู้ใช้ ห้ามเปิดเว็บสตรีมที่ละเมิดลิขสิทธิ์",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": _tool_list()}
    elif method == "tools/call":
        p = msg.get("params") or {}
        t = TOOLS.get(p.get("name"))
        if not t:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}
        log("tool:", p.get("name"), json.dumps(p.get("arguments") or {}, ensure_ascii=False)[:200])
        try:
            text = await t[0](p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}]}
        except ToolError as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


async def mcp(request: Request):
    h = request.headers.get("authorization", "")
    if not (h.startswith("Bearer ") and verify(h[7:], "access")):
        return Response(status_code=401, headers={
            "WWW-Authenticate": f'Bearer resource_metadata="{PUBLIC_URL}/.well-known/oauth-protected-resource"'
        })
    if request.method != "POST":
        return Response(status_code=405, headers={"Allow": "POST"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, 400)
    if isinstance(body, list):
        out = [r for r in [await _handle_rpc(m) for m in body] if r]
    else:
        out = await _handle_rpc(body)
    if not out:
        return Response(status_code=202)
    return JSONResponse(out)


def health(_: Request):
    return JSONResponse({"ok": True, "tv_online": tv.online()})


app = Starlette(routes=[
    Route("/", health),
    Route("/.well-known/oauth-authorization-server", auth_server_meta),
    Route("/.well-known/oauth-protected-resource", resource_meta),
    Route("/.well-known/oauth-protected-resource/mcp", resource_meta),
    Route("/register", register, methods=["POST"]),
    Route("/authorize", authorize, methods=["GET", "POST"]),
    Route("/token", token, methods=["POST"]),
    Route("/mcp", mcp, methods=["GET", "POST", "DELETE"]),
    Route("/tv/poll", tv_poll),
    Route("/tv/result", tv_result, methods=["POST"]),
    Route("/tv/pair/start", pair_start, methods=["POST"]),
    Route("/tv/pair/poll", pair_poll, methods=["POST"]),
    Route("/pair", pair_page, methods=["GET", "POST"]),
])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")

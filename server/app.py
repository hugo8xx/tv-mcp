"""tv-mcp server: MCP (Streamable HTTP, JSON response) + OAuth + long-poll relay ไปหาแอปบนทีวี

Claude (มือถือ/เว็บ/Desktop) ──OAuth──> /mcp ──queue──> /tv/poll <── แอป com.pinato.remote บนทีวี

2 โหมด (env MODE):
  selfhost (default)  เจ้าของคนเดียว ล็อกอินด้วย OWNER_PASSWORD ไม่มีฐานข้อมูล
  cloud               หลายบ้าน ล็อกอินด้วย Google, เก็บผู้ใช้/ทีวีใน SQLite ($DATA_DIR)

token ทุกตัว stateless ด้วย HMAC (SECRET_KEY) — redeploy แล้วยังใช้ได้ เปลี่ยน SECRET_KEY = ถอนทุกตัว

env:
  SECRET_KEY            สุ่มยาว (ต้องใส่)
  OWNER_PASSWORD        selfhost: รหัสเจ้าของ
  GOOGLE_CLIENT_ID      cloud: OAuth client (Web) ของ Google
  GOOGLE_CLIENT_SECRET  cloud
  DATA_DIR              cloud: ที่เก็บ SQLite (mount volume ไว้) default ./data
  TV_DEVICE_TOKEN       selfhost (ไม่ใส่ก็ได้): token คงที่สำหรับทีวีที่จับคู่ผ่าน adb แบบเก่า
  DEVICE_EPOCH          (ไม่ใส่ก็ได้) เพิ่มเลขนี้ = ถอนทีวีที่จับคู่ด้วยรหัสทุกเครื่อง
  PUBLIC_URL            (ไม่ใส่ก็ได้) default https://$RAILWAY_PUBLIC_DOMAIN
  BLOCKED_DOMAINS       โดเมนเพิ่มเติมที่ห้ามเปิด คั่นด้วย comma
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.request
import uuid
from html import escape
from urllib.parse import quote, urlencode, urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from pages import SOURCE_URL, hidden, msg, page
from tokens import b64, sign, verify

MODE = os.environ.get("MODE", "selfhost")
CLOUD = MODE == "cloud"
OWNER = "owner"  # user id ของโหมด selfhost

OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
TV_DEVICE_TOKEN = os.environ.get("TV_DEVICE_TOKEN", "") if not CLOUD else ""
DEVICE_EPOCH = int(os.environ.get("DEVICE_EPOCH", "1"))
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or f"https://{os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'localhost:8000')}").rstrip("/")

if CLOUD:
    assert GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, "cloud mode ต้องตั้ง GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET"
    from store import Store
    store = Store(os.path.join(os.environ.get("DATA_DIR", "data"), "tv-mcp.sqlite"))
else:
    assert OWNER_PASSWORD, "selfhost mode ต้องตั้ง OWNER_PASSWORD"
    store = None

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
SESSION_TTL = 7 * 86400
SESSION_COOKIE = "tvmcp_s"

APPS = ["netflix", "youtube", "prime", "appletv", "livetv", "browser", "pinato", "tvhub"]
KEYS = [
    "home", "back", "up", "down", "left", "right", "ok", "play", "pause", "stop",
    "rewind", "forward", "next", "prev", "volume_up", "volume_down", "mute",
    "channel_up", "channel_down", "input", "subtitle", "audio", "guide", "info",
]


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def tag(uid: str) -> str:
    """ชื่อย่อผู้ใช้ใน log — ไม่ลงอีเมลหรือ Google id"""
    return uid if uid == OWNER else "u" + hashlib.sha256(uid.encode()).hexdigest()[:8]


class RateLimit:
    def __init__(self, n: int, per: float):
        self.n, self.per, self.hits = n, per, {}

    def ok(self, key: str) -> bool:
        now = time.time()
        h = [t for t in self.hits.get(key, []) if now - t < self.per]
        if len(h) >= self.n:
            self.hits[key] = h
            return False
        h.append(now)
        self.hits[key] = h
        return True


# ---------- ผู้ใช้ / session ----------

def user_ok(uid: str, since) -> bool:
    """token ทุกตัวพก uid + since (เวลาที่สร้างบัญชี) — ลบบัญชีแล้ว token เก่าใช้ไม่ได้แม้สมัครใหม่"""
    if not CLOUD:
        return uid == OWNER
    u = store.user(uid)
    return bool(u and u["created"] == since)


def current_user(request: Request) -> dict | None:
    s = verify(request.cookies.get(SESSION_COOKIE, ""), "session")
    if not s or not user_ok(s["u"], s.get("s")):
        return None
    return {"id": s["u"], "since": s.get("s"), "email": s.get("e", "")}


def csrf(user: dict) -> str:
    return sign("csrf", {"u": user["id"]}, 3600)


def csrf_ok(user: dict, form) -> bool:
    c = verify(str(form.get("csrf", "")), "csrf")
    return bool(c and c["u"] == user["id"])


def need_login(request: Request):
    nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(nxt)}", 302)


def _safe_next(n: str) -> str:
    return n if n.startswith("/") and not n.startswith("//") else "/account"


def _login_response(nxt: str, uid: str, since, email: str):
    r = RedirectResponse(_safe_next(nxt), 302)
    r.set_cookie(SESSION_COOKIE, sign("session", {"u": uid, "s": since, "e": email}, SESSION_TTL),
                 max_age=SESSION_TTL, httponly=True, secure=PUBLIC_URL.startswith("https"), samesite="lax")
    return r


_fails: list[float] = []


def _locked() -> bool:
    now = time.time()
    _fails[:] = [t for t in _fails if now - t < 900]
    return len(_fails) >= 5


async def login(request: Request):
    nxt = _safe_next(request.query_params.get("next", "/account"))
    if CLOUD:
        q = {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": f"{PUBLIC_URL}/auth/google/callback",
            "response_type": "code",
            "scope": "openid email",
            "state": sign("gstate", {"n": nxt}, 600),
            "prompt": "select_account",
        }
        return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(q), 302)

    def form(err=None):
        return page("เข้าสู่ระบบ", f"""<h1>📺 TV MCP</h1><p>ใส่รหัสเจ้าของ server นี้</p>{msg(err)}
<form method="post">{hidden({"next": nxt})}
<input type="password" name="password" placeholder="รหัสเจ้าของ" autofocus required>
<button class="primary">เข้าสู่ระบบ</button></form>""")

    if request.method == "GET":
        return form()
    f = await request.form()
    nxt = _safe_next(str(f.get("next", "/account")))
    if _locked():
        return form("ใส่รหัสผิดหลายครั้ง รอ 15 นาที")
    if not hmac.compare_digest(str(f.get("password", "")).encode(), OWNER_PASSWORD.encode()):
        _fails.append(time.time())
        log("login: wrong password")
        await asyncio.sleep(1)
        return form("รหัสไม่ถูกต้อง")
    return _login_response(nxt, OWNER, None, "")


def _google_exchange(code: str) -> dict:
    data = urlencode({
        "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": f"{PUBLIC_URL}/auth/google/callback", "grant_type": "authorization_code",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data), timeout=15) as r:
        return json.load(r)


async def google_callback(request: Request):
    if not CLOUD:
        return Response(status_code=404)
    st = verify(request.query_params.get("state", ""), "gstate")
    code = request.query_params.get("code", "")
    if not st or not code:
        return page("เข้าสู่ระบบไม่สำเร็จ", f'<h1>เข้าสู่ระบบไม่สำเร็จ</h1><a class="btn" href="/login">ลองใหม่</a>', 400)
    try:
        tok = await asyncio.to_thread(_google_exchange, code)
        # id_token มาจาก token endpoint ของ Google ตรงๆ ผ่าน TLS ด้วย client secret จึงอ่าน payload ได้เลย
        claims = json.loads(base64url_decode(tok["id_token"].split(".")[1]))
    except Exception as e:
        log("google: exchange failed", type(e).__name__)
        return page("เข้าสู่ระบบไม่สำเร็จ", '<h1>เข้าสู่ระบบไม่สำเร็จ</h1><a class="btn" href="/login">ลองใหม่</a>', 502)
    if (claims.get("aud") != GOOGLE_CLIENT_ID or claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com")
            or claims.get("exp", 0) < time.time() or not claims.get("email_verified")):
        return page("เข้าสู่ระบบไม่สำเร็จ", "<h1>บัญชี Google นี้ยังไม่ยืนยันอีเมล</h1>", 403)
    uid = "g_" + claims["sub"]
    since = store.upsert_user(uid, claims["email"])
    log("login:", tag(uid))
    return _login_response(st["n"], uid, since, claims["email"])


def base64url_decode(s: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def logout(_: Request):
    r = RedirectResponse("/", 302)
    r.delete_cookie(SESSION_COOKIE)
    return r


# ---------- OAuth (ให้ Claude) ----------

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
    client_id = sign("client", {"r": uris, "n": str(body.get("client_name", ""))[:60]}, None)
    log("register:", body.get("client_name"), uris)
    return JSONResponse({
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }, 201)


_used_codes: dict[str, float] = {}

AUTH_KEYS = ["client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "response_type", "scope", "resource"]


def _check_auth_request(p: dict):
    client = verify(p["client_id"], "client")
    if not client:
        return None, "client_id ไม่ถูกต้อง"
    if p["redirect_uri"] not in client["r"]:
        return None, "redirect_uri ไม่ตรงกับที่ลงทะเบียน"
    if p["response_type"] != "code" or p["code_challenge_method"] != "S256" or not p["code_challenge"]:
        return None, "ต้องใช้ response_type=code + PKCE S256"
    return client, None


async def authorize(request: Request):
    src = request.query_params if request.method == "GET" else await request.form()
    p = {k: str(src.get(k, "")) for k in AUTH_KEYS}
    client, err = _check_auth_request(p)
    if err:
        return page("ผิดพลาด", f"<h1>คำขอไม่ถูกต้อง</h1>{msg(err)}", 400)
    user = current_user(request)
    if not user:
        return need_login(request)
    who = escape(user["email"]) if CLOUD else "เจ้าของ server นี้"

    if request.method == "GET":
        return page("อนุญาต", f"""<h1>📺 TV MCP</h1>
<p><b>{escape(client.get("n") or "Claude")}</b> ขอสิทธิ์สั่งทีวีของ <b>{who}</b></p>
<p class="mut">เปิดลิงก์/แอป ปรับเสียง กดปุ่มรีโมท และปิดทีวี — ส่งกลับไปที่ {escape(urlsplit(p["redirect_uri"]).netloc)}</p>
<form method="post">{hidden({**p, "csrf": csrf(user)})}<button class="primary">อนุญาต</button></form>
<a class="btn" href="/account">ยกเลิก</a>""")

    if not csrf_ok(user, src):
        return page("ผิดพลาด", "<h1>หน้านี้หมดอายุ</h1><p>กลับไปเชื่อมใหม่จาก Claude</p>", 400)
    code = sign("code", {
        "c": hashlib.sha256(p["client_id"].encode()).hexdigest()[:32],
        "r": p["redirect_uri"], "ch": p["code_challenge"], "j": secrets.token_hex(8),
        "u": user["id"], "s": user["since"],
    }, CODE_TTL)
    log("authorize:", client.get("n"), tag(user["id"]))
    q = {"code": code}
    if p["state"]:
        q["state"] = p["state"]
    return RedirectResponse(f"{p['redirect_uri']}{'&' if '?' in p['redirect_uri'] else '?'}{urlencode(q)}", 302)


def _issue(client_id: str, uid: str, since):
    cid = hashlib.sha256(client_id.encode()).hexdigest()[:32]
    return JSONResponse({
        "access_token": sign("access", {"c": cid, "u": uid, "s": since}, ACCESS_TTL),
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL,
        "refresh_token": sign("refresh", {"c": cid, "u": uid, "s": since, "j": secrets.token_hex(8)}, REFRESH_TTL),
        "scope": "tv",
    }, headers={"Cache-Control": "no-store"})


async def token(request: Request):
    f = await request.form()
    gt = f.get("grant_type")
    client_id = str(f.get("client_id", ""))
    if not verify(client_id, "client"):
        return JSONResponse({"error": "invalid_client"}, 401)
    cid = hashlib.sha256(client_id.encode()).hexdigest()[:32]

    if gt == "authorization_code":
        c = verify(str(f.get("code", "")), "code")
        if not c or c["c"] != cid or c["r"] != f.get("redirect_uri"):
            return JSONResponse({"error": "invalid_grant"}, 400)
        now = time.time()
        for j, t in list(_used_codes.items()):
            if now - t > CODE_TTL:
                del _used_codes[j]
        if c["j"] in _used_codes:
            return JSONResponse({"error": "invalid_grant", "error_description": "code used"}, 400)
        if b64(hashlib.sha256(str(f.get("code_verifier", "")).encode()).digest()) != c["ch"]:
            return JSONResponse({"error": "invalid_grant", "error_description": "pkce"}, 400)
        _used_codes[c["j"]] = now
        uid, since = c.get("u", OWNER), c.get("s")
    elif gt == "refresh_token":
        r = verify(str(f.get("refresh_token", "")), "refresh")
        if not r or r["c"] != cid:
            return JSONResponse({"error": "invalid_grant"}, 400)
        uid, since = r.get("u", OWNER), r.get("s")
    else:
        return JSONResponse({"error": "unsupported_grant_type"}, 400)
    if not user_ok(uid, since):
        return JSONResponse({"error": "invalid_grant", "error_description": "account gone"}, 400)
    return _issue(client_id, uid, since)


# ---------- relay ไปทีวี (1 คิวต่อผู้ใช้) ----------

class ToolError(Exception):
    pass


class TV:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.pending: dict[str, asyncio.Future] = {}
        self.last_poll = 0.0

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


tvs: dict[str, TV] = {}


def tv_of(uid: str) -> TV:
    return tvs.setdefault(uid, TV())


def _tv_auth(request: Request) -> str | None:
    """คืน user id เจ้าของทีวี"""
    h = request.headers.get("authorization", "")
    if not h.startswith("Bearer "):
        return None
    if TV_DEVICE_TOKEN and hmac.compare_digest(h.encode(), f"Bearer {TV_DEVICE_TOKEN}".encode()):
        return OWNER
    d = verify(h[7:], "device")
    if not d or d.get("e") != DEVICE_EPOCH:
        return None
    uid = d.get("u", OWNER)
    if not user_ok(uid, d.get("s")):
        return None
    if CLOUD and not store.device_ok(d["d"], uid):
        return None
    return uid


async def tv_poll(request: Request):
    uid = _tv_auth(request)
    if not uid:
        return Response(status_code=401)
    tv = tv_of(uid)
    tv.last_poll = time.time()
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
    uid = _tv_auth(request)
    if not uid:
        return Response(status_code=401)
    tv = tv_of(uid)
    tv.last_poll = time.time()
    body = await request.json()
    fut = tv.pending.get(body.get("id"))
    if fut and not fut.done():
        fut.set_result(body)
    return Response(status_code=204)


# ---------- จับคู่ทีวีด้วยรหัสบนจอ (ไม่ต้องใช้ adb) ----------
# ทีวี: POST /tv/pair/start → โชว์รหัส → POST /tv/pair/poll จนได้ device_token
# คน:   เปิด /pair บนมือถือ (ล็อกอิน) → ใส่รหัส

PAIR_TTL = 600
_pairs: dict[str, dict] = {}  # pair_id → {code, exp, token}
_pair_ip = RateLimit(20, 600)


def _clean_pairs():
    now = time.time()
    for k, v in list(_pairs.items()):
        if v["exp"] < now:
            del _pairs[k]


async def pair_start(request: Request):
    _clean_pairs()
    ip = request.client.host if request.client else "?"
    if len(_pairs) >= 500 or not _pair_ip.ok(ip):
        return JSONResponse({"error": "busy"}, 429)
    taken = {v["code"] for v in _pairs.values()}
    code = f"{secrets.randbelow(10**6):06d}"
    while code in taken:
        code = f"{secrets.randbelow(10**6):06d}"
    pid = secrets.token_urlsafe(24)
    _pairs[pid] = {"code": code, "exp": time.time() + PAIR_TTL, "token": None}
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


_pair_try = RateLimit(10, 600)


async def pair_page(request: Request):
    user = current_user(request)
    if not user:
        return need_login(request)

    def form(m=None, cls="err"):
        return page("จับคู่ทีวี", f"""<h1>📺 จับคู่ทีวี</h1>
<p class="mut">เปิดแอป TV Remote บนทีวี แล้วใส่รหัส 6 หลักที่ขึ้นบนจอ</p>{msg(m, cls)}
<form method="post">{hidden({"csrf": csrf(user)})}
<input name="code" inputmode="numeric" placeholder="รหัส 6 หลักบนจอทีวี" autocomplete="off" required autofocus>
<button class="primary">จับคู่</button></form>""")

    if request.method == "GET":
        return form()
    f = await request.form()
    if not csrf_ok(user, f):
        return form("หน้านี้หมดอายุ ลองใหม่")
    if not _pair_try.ok(user["id"]):
        return form("ลองหลายครั้งเกินไป รอ 10 นาที")
    code = re.sub(r"\D", "", str(f.get("code", "")))
    _clean_pairs()
    p = next((v for v in _pairs.values() if v["code"] == code and not v["token"]), None)
    if not p:
        return form("ไม่พบรหัสนี้ หรือหมดอายุแล้ว — ดูรหัสใหม่บนจอทีวี")
    did = uuid.uuid4().hex[:12]
    if CLOUD:
        store.add_device(did, user["id"])
        tvs.pop(user["id"], None)  # ล้างคิวของเครื่องเก่า
    p["token"] = sign("device", {"d": did, "e": DEVICE_EPOCH, "u": user["id"], "s": user["since"]}, None)
    log("pair: approved", tag(user["id"]))
    return form("จับคู่สำเร็จ ✅ ทีวีจะออนไลน์ในไม่กี่วินาที", "ok")


# ---------- MCP tools ----------

def _blocked(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


async def _run(uid, cmd, args):
    r = await tv_of(uid).call(cmd, args)
    if not r.get("ok"):
        raise ToolError(r.get("message") or "ทีวีทำไม่สำเร็จ")
    return r.get("message") or "ok"


async def t_status(uid, a):
    tv = tv_of(uid)
    if not tv.online():
        ago = int(time.time() - tv.last_poll) if tv.last_poll else None
        return "ทีวีออฟไลน์" + (f" (เห็นล่าสุด {ago} วินาทีก่อน)" if ago else "")
    return "ทีวีออนไลน์ · " + await _run(uid, "status", {})


async def t_open_url(uid, a):
    url = str(a.get("url", "")).strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolError("รับเฉพาะลิงก์ http:// หรือ https://")
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise ToolError("ลิงก์มีช่องว่างหรืออักขระควบคุม")
    if _blocked(parts.hostname):
        log("open_url blocked:", parts.hostname)
        raise ToolError("โดเมนนี้ถูกบล็อก (เว็บสตรีมที่ไม่มีลิขสิทธิ์) — ไม่เปิดให้")
    return await _run(uid, "open_url", {"url": url})


async def t_open_app(uid, a):
    app_ = a.get("app")
    if app_ not in APPS:
        raise ToolError(f"ไม่รู้จักแอป {app_}")
    return await _run(uid, "open_app", {"app": app_})


async def t_volume(uid, a):
    action = a.get("action")
    if action not in ("up", "down", "mute", "unmute", "set", "get"):
        raise ToolError("action ไม่ถูกต้อง")
    args = {"action": action}
    if action == "set":
        level = a.get("level")
        if not isinstance(level, int) or not 0 <= level <= 100:
            raise ToolError("level ต้องเป็นตัวเลข 0–100")
        args["level"] = level
    return await _run(uid, "volume", args)


async def t_remote(uid, a):
    keys = a.get("keys")
    if isinstance(keys, str):
        keys = [keys]
    if not keys or len(keys) > 20 or any(k not in KEYS for k in keys):
        raise ToolError("keys ต้องเป็นรายการปุ่มที่รู้จัก (สูงสุด 20 ปุ่ม)")
    return await _run(uid, "remote", {"keys": keys})


async def t_power_off(uid, a):
    return await _run(uid, "power_off", {})


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

_tool_rate = RateLimit(60, 60)


def _tool_list():
    return [{
        "name": n,
        "description": d,
        "inputSchema": {"type": "object", "properties": props, "required": req},
    } for n, (_, d, props, req) in TOOLS.items()]


def _log_args(name: str, a: dict) -> str:
    if name == "tv_open_url":  # ไม่ลง path/query ของลิงก์
        return urlsplit(str(a.get("url", ""))).hostname or "?"
    return json.dumps(a, ensure_ascii=False)[:120]


async def _handle_rpc(uid: str, m: dict):
    method, mid = m.get("method"), m.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": (m.get("params") or {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tv-mcp", "version": "0.3.0"},
            "instructions": "สั่งทีวี Android TV ที่บ้านของผู้ใช้ ห้ามเปิดเว็บสตรีมที่ละเมิดลิขสิทธิ์",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": _tool_list()}
    elif method == "tools/call":
        p = m.get("params") or {}
        t = TOOLS.get(p.get("name"))
        if not t:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}
        args = p.get("arguments") or {}
        log("tool:", tag(uid), p.get("name"), _log_args(p.get("name"), args))
        try:
            if not _tool_rate.ok(uid):
                raise ToolError("สั่งถี่เกินไป รอสักครู่")
            text = await t[0](uid, args)
            result = {"content": [{"type": "text", "text": text}]}
        except ToolError as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


async def mcp(request: Request):
    h = request.headers.get("authorization", "")
    a = verify(h[7:], "access") if h.startswith("Bearer ") else None
    uid = a.get("u", OWNER) if a else None
    if not a or not user_ok(uid, a.get("s")):
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
        out = [r for r in [await _handle_rpc(uid, x) for x in body] if r]
    else:
        out = await _handle_rpc(uid, body)
    if not out:
        return Response(status_code=202)
    return JSONResponse(out)


# ---------- หน้าเว็บ ----------

def _ago(ts: float) -> str:
    if not ts:
        return "ยังไม่เคย"
    s = int(time.time() - ts)
    return f"{s} วินาทีก่อน" if s < 120 else f"{s // 60} นาทีก่อน" if s < 7200 else time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


async def account(request: Request):
    user = current_user(request)
    if not user:
        return need_login(request)
    uid, tok = user["id"], csrf(user)
    note = None

    if request.method == "POST":
        f = await request.form()
        if not csrf_ok(user, f):
            note = "หน้านี้หมดอายุ ลองใหม่"
        elif CLOUD and f.get("action") == "revoke":
            store.revoke_device(str(f.get("device", "")), uid)
            tvs.pop(uid, None)
            note = "ถอนทีวีแล้ว"
        elif CLOUD and f.get("action") == "delete" and f.get("confirm") == "DELETE":
            store.delete_user(uid)
            tvs.pop(uid, None)
            log("account deleted:", tag(uid))
            r = page("ลบบัญชีแล้ว", '<h1>ลบบัญชีแล้ว</h1><p>ข้อมูลของคุณถูกลบหมดแล้ว และ Claude/ทีวีที่เชื่อมไว้จะใช้ไม่ได้อีก</p><a class="btn" href="/">กลับหน้าแรก</a>')
            r.delete_cookie(SESSION_COOKIE)
            return r

    tv = tv_of(uid)
    status = f'<span class="ok">ออนไลน์</span>' if tv.online() else f'<span class="mut">ออฟไลน์ · เห็นล่าสุด {_ago(tv.last_poll)}</span>'
    devices = ""
    if CLOUD:
        rows = store.devices(uid)
        devices = "".join(f"""<div class="card">ทีวี <code>{escape(d["id"][:6])}</code> · จับคู่ {time.strftime("%Y-%m-%d", time.localtime(d["created"]))}
<form method="post">{hidden({"csrf": tok, "action": "revoke", "device": d["id"]})}<button class="link">ถอนทีวีเครื่องนี้</button></form></div>""" for d in rows) or '<p class="mut">ยังไม่ได้จับคู่ทีวี</p>'
    danger = f"""<h2>ลบบัญชี</h2><p class="mut">ลบอีเมลและรายการทีวีทั้งหมดทันที พิมพ์ DELETE เพื่อยืนยัน</p>
<form method="post">{hidden({"csrf": tok, "action": "delete"})}<input name="confirm" placeholder="DELETE" autocomplete="off">
<button>ลบบัญชีของฉัน</button></form>""" if CLOUD else ""
    return page("บัญชี", f"""<h1>📺 บัญชีของคุณ</h1>
<p>{escape(user["email"]) if CLOUD else "เจ้าของ server นี้"} · <a href="/logout">ออกจากระบบ</a></p>{msg(note, "ok")}
<h2>ทีวี</h2><p>{status}</p>{devices}
<a class="btn primary" href="/pair">จับคู่ทีวี</a>
<h2>เชื่อมกับ Claude</h2>
<p>claude.ai → Settings → Connectors → Add custom connector แล้วใส่ URL นี้:</p><p><code>{PUBLIC_URL}/mcp</code></p>
{danger}""")


def home(request: Request):
    if not CLOUD:  # self-host: คงพฤติกรรมเดิมไว้ให้ health check
        return health(request)
    return page("หน้าแรก", f"""<h1>📺 TV MCP</h1>
<p>สั่งทีวี Android TV ที่บ้านจาก Claude — บนมือถือ เว็บ หรือ Desktop</p>
<p class="mut">"เปิด YouTube บนทีวี" · "เสียง 20" · "เปิดลิงก์นี้บนทีวี" · "ปิดทีวี"</p>
<h2>เริ่มใช้ 3 ขั้น</h2>
<div class="card">1. ติดตั้งแอป <b>TV Remote</b> บนทีวี<br><code>github.com/hugo8xx/tv-mcp/releases/latest/download/tv-remote.apk</code></div>
<div class="card">2. เปิดแอปบนทีวี แล้วจับคู่ด้วยรหัส 6 หลักที่ <a href="/pair">{escape(urlsplit(PUBLIC_URL).netloc)}/pair</a></div>
<div class="card">3. ใน claude.ai เพิ่ม custom connector: <code>{PUBLIC_URL}/mcp</code></div>
<a class="btn primary" href="/account">เข้าสู่ระบบด้วย Google</a>
<p class="mut">ทำงานเต็มที่กับ Sony BRAVIA · ยี่ห้ออื่นเปิดแอปและปรับเสียงได้ · ช่วงทดลอง (beta) ใช้ฟรี</p>
<p class="mut">ซอฟต์แวร์โอเพนซอร์ส AGPL-3.0 — <a href="{SOURCE_URL}">ดูซอร์สโค้ด</a> หรือติดตั้งบน server ของคุณเอง</p>""")


def privacy(_: Request):
    # TODO(PinaTo): ให้ฝ่ายกฎหมายตรวจข้อความนี้ และใส่อีเมลติดต่อจริงก่อนเปิดให้คนทั่วไปใช้
    contact = os.environ.get("CONTACT_EMAIL", "")
    contact_html = f'<a href="mailto:{escape(contact)}">{escape(contact)}</a>' if contact else "(ยังไม่ได้ระบุ)"
    return page("ความเป็นส่วนตัว", f"""<h1>นโยบายความเป็นส่วนตัว</h1>
<p class="mut">ฉบับร่าง — ใช้กับบริการ TV MCP ที่ {escape(urlsplit(PUBLIC_URL).netloc)}</p>
<h2>ผู้ให้บริการ</h2><p>PinaTo Co., Ltd. · ติดต่อ: {contact_html}</p>
<h2>ข้อมูลที่เก็บ</h2>
<p>• บัญชี Google: รหัสผู้ใช้ (sub) และอีเมล — ใช้ระบุตัวตนเท่านั้น<br>
• ทีวีที่จับคู่: รหัสเครื่องแบบสุ่ม วันที่จับคู่ และเวลาที่ออนไลน์ล่าสุด</p>
<h2>ข้อมูลที่ไม่เก็บ</h2>
<p>ไม่เก็บประวัติคำสั่งหรือลิงก์ที่เปิด ไม่เก็บรหัสผ่านใดๆ ไม่ขายหรือแบ่งปันข้อมูลให้ใคร
log ของ server บันทึกชื่อคำสั่ง โดเมนของลิงก์ (ไม่รวม path) และรหัสผู้ใช้แบบย่อ เพื่อแก้ปัญหาระบบ</p>
<h2>ผู้ให้บริการภายนอก</h2><p>Google (เข้าสู่ระบบ) และ Railway (โฮสต์ server)</p>
<h2>ลบข้อมูล</h2><p>ลบบัญชีได้เองทันทีที่ <a href="/account">หน้าบัญชี</a> ข้อมูลทั้งหมดของคุณจะถูกลบจากฐานข้อมูล</p>""")


def health(_: Request):
    body = {"ok": True, "mode": MODE}
    if not CLOUD:
        body["tv_online"] = tv_of(OWNER).online()
    return JSONResponse(body)


SECURITY_HEADERS = {
    # หน้า "อนุญาต" ต้องไม่ถูกฝังใน iframe (clickjacking) — แนวเดียวกับ pact-auth
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://claude.ai https://claude.com http://localhost:* http://127.0.0.1:*; frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_(m):
            if m["type"] == "http.response.start":
                m["headers"] = list(m.get("headers", [])) + [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
            await send(m)

        await self.app(scope, receive, send_)


app = Starlette(routes=[
    Route("/", home),
    Route("/health", health),
    Route("/.well-known/oauth-authorization-server", auth_server_meta),
    Route("/.well-known/oauth-protected-resource", resource_meta),
    Route("/.well-known/oauth-protected-resource/mcp", resource_meta),
    Route("/register", register, methods=["POST"]),
    Route("/authorize", authorize, methods=["GET", "POST"]),
    Route("/token", token, methods=["POST"]),
    Route("/mcp", mcp, methods=["GET", "POST", "DELETE"]),
    Route("/login", login, methods=["GET", "POST"]),
    Route("/auth/google/callback", google_callback),
    Route("/logout", logout),
    Route("/account", account, methods=["GET", "POST"]),
    Route("/privacy", privacy),
    Route("/pair", pair_page, methods=["GET", "POST"]),
    Route("/tv/poll", tv_poll),
    Route("/tv/result", tv_result, methods=["POST"]),
    Route("/tv/pair/start", pair_start, methods=["POST"]),
    Route("/tv/pair/poll", pair_poll, methods=["POST"]),
])


app = SecurityHeaders(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")

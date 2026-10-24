#!/usr/bin/env python3
"""tv-mcp: MCP server (stdio) สำหรับสั่งทีวี Sony BRAVIA ที่บ้าน

ไม่มี dependency นอกจาก Python stdlib + adb
เพิ่ม tool ใหม่: เขียนฟังก์ชัน แล้วใส่ใน TOOLS ด้านล่าง

ตั้งค่าผ่าน env:
  TV_ADB_SERIAL  (ต้องใส่) เช่น 192.168.1.50:5555
  ADB            default /opt/homebrew/bin/adb
"""
import base64
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

SERIAL = os.environ.get("TV_ADB_SERIAL", "")
ADB = os.environ.get("ADB", "/opt/homebrew/bin/adb")
VEWD = "com.vewd.core.integration.dia/com.vewd.core.browser.BrowserActivity"


class ToolError(Exception):
    pass


def adb(*args, timeout=20, binary=False):
    p = subprocess.run([ADB, "-s", SERIAL, *args], capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise ToolError((p.stderr or p.stdout).decode(errors="replace").strip() or "adb failed")
    return p.stdout if binary else p.stdout.decode(errors="replace")


def ensure_connected():
    if not SERIAL:
        raise ToolError("ยังไม่ได้ตั้ง TV_ADB_SERIAL (เช่น 192.168.1.50:5555)")
    subprocess.run([ADB, "connect", SERIAL], capture_output=True, timeout=10)
    state = subprocess.run([ADB, "-s", SERIAL, "get-state"], capture_output=True, timeout=10)
    if state.stdout.decode().strip() != "device":
        raise ToolError(
            f"ต่อ ADB ไปที่ {SERIAL} ไม่ได้ (ทีวีปิด/standby หรือปิด ADB debugging อยู่)"
        )


def resumed_activity():
    out = adb("shell", "dumpsys", "activity", "activities")
    for line in out.splitlines():
        if "mResumedActivity" in line or "topResumedActivity" in line:
            # ... ActivityRecord{abc u0 pkg/.Activity t123}
            for tok in line.split():
                if "/" in tok:
                    return tok
    return "unknown"


def tv_open_url(url: str):
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ToolError("รับเฉพาะลิงก์ http:// หรือ https://")
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise ToolError("ลิงก์มีช่องว่างหรืออักขระควบคุม")
    ensure_connected()
    # adb shell ต่อ args เป็นสตริงเดียวแล้วส่งให้ sh บนทีวี → ต้อง quote URL เอง
    cmd = f"am start -a android.intent.action.VIEW -d {shlex.quote(url)} -n {VEWD}"
    out = adb("shell", cmd)
    if "Error" in out:
        raise ToolError(out.strip())
    time.sleep(3)
    top = resumed_activity()
    note = ""
    if "vewd" not in top:
        note = " (หน้าจอบนสุดไม่ใช่ Vewd — อาจติดหน้า Terms of Service หรือเปิดไม่สำเร็จ ลองใช้ tv_screenshot ดู)"
    return [{"type": "text", "text": f"เปิด {url} บนทีวีแล้ว · หน้าจอปัจจุบัน: {top}{note}"}]


def tv_screenshot():
    ensure_connected()
    png = adb("exec-out", "screencap", "-p", binary=True, timeout=30)
    if not png.startswith(b"\x89PNG"):
        raise ToolError("screencap ไม่ได้ภาพ PNG")
    # ย่อเป็น JPEG กว้าง 960 ให้ส่งเบาลง (sips มีในทุก macOS)
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "s.png"), os.path.join(d, "s.jpg")
        with open(src, "wb") as f:
            f.write(png)
        r = subprocess.run(
            ["sips", "-Z", "960", "-s", "format", "jpeg", src, "--out", dst],
            capture_output=True, timeout=20,
        )
        if r.returncode == 0:
            data, mime = open(dst, "rb").read(), "image/jpeg"
        else:
            data, mime = png, "image/png"
    return [
        {"type": "image", "data": base64.b64encode(data).decode(), "mimeType": mime},
        {"type": "text", "text": "หมายเหตุ: แอปที่มี DRM (Netflix/Prime/Apple TV) จะออกเป็นจอดำ"},
    ]


TOOLS = {
    "tv_open_url": {
        "fn": lambda a: tv_open_url(a.get("url")),
        "description": "เปิดลิงก์เว็บ (http/https) บนทีวี Sony ที่บ้าน ด้วย Web Browser by Vewd ที่ติดมากับทีวี",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "ลิงก์ http:// หรือ https://"}},
            "required": ["url"],
        },
    },
    "tv_screenshot": {
        "fn": lambda a: tv_screenshot(),
        "description": "ถ่ายภาพหน้าจอทีวีตอนนี้ (ใช้เช็กว่าเปิดหน้าไหนอยู่)",
        "inputSchema": {"type": "object", "properties": {}},
    },
}


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification
        return None
    if method == "initialize":
        result = {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tv", "version": "0.1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [
            {"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
            for n, t in TOOLS.items()
        ]}
    elif method == "tools/call":
        p = msg.get("params", {})
        tool = TOOLS.get(p.get("name"))
        if not tool:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}
        try:
            result = {"content": tool["fn"](p.get("arguments") or {})}
        except (ToolError, subprocess.TimeoutExpired) as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except Exception as e:  # ไม่ให้ server ล้มเพราะข้อความเดียว
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

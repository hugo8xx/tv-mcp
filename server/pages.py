"""หน้าเว็บเล็กๆ ของ server (มือถือเป็นหลัก) — ข้อความที่มาจากผู้ใช้ต้อง escape ก่อนส่งเข้ามา"""
from html import escape
from starlette.responses import HTMLResponse

SOURCE_URL = "https://github.com/hugo8xx/tv-mcp"

CSS = """
:root{--bg:#fff;--fg:#1d1b22;--mut:#6b6575;--acc:#5b3a8c;--line:#e4e0ea;--ok:#14743a;--err:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#16131c;--fg:#eee9f5;--mut:#a59db3;--acc:#b597ff;--line:#2e2838;--ok:#6fd497;--err:#ff8a80}}
*{box-sizing:border-box}body{font-family:-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg);
max-width:460px;margin:0 auto;padding:8vh 16px 40px;line-height:1.55}
a{color:var(--acc)}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 8px}p{margin:8px 0}
input,button,.btn{font:inherit;font-size:17px;padding:12px 14px;width:100%;margin-top:10px;border-radius:12px;
border:1px solid var(--line);background:var(--bg);color:var(--fg);display:block;text-align:center;text-decoration:none}
button.primary,.btn.primary{background:var(--acc);color:#fff;border:0}button.link{border:0;background:none;color:var(--acc);width:auto;padding:4px 0}
.mut{color:var(--mut);font-size:14px}.ok{color:var(--ok)}.err{color:var(--err)}
.card{border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:10px 0}
code{background:var(--line);padding:2px 6px;border-radius:6px;word-break:break-all}
footer{margin-top:40px;font-size:13px;color:var(--mut)}footer a{color:var(--mut)}
"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · TV MCP</title>
<style>{CSS}</style></head><body>{body}
<footer><a href="/">TV MCP</a> · <a href="/account">บัญชี</a> · <a href="/privacy">ความเป็นส่วนตัว</a> ·
<a href="{SOURCE_URL}">source (AGPL-3.0)</a></footer></body></html>""", status)


def msg(m: str | None, cls: str = "err") -> str:
    return f'<p class="{cls}">{escape(m)}</p>' if m else ""


def hidden(fields: dict) -> str:
    return "".join(f'<input type="hidden" name="{escape(k)}" value="{escape(str(v))}">' for k, v in fields.items())

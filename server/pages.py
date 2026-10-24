"""หน้าเว็บเล็กๆ ของ server (มือถือเป็นหลัก) — ข้อความที่มาจากผู้ใช้ต้อง escape ก่อนส่งเข้ามา
CSP ของ server ห้ามโหลดอะไรจากข้างนอก: ฟอนต์ใช้ของระบบ รูปใช้ SVG inline เท่านั้น
"""
from html import escape
from starlette.responses import HTMLResponse

SOURCE_URL = "https://github.com/hugo8xx/tv-mcp"
APK_URL = f"{SOURCE_URL}/releases/latest/download/tv-remote.apk"

CSS = """
:root{--bg:#f7f5fb;--card:#fff;--fg:#1d1b22;--mut:#6b6575;--acc:#5b3a8c;--acc2:#8b5cf6;--line:#e6e1ee;
--ok:#14743a;--err:#b3261e;--shadow:0 1px 2px rgba(30,20,50,.06),0 8px 24px rgba(30,20,50,.06)}
@media (prefers-color-scheme:dark){:root{--bg:#121017;--card:#1b1822;--fg:#eee9f5;--mut:#a59db3;--acc:#b597ff;--acc2:#7c5cf0;
--line:#2c2736;--ok:#6fd497;--err:#ff8a80;--shadow:0 1px 2px rgba(0,0,0,.3),0 8px 24px rgba(0,0,0,.25)}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{font-family:-apple-system,BlinkMacSystemFont,"Sukhumvit Set","Noto Sans Thai","Segoe UI",system-ui,sans-serif;
background:var(--bg);color:var(--fg);max-width:480px;margin:0 auto;padding:7vh 16px 40px;line-height:1.6}
body.wide{max-width:960px;padding-top:0}
a{color:var(--acc)}h1{font-size:26px;line-height:1.25;margin:0 0 6px}h2{font-size:18px;margin:28px 0 8px}p{margin:8px 0}
input,button,.btn{font:inherit;font-size:17px;padding:13px 16px;width:100%;margin-top:10px;border-radius:12px;
border:1px solid var(--line);background:var(--card);color:var(--fg);display:block;text-align:center;text-decoration:none}
button.primary,.btn.primary{background:var(--acc);color:#fff;border:0}
@media (prefers-color-scheme:dark){button.primary,.btn.primary{background:var(--acc2)}}
button.link{border:0;background:none;color:var(--acc);width:auto;padding:4px 0}
.mut{color:var(--mut);font-size:14px}.ok{color:var(--ok)}.err{color:var(--err)}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px;margin:10px 0;box-shadow:var(--shadow)}
code{background:var(--line);padding:2px 6px;border-radius:6px;word-break:break-all;font-size:.92em}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);font-size:13px;color:var(--mut)}footer a{color:var(--mut)}

/* ---------- หน้าแรก ---------- */
.nav{display:flex;align-items:center;justify-content:space-between;padding:18px 0}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px;color:var(--fg);text-decoration:none}
.nav a.signin{font-size:15px;text-decoration:none;padding:8px 14px;border:1px solid var(--line);border-radius:999px;background:var(--card)}
.hero{display:grid;gap:32px;align-items:center;padding:28px 0 12px}
@media (min-width:820px){.hero{grid-template-columns:1.1fr .9fr;padding:56px 0 24px}}
.badge{display:inline-block;font-size:13px;font-weight:600;color:var(--acc);background:color-mix(in srgb,var(--acc) 12%,transparent);
padding:4px 10px;border-radius:999px;margin-bottom:12px}
.hero h1{font-size:clamp(30px,6vw,44px);letter-spacing:-.01em}
.hero h1 em{font-style:normal;background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
.lead{font-size:18px;color:var(--mut);max-width:34em}
.ctas{display:flex;flex-wrap:wrap;gap:10px;margin-top:20px}.ctas .btn{width:auto;margin:0;padding:12px 20px}
.chat{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:18px;box-shadow:var(--shadow)}
.bubble{max-width:85%;padding:10px 14px;border-radius:18px;margin:8px 0;font-size:15px}
.me{margin-left:auto;background:var(--acc);color:#fff;border-bottom-right-radius:6px}
@media (prefers-color-scheme:dark){.me{background:var(--acc2)}}
.bot{background:var(--bg);border:1px solid var(--line);border-bottom-left-radius:6px}
.bot small{display:block;color:var(--mut);font-size:12px;margin-bottom:2px}
.section{padding:28px 0}.section>h2{font-size:22px;margin:0 0 4px}
.steps{display:grid;gap:12px;margin-top:16px}@media (min-width:820px){.steps{grid-template-columns:repeat(3,1fr)}}
.step .n{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:50%;background:var(--acc);color:#fff;
font-weight:700;font-size:14px;margin-bottom:8px}
@media (prefers-color-scheme:dark){.step .n{background:var(--acc2)}}
.step h3{margin:0 0 4px;font-size:17px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(2,1fr);margin-top:16px}@media (min-width:820px){.grid{grid-template-columns:repeat(3,1fr)}}
.feat{padding:14px 16px}.feat b{display:block;font-size:16px;margin:4px 0 2px}.feat span{font-size:14px;color:var(--mut)}
.feat .ic{font-size:22px}
.note{display:grid;gap:12px}@media (min-width:820px){.note{grid-template-columns:1fr 1fr}}
.copy{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
"""

TV_SVG = """<svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#5b3a8c"/><stop offset="1" stop-color="#8b5cf6"/></linearGradient></defs>
<rect x="2" y="6" width="28" height="19" rx="5" fill="url(#g)"/><path d="M11 29h10" stroke="#8b5cf6" stroke-width="2.4" stroke-linecap="round"/>
<path d="M10 13.5l3.2 3.2L10 20M16 20h6" stroke="#fff" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>"""


def page(title: str, body: str, status: int = 200, wide: bool = False) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · TV MCP</title>
<meta name="description" content="สั่งทีวี Android TV ที่บ้านด้วยคำพูด ผ่าน Claude บนมือถือ เว็บ หรือ Desktop">
<style>{CSS}</style></head><body{' class="wide"' if wide else ''}>{body}
<footer><a href="/">TV MCP</a> · <a href="/account">บัญชี</a> · <a href="/privacy">ความเป็นส่วนตัว</a> ·
<a href="{SOURCE_URL}">source (AGPL-3.0)</a> · © PinaTo Co., Ltd.</footer></body></html>""", status)


def msg(m: str | None, cls: str = "err") -> str:
    return f'<p class="{cls}">{escape(m)}</p>' if m else ""


def hidden(fields: dict) -> str:
    return "".join(f'<input type="hidden" name="{escape(k)}" value="{escape(str(v))}">' for k, v in fields.items())


def landing(public_url: str, host: str) -> HTMLResponse:
    mcp = escape(public_url + "/mcp")
    return page("สั่งทีวีด้วย Claude", f"""
<nav class="nav"><a class="brand" href="/">{TV_SVG} TV MCP</a><a class="signin" href="/account">เข้าสู่ระบบ</a></nav>

<section class="hero">
  <div>
    <span class="badge">Beta · ใช้ฟรี</span>
    <h1>สั่งทีวีที่บ้าน<br><em>แค่พิมพ์บอก Claude</em></h1>
    <p class="lead">เปิดแอป เปิดลิงก์ ปรับเสียง ปิดทีวี — จาก Claude บนมือถือ เว็บ หรือ Desktop ไม่ต้องหารีโมท</p>
    <div class="ctas">
      <a class="btn primary" href="/account">เริ่มใช้ด้วย Google</a>
      <a class="btn" href="{APK_URL}">ดาวน์โหลดแอปทีวี</a>
    </div>
  </div>
  <div class="chat" aria-label="ตัวอย่างการสั่งงาน">
    <div class="bubble me">เปิด YouTube บนทีวีหน่อย</div>
    <div class="bubble bot"><small>TV MCP · tv_open_app</small>เปิด YouTube บนทีวีแล้วครับ</div>
    <div class="bubble me">เบาเสียงลงเหลือ 20</div>
    <div class="bubble bot"><small>TV MCP · tv_volume</small>เสียง 20/100</div>
    <div class="bubble me">เปิดสูตรนี้ให้ดูบนจอใหญ่ที</div>
    <div class="bubble bot"><small>TV MCP · tv_open_url</small>เปิดลิงก์บนทีวีแล้ว</div>
  </div>
</section>

<section class="section">
  <h2>เริ่มใช้ใน 3 ขั้น</h2>
  <p class="mut">ใช้เวลาประมาณ 5 นาที ทำครั้งเดียว</p>
  <div class="steps">
    <div class="card step"><span class="n">1</span><h3>ติดตั้งแอปบนทีวี</h3>
      <p class="mut">ลงแอป <b>MCP</b> ผ่านแอป Downloader บนทีวี ด้วยลิงก์นี้</p><p><code>{escape(APK_URL.removeprefix("https://"))}</code></p></div>
    <div class="card step"><span class="n">2</span><h3>จับคู่ทีวี</h3>
      <p class="mut">เปิดแอปบนทีวี จะมีรหัส 6 หลัก แล้วใส่รหัสที่ <a href="/pair">{escape(host)}/pair</a> บนมือถือหรือคอมพิวเตอร์</p></div>
    <div class="card step"><span class="n">3</span><h3>เชื่อมกับ Claude</h3>
      <p class="mut">claude.ai → Settings → Connectors → Add custom connector แล้วใส่</p><p><code>{mcp}</code></p></div>
  </div>
</section>

<section class="section">
  <h2>ทำอะไรได้บ้าง</h2>
  <div class="grid">
    <div class="card feat"><span class="ic">📺</span><b>เปิดแอป</b><span>Netflix, YouTube, Prime, Apple TV, ทีวีช่องปกติ</span></div>
    <div class="card feat"><span class="ic">🔗</span><b>เปิดลิงก์</b><span>ส่งเว็บจากแชทขึ้นจอใหญ่</span></div>
    <div class="card feat"><span class="ic">🔊</span><b>ปรับเสียง</b><span>ตั้งระดับ เพิ่ม ลด ปิดเสียง</span></div>
    <div class="card feat"><span class="ic">🎮</span><b>ปุ่มรีโมท</b><span>Home, Back, ลูกศร, Play/Pause</span></div>
    <div class="card feat"><span class="ic">⏻</span><b>ปิดทีวี</b><span>สั่งปิดจากที่ไหนก็ได้</span></div>
    <div class="card feat"><span class="ic">✅</span><b>เช็กสถานะ</b><span>ทีวีเปิดอยู่ไหม เสียงเท่าไหร่</span></div>
  </div>
  <p class="mut">ปุ่มรีโมทและปิดทีวีใช้ได้กับ Sony BRAVIA (ต้องเปิด IP Control) · ยี่ห้ออื่นเปิดแอปและปรับเสียงได้</p>
</section>

<section class="section note">
  <div class="card"><b>🔒 ปลอดภัยไว้ก่อน</b>
    <p class="mut">ทีวีต่อออกมาหา server อย่างเดียว ไม่ต้องเปิดพอร์ตในบ้าน · สั่งได้เฉพาะคำสั่งที่กำหนด ไม่มี shell · ไม่เก็บประวัติคำสั่ง ·
    ลบบัญชีได้เองทันที</p><p><a href="/privacy">อ่านนโยบายความเป็นส่วนตัว</a></p></div>
  <div class="card"><b>🧩 โอเพนซอร์ส</b>
    <p class="mut">โค้ดทั้งหมดเปิดเผยภายใต้ AGPL-3.0 อยากรันบน server ของตัวเองก็ได้</p><p><a href="{SOURCE_URL}">ดูบน GitHub</a></p></div>
</section>
""", wide=True)

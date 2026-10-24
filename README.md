# tv-mcp

Control your Android TV from Claude — on your phone, the web, or desktop.

> "Open YouTube on the TV", "volume 20", "open this link on the TV", "turn the TV off"

```
Claude (mobile / web / desktop)
        │  custom connector · OAuth 2.1 + PKCE
        ▼
  server/  ── MCP server you host (e.g. Railway)
        ▲
        │  long-poll, outbound from the TV only — no ports opened at home
        │
  android/ ── "TV Remote" app on the TV
        ├─ open links, open apps, volume
        └─ remote keys / power off via Sony BRAVIA IP Control
```

No computer has to stay on and ADB can stay off once the app is installed.

> The TV app UI and tool messages are currently in Thai.

## What's in the repo

| Path | What |
|---|---|
| `server/` | Python (Starlette) MCP server: Streamable HTTP, OAuth 2.1 with dynamic client registration, relay to the TV, on-screen-code pairing |
| `android/` | TV app (`com.pinato.remote`), plain Java, no dependencies |
| `mac-mcp/` | Optional stdio MCP for Claude Code that talks to the TV over ADB on your LAN (adds screenshots) |

## Tools

| Tool | Works on |
|---|---|
| `tv_status` | any Android TV |
| `tv_open_url` | Sony BRAVIA (built-in Vewd browser) |
| `tv_open_app` — netflix, youtube, prime, appletv, livetv, browser, … | any Android TV (`livetv`/`browser` are Sony packages) |
| `tv_volume` | any Android TV |
| `tv_remote` — home, back, d-pad, play/pause, input, … | Sony BRAVIA with IP Control + PSK |
| `tv_power_off` | Sony BRAVIA with IP Control + PSK |

Tested on a Sony BRAVIA KD-X9000H (Android TV 12). Turning the TV **on** from standby is not supported.

## Setup

### 1. Deploy the server

Any host that runs Python 3.12 and keeps **one** instance always on works. With [Railway](https://railway.com):

```bash
cd server
railway init -n tv-mcp
railway add -s tv-mcp
railway variables -s tv-mcp --set SECRET_KEY=$(openssl rand -hex 32) \
                            --set OWNER_PASSWORD='<a long password you will type once>'
railway domain -s tv-mcp
railway up -s tv-mcp --ci
```

| Variable | |
|---|---|
| `SECRET_KEY` | required — signs every token. Changing it signs out every Claude client |
| `OWNER_PASSWORD` | required — asked when you connect Claude and when you pair a TV |
| `BLOCKED_DOMAINS` | optional — extra domains `tv_open_url` refuses, comma separated |
| `DEVICE_EPOCH` | optional — bump it to unpair every TV |
| `PUBLIC_URL` | optional — defaults to `https://$RAILWAY_PUBLIC_DOMAIN` |

Railway bills by usage; the server must stay awake, so expect a small monthly cost.

### 2. Install the TV app

Build it (JDK 17 + Android SDK 34):

```bash
cd android
./gradlew assembleRelease -PtvRemoteServer=https://<your-server>
# → app/build/outputs/apk/release/app-release.apk
```

Then sideload it with a downloader app on the TV, or over ADB:

```bash
TV=<tv-ip>:5555 URL=https://<your-server> bash android/install.sh
```

### 3. Pair the TV

1. Open **TV Remote** on the TV — it shows a 6-digit code.
2. On your phone open `https://<your-server>/pair`, enter the code and your owner password.
3. In the app press **"ให้สิทธิ์เปิดแอป"** and allow *Display over other apps* — without it Android blocks opening links and apps from the background.

### 4. Connect Claude

claude.ai → **Settings → Connectors → Add custom connector** → URL `https://<your-server>/mcp` → sign in with the owner password. The connector is then available in the Claude mobile and desktop apps too.

### Optional: Sony remote keys and power off

On the TV: **Settings → Network → Home network → IP control** → Authentication *Normal and Pre-Shared Key*, set a PSK, and enable *Remote start*. Then pass it to the app once:

```bash
TV=<tv-ip>:5555 URL=https://<your-server> PSK=<your-psk> bash android/install.sh
```

### Optional: ADB MCP for Claude Code (screenshots)

```bash
claude mcp add tv -s user -e TV_ADB_SERIAL=<tv-ip>:5555 -- python3 /path/to/tv-mcp/mac-mcp/server.py
```

## Security model

- The TV only makes outbound HTTPS requests; nothing listens on your network.
- OAuth redirect URIs are limited to `claude.ai`, `claude.com` and `localhost`. Tokens are stateless HMAC: access 1 h, refresh 30 days.
- 5 wrong owner passwords lock sign-in and pairing for 15 minutes.
- Commands are allow-listed twice, on the server and again in the TV app: http(s) links only, known apps and keys only, no shell.
- Pairing codes expire after 10 minutes and need the owner password.

## Content policy

`tv_open_url` refuses known unlicensed streaming sites (see `BLOCKED_DOMAINS` in `server/app.py`). Please keep it that way in your deployment.

## Uninstall

`adb uninstall com.pinato.remote` (or uninstall from TV settings), delete the server, and remove the connector in claude.ai.

## License

[AGPL-3.0](LICENSE) © PinaTo Co., Ltd. If you run a modified version as a service for others, you must publish your changes.

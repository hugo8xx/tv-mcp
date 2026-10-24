#!/bin/bash
# build + ติดตั้งแอป TV Remote บนทีวีผ่าน ADB — รันซ้ำได้
#   TV=192.168.1.50:5555 URL=https://my-tv.up.railway.app bash android/install.sh
# ไม่ใส่ TV_DEVICE_TOKEN = จับคู่ด้วยรหัสบนจอทีวี; ใส่ PSK=… เพื่อเปิดปุ่มรีโมท (Sony)
set -e
: "${TV:?ตั้ง TV=<ip ทีวี>:5555}"
: "${URL:?ตั้ง URL=https://<server ของคุณ>}"
cd "$(dirname "$0")"
TC="$HOME/.cleanyourtv-toolchain"
export JAVA_HOME=$(ls -d "$TC"/jdk-17*/Contents/Home | head -1) ANDROID_HOME="$TC/android-sdk"
echo "sdk.dir=$ANDROID_HOME" > local.properties
./gradlew assembleRelease -q --console=plain -PtvRemoteServer="$URL"
adb connect $TV >/dev/null
adb -s $TV install -r app/build/outputs/apk/release/app-release.apk
adb -s $TV shell appops set com.pinato.remote SYSTEM_ALERT_WINDOW allow
adb -s $TV shell dumpsys deviceidle whitelist +com.pinato.remote >/dev/null
EXTRA=(--es server_url "$URL")
[ -n "$TV_DEVICE_TOKEN" ] && EXTRA+=(--es device_token "$TV_DEVICE_TOKEN")
[ -n "$PSK" ] && EXTRA+=(--es psk "$PSK")
adb -s $TV shell am start -n com.pinato.remote/.MainActivity "${EXTRA[@]}" >/dev/null
echo "ติดตั้งแล้ว — ถ้าทีวีขึ้นรหัส ให้เปิด $URL/pair บนมือถือ"

package com.pinato.remote;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.media.AudioManager;
import android.net.Uri;
import android.os.IBinder;
import android.os.PowerManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Long-poll คำสั่งจาก tv-remote server (ต่อออกอย่างเดียว ไม่เปิดพอร์ต) แล้วทำตาม
 * ทีวีตรวจซ้ำเองทุกคำสั่ง: เปิดได้แค่ http(s) ใน Vewd, แอปตามรายชื่อ, ปุ่มตามรายชื่อ
 */
public class RemoteService extends Service {
    static volatile String status = "กำลังเริ่ม…";
    static volatile String lastCommand = "-";

    private static final String VEWD = "com.vewd.core.integration.dia";
    private static final String VEWD_ACTIVITY = "com.vewd.core.browser.BrowserActivity";
    private static final Map<String, String> APPS = new HashMap<>();
    static {
        APPS.put("netflix", "com.netflix.ninja");
        APPS.put("youtube", "com.google.android.youtube.tv");
        APPS.put("prime", "com.amazon.amazonvideo.livingroom");
        APPS.put("appletv", "com.apple.atve.sony.appletv");
        APPS.put("livetv", "com.sony.dtv.tvx");
        APPS.put("browser", VEWD);
        APPS.put("pinato", "com.pinato.board");
        APPS.put("tvhub", "io.github.maxhayim.tvhub");
    }
    /** ชื่อปุ่มของเรา → ชื่อใน getRemoteControllerInfo ของ Sony (ลองตามลำดับ) */
    private static final Map<String, String[]> KEYS = new HashMap<>();
    static {
        String[][] k = {
            {"home", "Home"}, {"back", "Return", "Back"}, {"up", "Up"}, {"down", "Down"}, {"left", "Left"},
            {"right", "Right"}, {"ok", "Confirm", "DpadCenter"}, {"play", "Play"}, {"pause", "Pause"},
            {"stop", "Stop"}, {"rewind", "Rewind"}, {"forward", "Forward"}, {"next", "Next"}, {"prev", "Prev"},
            {"volume_up", "VolumeUp"}, {"volume_down", "VolumeDown"}, {"mute", "Mute"},
            {"channel_up", "ChannelUp"}, {"channel_down", "ChannelDown"}, {"input", "Input"},
            {"subtitle", "SubTitle", "Subtitle"}, {"audio", "Audio"}, {"guide", "GGuide", "EPG", "Guide"},
            {"info", "Display", "Info"},
        };
        for (String[] r : k) KEYS.put(r[0], java.util.Arrays.copyOfRange(r, 1, r.length));
    }

    private Thread worker;
    private volatile boolean running;
    private Map<String, String> ircc;

    private static volatile Thread current;

    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("remote", MODE_PRIVATE); }

    /** server ที่ตั้งไว้ หรือค่าที่ฝังตอน build (-PtvRemoteServer=…) */
    static String serverUrl(Context c) { return prefs(c).getString("server_url", BuildConfig.DEFAULT_SERVER); }

    /** ปลุก loop ที่กำลังรอ (เช่น หลังจับคู่ใหม่) ให้ต่อทันที */
    static void wake() { Thread t = current; if (t != null) t.interrupt(); }

    static String postJson(String url, String body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(15000);
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json");
        try (OutputStream os = c.getOutputStream()) { os.write(body.getBytes(StandardCharsets.UTF_8)); }
        int code = c.getResponseCode();
        String r = read(code < 400 ? c.getInputStream() : c.getErrorStream());
        c.disconnect();
        if (code >= 400 && code != 410) throw new IllegalStateException("HTTP " + code);
        return r;
    }

    static void start(Context c) {
        Intent i = new Intent(c, RemoteService.class);
        c.startForegroundService(i);
    }

    @Override public IBinder onBind(Intent i) { return null; }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("remote", "TV Remote", NotificationManager.IMPORTANCE_MIN));
        Notification n = new Notification.Builder(this, "remote")
                .setContentTitle("TV Remote").setContentText("รอคำสั่งจาก Claude")
                .setSmallIcon(android.R.drawable.ic_media_play).build();
        startForeground(1, n);
        if (worker == null || !worker.isAlive()) {
            running = true;
            worker = new Thread(this::loop, "remote-poll");
            current = worker;
            worker.start();
        }
        return START_STICKY;
    }

    @Override public void onDestroy() { running = false; if (worker != null) worker.interrupt(); super.onDestroy(); }

    // ---------- loop ----------

    private void loop() {
        long backoff = 2000;
        while (running) {
            Thread.interrupted();  // ล้างสัญญาณจาก wake()
            SharedPreferences p = prefs(this);
            String server = serverUrl(this), token = p.getString("device_token", "");
            if (server.isEmpty() || token.isEmpty()) { status = "ยังไม่ได้จับคู่"; sleep(10000); continue; }
            try {
                HttpURLConnection c = open(server + "/tv/poll", token);
                c.setReadTimeout(40000);
                c.setRequestProperty("X-TV-Info", new JSONObject().put("v", BuildConfig.VERSION_NAME).toString());
                int code = c.getResponseCode();
                if (code == 401) { status = "ถูกถอนสิทธิ์ — กด \"จับคู่กับ Claude\""; c.disconnect(); sleep(60000); continue; }
                status = "ออนไลน์ · " + now();
                backoff = 2000;
                if (code == 200) {
                    JSONObject cmd = new JSONObject(read(c.getInputStream()));
                    c.disconnect();
                    JSONObject res = execute(cmd);
                    HttpURLConnection r = open(server + "/tv/result", token);
                    r.setRequestMethod("POST");
                    r.setDoOutput(true);
                    r.setRequestProperty("Content-Type", "application/json");
                    try (OutputStream os = r.getOutputStream()) { os.write(res.toString().getBytes(StandardCharsets.UTF_8)); }
                    r.getResponseCode();
                    r.disconnect();
                } else {
                    c.disconnect();
                }
            } catch (Exception e) {
                status = "ต่อ server ไม่ได้ (" + e.getClass().getSimpleName() + ") · ลองใหม่";
                sleep(backoff);
                backoff = Math.min(backoff * 2, 60000);
            }
        }
    }

    private static HttpURLConnection open(String url, String token) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(15000);
        c.setRequestProperty("Authorization", "Bearer " + token);
        return c;
    }

    // ---------- commands ----------

    private JSONObject execute(JSONObject cmd) throws Exception {
        String id = cmd.optString("id"), name = cmd.optString("cmd");
        JSONObject a = cmd.optJSONObject("args");
        if (a == null) a = new JSONObject();
        lastCommand = name + " · " + now();
        JSONObject out = new JSONObject().put("id", id);
        try {
            String msg;
            switch (name) {
                case "status": msg = doStatus(); break;
                case "open_url": msg = doOpenUrl(a.optString("url")); break;
                case "open_app": msg = doOpenApp(a.optString("app")); break;
                case "volume": msg = doVolume(a.optString("action"), a.optInt("level", -1)); break;
                case "remote": msg = doRemote(a.optJSONArray("keys")); break;
                case "power_off": msg = doPowerOff(); break;
                default: throw new IllegalArgumentException("ไม่รู้จักคำสั่ง " + name);
            }
            return out.put("ok", true).put("message", msg);
        } catch (Exception e) {
            return out.put("ok", false).put("message", e.getMessage() == null ? e.toString() : e.getMessage());
        }
    }

    private String doStatus() {
        AudioManager am = getSystemService(AudioManager.class);
        PowerManager pm = getSystemService(PowerManager.class);
        int v = am.getStreamVolume(AudioManager.STREAM_MUSIC), max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        return "จอ" + (pm.isInteractive() ? "เปิด" : "ปิด")
                + " · เสียง " + v + "/" + max + (am.isStreamMute(AudioManager.STREAM_MUSIC) ? " (ปิดเสียง)" : "")
                + " · ปุ่มรีโมท " + (psk().isEmpty() ? "ยังไม่ได้ตั้ง PSK" : "พร้อม");
    }

    private String doOpenUrl(String url) {
        Uri u = Uri.parse(url);
        String s = u.getScheme();
        if (!("http".equals(s) || "https".equals(s)) || u.getHost() == null) throw new IllegalArgumentException("รับเฉพาะ http(s)");
        Intent i = new Intent(Intent.ACTION_VIEW, u)
                .setComponent(new ComponentName(VEWD, VEWD_ACTIVITY))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(i);
        return "เปิด " + url + " แล้ว";
    }

    private String doOpenApp(String app) {
        String pkg = APPS.get(app);
        if (pkg == null) throw new IllegalArgumentException("ไม่รู้จักแอป " + app);
        Intent i = getPackageManager().getLeanbackLaunchIntentForPackage(pkg);
        if (i == null) i = getPackageManager().getLaunchIntentForPackage(pkg);
        if (i == null) throw new IllegalStateException("ไม่พบแอป " + app + " บนทีวี");
        startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        return "เปิด " + app + " แล้ว";
    }

    private String doVolume(String action, int level) {
        AudioManager am = getSystemService(AudioManager.class);
        int s = AudioManager.STREAM_MUSIC, f = AudioManager.FLAG_SHOW_UI;
        switch (action) {
            case "up": am.adjustStreamVolume(s, AudioManager.ADJUST_RAISE, f); break;
            case "down": am.adjustStreamVolume(s, AudioManager.ADJUST_LOWER, f); break;
            case "mute": am.adjustStreamVolume(s, AudioManager.ADJUST_MUTE, f); break;
            case "unmute": am.adjustStreamVolume(s, AudioManager.ADJUST_UNMUTE, f); break;
            case "set":
                if (level < 0 || level > 100) throw new IllegalArgumentException("level 0–100");
                am.setStreamVolume(s, Math.round(level / 100f * am.getStreamMaxVolume(s)), f);
                break;
            case "get": break;
            default: throw new IllegalArgumentException("action ไม่ถูกต้อง");
        }
        return "เสียง " + am.getStreamVolume(s) + "/" + am.getStreamMaxVolume(s)
                + (am.isStreamMute(s) ? " (ปิดเสียง)" : "");
    }

    // ---------- Sony IP Control (ต้องตั้ง PSK) ----------

    private String psk() { return prefs(this).getString("psk", ""); }

    private String sony(String path, String body, String soapAction) throws Exception {
        if (psk().isEmpty()) throw new IllegalStateException("ยังไม่ได้ตั้ง Sony IP Control PSK บนทีวี");
        HttpURLConnection c = (HttpURLConnection) new URL("http://127.0.0.1/sony/" + path).openConnection();
        c.setConnectTimeout(3000);
        c.setReadTimeout(5000);
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setRequestProperty("X-Auth-PSK", psk());
        if (soapAction != null) {
            c.setRequestProperty("Content-Type", "text/xml; charset=UTF-8");
            c.setRequestProperty("SOAPACTION", soapAction);
        } else {
            c.setRequestProperty("Content-Type", "application/json");
        }
        try (OutputStream os = c.getOutputStream()) { os.write(body.getBytes(StandardCharsets.UTF_8)); }
        int code = c.getResponseCode();
        String resp = read(code < 400 ? c.getInputStream() : c.getErrorStream());
        c.disconnect();
        if (code == 403) throw new IllegalStateException("PSK ไม่ถูกต้อง");
        if (code >= 400) throw new IllegalStateException("Sony IP Control ตอบ " + code);
        return resp;
    }

    private Map<String, String> irccCodes() throws Exception {
        if (ircc != null) return ircc;
        String r = sony("system", "{\"method\":\"getRemoteControllerInfo\",\"id\":10,\"params\":[],\"version\":\"1.0\"}", null);
        JSONArray list = new JSONObject(r).getJSONArray("result").getJSONArray(1);
        Map<String, String> m = new HashMap<>();
        for (int i = 0; i < list.length(); i++) m.put(list.getJSONObject(i).getString("name"), list.getJSONObject(i).getString("value"));
        return ircc = m;
    }

    private String doRemote(JSONArray keys) throws Exception {
        if (keys == null || keys.length() == 0 || keys.length() > 20) throw new IllegalArgumentException("keys 1–20 ปุ่ม");
        Map<String, String> codes = irccCodes();
        for (int i = 0; i < keys.length(); i++) {
            String k = keys.getString(i);
            String[] names = KEYS.get(k);
            if (names == null) throw new IllegalArgumentException("ไม่รู้จักปุ่ม " + k);
            String code = null;
            for (String n : names) if ((code = codes.get(n)) != null) break;
            if (code == null) throw new IllegalStateException("ทีวีไม่มีปุ่ม " + k);
            sony("IRCC", "<?xml version=\"1.0\"?><s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\""
                    + " s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body>"
                    + "<u:X_SendIRCC xmlns:u=\"urn:schemas-sony-com:service:IRCC:1\"><IRCCCode>" + code
                    + "</IRCCCode></u:X_SendIRCC></s:Body></s:Envelope>",
                    "\"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC\"");
            Thread.sleep(250);
        }
        return "กด " + keys.join(", ").replace("\"", "") + " แล้ว";
    }

    private String doPowerOff() throws Exception {
        sony("system", "{\"method\":\"setPowerStatus\",\"id\":55,\"params\":[{\"status\":false}],\"version\":\"1.0\"}", null);
        return "ปิดทีวีแล้ว";
    }

    // ---------- utils ----------

    private static String read(InputStream in) throws Exception {
        if (in == null) return "";
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        for (int n; (n = in.read(buf)) > 0; ) b.write(buf, 0, n);
        in.close();
        return b.toString("UTF-8");
    }

    private static String now() { return new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date()); }

    private static void sleep(long ms) { try { Thread.sleep(ms); } catch (InterruptedException ignored) { } }
}

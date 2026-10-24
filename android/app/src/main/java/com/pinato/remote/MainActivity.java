package com.pinato.remote;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Wizard 3 ขั้น ใช้รีโมทได้ทั้งหมด ไม่ต้องใช้ adb:
 *   1 จับคู่บัญชี  — QR เปิด {server}/pair?code=… บนมือถือ แล้วกดยืนยัน
 *   2 ให้สิทธิ์เปิดแอป — Display over other apps
 *   3 เชื่อม Claude — server บอกผ่าน /tv/me ว่า Claude ของบัญชีนี้เชื่อมแล้วหรือยัง
 * ขั้นที่ผ่านแล้วขึ้น ✓ ผ่านแล้ว · ครบ 3 ขั้น = หน้า "พร้อมใช้แล้ว"
 * ยังรับค่าทาง intent extras ได้ (สำหรับคนที่ใช้ adb):
 *   adb shell am start -n com.pinato.remote/.MainActivity --es server_url https://… --es device_token … [--es psk …]
 */
public class MainActivity extends Activity {
    private static final int BG = Color.rgb(8, 36, 31), CARD = Color.rgb(11, 58, 51), GREEN = Color.rgb(15, 110, 94),
            MINT = Color.rgb(93, 202, 165), PALE = Color.rgb(159, 225, 203), GOLD = Color.rgb(255, 214, 102),
            MUTED = Color.rgb(180, 178, 169);

    private final Step[] steps = new Step[3];
    private ImageView qr;
    private TextView panelTitle, panelBig, panelBody;
    private Button settingsBtn, actionBtn;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private volatile Thread pairing;
    private volatile String pairCode, pairUrl, pairPid;
    private volatile boolean repair;          // ผู้ใช้สั่งจับคู่ใหม่จากเมนูตั้งค่า
    private volatile Boolean claudeOk;        // null = ยังไม่รู้
    private volatile String account = "", connectUrl = "";
    private String shownQr = "";
    private long lastMe;

    private final Runnable tick = new Runnable() {
        @Override public void run() { render(); ui.postDelayed(this, 1500); }
    };

    private static final class Step {
        LinearLayout box;
        TextView state, title, sub;
    }

    // ---------- layout ----------

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);
        root.setPadding(96, 48, 96, 40);
        root.setClipChildren(false);

        LinearLayout head = new LinearLayout(this);
        head.setGravity(Gravity.CENTER_VERTICAL);
        head.setClipChildren(false);
        TextView title = text(34, Color.WHITE);
        title.setText("MCP");
        head.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        settingsBtn = button("ตั้งค่า", v -> openSettings());
        head.addView(settingsBtn, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        root.addView(head);

        LinearLayout row = new LinearLayout(this);
        row.setPadding(0, 24, 0, 24);
        String[] names = {"1 · จับคู่บัญชี", "2 · ให้สิทธิ์เปิดแอป", "3 · เชื่อม Claude"};
        for (int i = 0; i < 3; i++) {
            Step s = new Step();
            s.box = new LinearLayout(this);
            s.box.setOrientation(LinearLayout.VERTICAL);
            s.box.setPadding(28, 18, 28, 18);
            s.state = text(16, MINT);
            s.title = text(22, Color.WHITE);
            s.title.setText(names[i]);
            s.sub = text(16, PALE);
            s.box.addView(s.state);
            s.box.addView(s.title);
            s.box.addView(s.sub);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
            lp.setMargins(i == 0 ? 0 : 12, 0, i == 2 ? 0 : 12, 0);
            row.addView(s.box, lp);
            steps[i] = s;
        }
        root.addView(row);

        LinearLayout panel = new LinearLayout(this);
        panel.setGravity(Gravity.CENTER_VERTICAL);
        panel.setClipChildren(false);
        qr = new ImageView(this);
        qr.setScaleType(ImageView.ScaleType.FIT_CENTER);
        qr.setBackgroundColor(Color.WHITE);
        LinearLayout.LayoutParams qlp = new LinearLayout.LayoutParams(340, 340);
        qlp.setMargins(0, 0, 48, 0);
        panel.addView(qr, qlp);
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setClipChildren(false);
        panelTitle = text(28, Color.WHITE);
        panelBig = text(44, GOLD);
        panelBody = text(18, MUTED);
        actionBtn = button("", v -> onAction());
        col.addView(panelTitle);
        col.addView(panelBig);
        col.addView(panelBody);
        LinearLayout.LayoutParams alp = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        alp.setMargins(8, 20, 0, 0);
        col.addView(actionBtn, alp);
        panel.addView(col, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        root.addView(panel, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        setContentView(root);
        handle(getIntent());
    }

    private TextView text(int sp, int color) {
        TextView t = new TextView(this);
        t.setTextSize(sp);
        t.setTextColor(color);
        t.setPadding(0, 4, 0, 4);
        return t;
    }

    private Button button(String label, View.OnClickListener l) {
        Button btn = new Button(this);
        btn.setText(label);
        btn.setTextSize(20);
        btn.setAllCaps(false);
        btn.setOnClickListener(l);
        // ทีวีไม่มีเมาส์: ต้องเห็นชัดว่าปุ่มไหนถูกเลือก
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(16);
        btn.setBackground(bg);
        View.OnFocusChangeListener f = (v, has) -> {
            bg.setColor(has ? GOLD : GREEN);
            bg.setStroke(has ? 6 : 0, Color.WHITE);
            btn.setTextColor(has ? BG : Color.WHITE);
            btn.animate().scaleX(has ? 1.06f : 1f).scaleY(has ? 1.06f : 1f).setDuration(120).start();
        };
        btn.setOnFocusChangeListener(f);
        f.onFocusChange(btn, false);
        btn.setPadding(36, 18, 36, 18);
        return btn;
    }

    private void paintStep(Step s, int state, String sub) {   // 0 = รอ, 1 = กำลังทำ, 2 = ผ่านแล้ว
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(18);
        bg.setColor(state == 2 ? CARD : BG);
        bg.setStroke(state == 1 ? 5 : 2, state == 1 ? GOLD : state == 2 ? GREEN : Color.rgb(40, 70, 64));
        s.box.setBackground(bg);
        s.state.setText(state == 2 ? "✓ ผ่านแล้ว" : state == 1 ? "→ ทำขั้นนี้" : "รอขั้นก่อนหน้า");
        s.state.setTextColor(state == 2 ? MINT : state == 1 ? GOLD : MUTED);
        s.title.setTextColor(state == 0 ? MUTED : Color.WHITE);
        s.sub.setText(sub);
    }

    // ---------- intent / lifecycle ----------

    @Override protected void onNewIntent(Intent i) { super.onNewIntent(i); handle(i); }

    private void handle(Intent i) {
        SharedPreferences.Editor e = RemoteService.prefs(this).edit();
        boolean changed = false;
        for (String k : new String[]{"server_url", "device_token", "psk"}) {
            String v = i == null ? null : i.getStringExtra(k);
            if (v != null) { e.putString(k, v.trim()); changed = true; }
        }
        if (changed) { e.commit(); RemoteService.wake(); claudeOk = null; lastMe = 0; }
        RemoteService.start(this);
        render();
    }

    @Override protected void onResume() { super.onResume(); lastMe = 0; ui.post(tick); }
    @Override protected void onPause() { super.onPause(); ui.removeCallbacks(tick); }
    @Override protected void onDestroy() { pairing = null; super.onDestroy(); }

    // ---------- state ----------

    private boolean paired() {
        return !repair && !RemoteService.prefs(this).getString("device_token", "").isEmpty()
                && !RemoteService.status.startsWith("ถูกถอนสิทธิ์");
    }

    private int current = -1;

    private void render() {
        String server = RemoteService.serverUrl(this);
        boolean s1 = paired(), s2 = Settings.canDrawOverlays(this), s3 = Boolean.TRUE.equals(claudeOk);
        if (s1) refreshMe(server);
        else if (!server.isEmpty() && pairing == null) startPairing();

        int now = !s1 ? 0 : !s2 ? 1 : !s3 ? 2 : 3;
        paintStep(steps[0], s1 ? 2 : 1, s1 ? (account.isEmpty() ? "จับคู่แล้ว" : account) : "สแกน QR ด้วยมือถือ");
        paintStep(steps[1], s2 ? 2 : now == 1 ? 1 : 0, s2 ? "เปิดลิงก์และแอปได้" : "เปิดสวิตช์ให้แอป MCP");
        paintStep(steps[2], s3 ? 2 : now == 2 ? 1 : 0, s3 ? "Claude ใช้งานได้" : "เพิ่ม connector ใน claude.ai");

        if (server.isEmpty()) {
            showPanel(null, "ตั้งค่า server ก่อน", "", "กดปุ่มด้านล่าง แล้วใส่ URL ของ server", "ตั้งค่า server");
        } else if (now == 0) {
            if (pairCode == null) showPanel(null, "กำลังขอรหัสจับคู่…", "", RemoteService.status, null);
            else showPanel(server + "/tv/pair/qr?pid=" + pairPid, "สแกน QR ด้วยมือถือ", pairCode,
                    "หรือเปิด " + pairUrl + " บนมือถือหรือคอมพิวเตอร์ แล้วใส่รหัสนี้", null);
        } else if (now == 1) {
            showPanel(null, "ให้สิทธิ์เปิดแอปและลิงก์", "",
                    "กดปุ่มด้านล่าง แล้วเปิดสวิตช์ของ MCP จากนั้นกด Back กลับมาที่นี่", "เปิดหน้าตั้งค่าสิทธิ์");
        } else if (now == 2) {
            showPanel(server + "/qr/connect.png", "เชื่อม Claude กับทีวี", "",
                    "สแกน QR ดูวิธีเพิ่ม connector ใน claude.ai\nหรือใส่ URL นี้เป็น custom connector:\n" + server + "/mcp\n\nกดอนุญาตแล้ว ขั้นนี้จะขึ้น ✓ เอง", null);
        } else {
            showPanel(null, "พร้อมใช้แล้ว 🎉", "",
                    "ลองพิมพ์ใน Claude บนมือถือหรือคอมพิวเตอร์:\n"
                    + "  “เปิด YouTube บนทีวี”\n  “เสียงทีวี 20”\n  “เปิดลิงก์นี้บนทีวี …”\n\n"
                    + "ไม่ต้องเปิดหน้านี้ค้างไว้ — MCP ทำงานเบื้องหลังเอง", null);
        }
        if (now != current) {   // ย้ายโฟกัสไปที่ปุ่มที่ควรกดของขั้นนี้
            current = now;
            (actionBtn.getVisibility() == View.VISIBLE ? actionBtn : settingsBtn).requestFocus();
        }
    }

    private void showPanel(String qrUrl, String title, String big, String body, String action) {
        panelTitle.setText(title);
        panelBig.setText(big);
        panelBig.setVisibility(big.isEmpty() ? View.GONE : View.VISIBLE);
        panelBody.setText(body);
        actionBtn.setText(action == null ? "" : action);
        actionBtn.setVisibility(action == null ? View.GONE : View.VISIBLE);
        qr.setVisibility(qrUrl == null ? View.GONE : View.VISIBLE);
        if (qrUrl != null && !qrUrl.equals(shownQr)) {
            shownQr = qrUrl;
            qr.setImageDrawable(null);
            new Thread(() -> {
                Bitmap bm = fetchBitmap(qrUrl);
                ui.post(() -> { if (qrUrl.equals(shownQr) && bm != null) qr.setImageBitmap(bm); });
            }, "qr").start();
        }
    }

    private void onAction() {
        if (RemoteService.serverUrl(this).isEmpty()) { editServer(); return; }
        if (current == 1) {
            try {
                startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + getPackageName())));
            } catch (Exception e) {
                panelBody.setText("ทีวีรุ่นนี้เปิดหน้านี้ตรงไม่ได้\nไปที่ Settings › Apps › Special app access › Display over other apps › MCP");
            }
        }
    }

    // ---------- server ----------

    private void refreshMe(String server) {
        if (System.currentTimeMillis() - lastMe < 5000) return;
        lastMe = System.currentTimeMillis();
        String token = RemoteService.prefs(this).getString("device_token", "");
        new Thread(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(server + "/tv/me").openConnection();
                c.setConnectTimeout(8000);
                c.setReadTimeout(8000);
                c.setRequestProperty("Authorization", "Bearer " + token);
                if (c.getResponseCode() == 200) {
                    JSONObject j = new JSONObject(new java.util.Scanner(c.getInputStream(), "UTF-8").useDelimiter("\\A").next());
                    account = j.optString("account", "");
                    claudeOk = j.optBoolean("claude", false);
                }
                c.disconnect();
            } catch (Exception ignored) { }
        }, "me").start();
    }

    private static Bitmap fetchBitmap(String url) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(8000);
            c.setReadTimeout(8000);
            if (c.getResponseCode() != 200) return null;
            try (InputStream in = c.getInputStream()) { return BitmapFactory.decodeStream(in); }
        } catch (Exception e) {
            return null;
        }
    }

    private void startPairing() {
        String server = RemoteService.serverUrl(this);
        if (server.isEmpty()) return;
        pairCode = null;
        Thread t = new Thread(() -> pairLoop(server), "pair");
        pairing = t;
        t.start();
    }

    private void pairLoop(String server) {
        Thread me = Thread.currentThread();
        try {
            JSONObject start = new JSONObject(RemoteService.postJson(server + "/tv/pair/start", "{}"));
            pairPid = start.getString("pair_id");
            pairUrl = start.getString("url");
            pairCode = start.getString("code");
            long until = System.currentTimeMillis() + start.optLong("expires_in", 600) * 1000;
            while (pairing == me && System.currentTimeMillis() < until) {
                Thread.sleep(3000);
                JSONObject r = new JSONObject(RemoteService.postJson(server + "/tv/pair/poll",
                        new JSONObject().put("pair_id", pairPid).toString()));
                String st = r.optString("status");
                if ("ok".equals(st)) {
                    RemoteService.prefs(this).edit().putString("server_url", server)
                            .putString("device_token", r.getString("device_token")).commit();
                    RemoteService.wake();
                    repair = false;
                    claudeOk = null;
                    lastMe = 0;
                    break;
                }
                if (!"pending".equals(st)) break;
            }
        } catch (Exception e) {
            RemoteService.status = "ต่อ server ไม่ได้: " + e.getMessage();
            try { Thread.sleep(5000); } catch (InterruptedException ignored) { }
        }
        if (pairing == me) { pairing = null; pairCode = null; }   // หมดอายุ/ผิดพลาด → render() ขอรหัสใหม่เอง
    }

    // ---------- ตั้งค่า ----------

    private void openSettings() {
        String server = RemoteService.serverUrl(this);
        String[] items = {"จับคู่ใหม่", "เปลี่ยน server"};
        TextView info = text(16, Color.DKGRAY);
        info.setPadding(56, 24, 56, 0);
        info.setText("MCP v" + BuildConfig.VERSION_NAME + "\nServer: " + (server.isEmpty() ? "ยังไม่ได้ตั้ง" : server)
                + "\nสถานะ: " + RemoteService.status);
        new AlertDialog.Builder(this)
                .setCustomTitle(info)
                .setItems(items, (d, w) -> {
                    if (w == 0) { repair = true; pairing = null; startPairing(); render(); }
                    else editServer();
                })
                .setNegativeButton("ปิด", null)
                .show();
    }

    private void editServer() {
        EditText in = new EditText(this);
        in.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        String cur = RemoteService.serverUrl(this);
        in.setText(cur.isEmpty() ? "https://" : cur);
        new AlertDialog.Builder(this)
                .setTitle("Server URL")
                .setView(in)
                .setPositiveButton("บันทึก", (d, w) -> {
                    String u = in.getText().toString().trim().replaceAll("/+$", "");
                    if (!u.startsWith("https://")) { panelBody.setText("ต้องขึ้นต้นด้วย https://"); return; }
                    RemoteService.prefs(this).edit().putString("server_url", u).remove("device_token").commit();
                    claudeOk = null;
                    pairing = null;
                    render();
                })
                .setNegativeButton("ยกเลิก", null)
                .show();
    }
}

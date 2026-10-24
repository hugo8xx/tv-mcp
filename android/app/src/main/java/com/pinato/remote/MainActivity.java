package com.pinato.remote;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
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
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * หน้าจับคู่ + สถานะ ใช้รีโมทได้ทั้งหมด ไม่ต้องใช้ adb:
 *   จับคู่: ทีวีโชว์รหัส 6 หลัก → เปิด {server}/pair บนมือถือ → ใส่รหัส + รหัสเจ้าของ
 * ยังรับค่าทาง intent extras ได้ (สำหรับคนที่ใช้ adb):
 *   adb shell am start -n com.pinato.remote/.MainActivity --es server_url https://… --es device_token … [--es psk …]
 */
public class MainActivity extends Activity {
    private TextView status, big, hint;
    private Button pairBtn, permBtn;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private volatile Thread pairing;
    private final Runnable tick = new Runnable() {
        @Override public void run() { render(); ui.postDelayed(this, 2000); }
    };

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(24, 18, 40));
        root.setPadding(96, 56, 96, 56);

        TextView title = text(34);
        title.setText("📺 MCP");
        big = text(44);
        big.setGravity(Gravity.CENTER);
        big.setTextColor(Color.rgb(255, 214, 102));
        big.setVisibility(View.GONE);
        hint = text(20);
        hint.setGravity(Gravity.CENTER);
        hint.setTextColor(Color.rgb(255, 214, 102));
        hint.setVisibility(View.GONE);
        status = text(18);

        LinearLayout row = new LinearLayout(this);
        row.setPadding(16, 32, 0, 16);
        row.setClipChildren(false);  // ปุ่มที่ถูกเลือกขยายขึ้น อย่าให้ขอบโดนตัด
        root.setClipChildren(false);
        pairBtn = button(row, "จับคู่กับ Claude", v -> startPairing());
        permBtn = button(row, "ให้สิทธิ์เปิดแอป", v -> openOverlaySettings());
        button(row, "เปลี่ยน server", v -> editServer());

        root.addView(title);
        root.addView(big);
        root.addView(hint);
        root.addView(status);
        root.addView(row);
        setContentView(root);
        handle(getIntent());
    }

    private TextView text(int sp) {
        TextView t = new TextView(this);
        t.setTextSize(sp);
        t.setTextColor(Color.WHITE);
        t.setPadding(0, 12, 0, 12);
        return t;
    }

    private Button button(LinearLayout row, String label, View.OnClickListener l) {
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
            bg.setColor(has ? Color.rgb(255, 214, 102) : Color.rgb(70, 62, 92));
            bg.setStroke(has ? 6 : 0, Color.WHITE);
            btn.setTextColor(has ? Color.rgb(24, 18, 40) : Color.WHITE);
            btn.animate().scaleX(has ? 1.08f : 1f).scaleY(has ? 1.08f : 1f).setDuration(120).start();
        };
        btn.setOnFocusChangeListener(f);
        f.onFocusChange(btn, false);
        btn.setPadding(24, 20, 24, 20);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1);
        lp.setMargins(0, 0, 24, 0);
        row.addView(btn, lp);
        return btn;
    }

    @Override protected void onNewIntent(Intent i) { super.onNewIntent(i); handle(i); }

    private void handle(Intent i) {
        SharedPreferences.Editor e = RemoteService.prefs(this).edit();
        boolean changed = false;
        for (String k : new String[]{"server_url", "device_token", "psk"}) {
            String v = i == null ? null : i.getStringExtra(k);
            if (v != null) { e.putString(k, v.trim()); changed = true; }
        }
        if (changed) { e.commit(); RemoteService.wake(); }
        RemoteService.start(this);
        // ครั้งแรก: รู้ server แล้วแต่ยังไม่จับคู่ → โชว์รหัสเลย
        if (!RemoteService.serverUrl(this).isEmpty() && RemoteService.prefs(this).getString("device_token", "").isEmpty()) {
            startPairing();
        }
        (Settings.canDrawOverlays(this) ? pairBtn : permBtn).requestFocus();
    }

    @Override protected void onResume() { super.onResume(); ui.post(tick); }
    @Override protected void onPause() { super.onPause(); ui.removeCallbacks(tick); }
    @Override protected void onDestroy() { pairing = null; super.onDestroy(); }

    private void render() {
        SharedPreferences p = RemoteService.prefs(this);
        String server = RemoteService.serverUrl(this);
        boolean paired = !p.getString("device_token", "").isEmpty();
        boolean overlay = Settings.canDrawOverlays(this);
        status.setText(
                "Server: " + (server.isEmpty() ? "ยังไม่ได้ตั้ง — กด \"เปลี่ยน server\"" : server) + "\n"
                + "จับคู่: " + (paired ? "แล้ว" : "ยังไม่ได้") + " · สถานะ: " + RemoteService.status + "\n"
                + "สิทธิ์เปิดแอป/ลิงก์: " + (overlay ? "✅ พร้อม" : "❌ ยังไม่ได้ให้ — กด \"ให้สิทธิ์เปิดแอป\" แล้วเปิดให้ MCP") + "\n"
                + "ปุ่มรีโมท/ปิดเครื่อง (Sony IP Control): " + (p.getString("psk", "").isEmpty() ? "ยังไม่ได้ตั้ง PSK" : "พร้อม") + "\n"
                + "คำสั่งล่าสุด: " + RemoteService.lastCommand + "\n\n"
                + "ไม่ต้องเปิดหน้านี้ค้างไว้ — ทำงานเบื้องหลังเอง");
    }

    // ---------- จับคู่ ----------

    private void startPairing() {
        String server = RemoteService.serverUrl(this);
        if (server.isEmpty()) { editServer(); return; }
        Thread t = new Thread(() -> pairLoop(server), "pair");
        pairing = t;
        t.start();
    }

    private void showBig(String s) { showBig(s, null); }

    /** ข้อความใหญ่ (เช่น รหัส) + บรรทัดอธิบายตัวเล็ก — แยกกันเพื่อไม่ให้ดันปุ่มตกขอบจอ */
    private void showBig(String s, String sub) {
        ui.post(() -> {
            big.setText(s);
            big.setVisibility(s == null ? View.GONE : View.VISIBLE);
            hint.setText(sub);
            hint.setVisibility(sub == null ? View.GONE : View.VISIBLE);
        });
    }

    private void pairLoop(String server) {
        Thread me = Thread.currentThread();
        try {
            JSONObject start = new JSONObject(RemoteService.postJson(server + "/tv/pair/start", "{}"));
            String pid = start.getString("pair_id");
            showBig("รหัส  " + start.getString("code"), "เปิด " + start.getString("url") + " บนมือถือหรือคอมพิวเตอร์ แล้วใส่รหัสนี้");
            long until = System.currentTimeMillis() + start.optLong("expires_in", 600) * 1000;
            while (pairing == me && System.currentTimeMillis() < until) {
                Thread.sleep(3000);
                JSONObject r = new JSONObject(RemoteService.postJson(server + "/tv/pair/poll",
                        new JSONObject().put("pair_id", pid).toString()));
                String st = r.optString("status");
                if ("ok".equals(st)) {
                    RemoteService.prefs(this).edit().putString("server_url", server)
                            .putString("device_token", r.getString("device_token")).commit();
                    RemoteService.wake();
                    showBig("จับคู่สำเร็จ ✅");
                    ui.postDelayed(() -> showBig(null), 4000);
                    return;
                }
                if (!"pending".equals(st)) break;
            }
            if (pairing == me) showBig("รหัสหมดอายุ — กด \"จับคู่กับ Claude\" อีกครั้ง");
        } catch (Exception e) {
            if (pairing == me) showBig("ต่อ server ไม่ได้: " + e.getMessage());
        }
    }

    // ---------- สิทธิ์ / server ----------

    private void openOverlaySettings() {
        try {
            startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + getPackageName())));
        } catch (Exception e) {
            showBig("ทีวีรุ่นนี้เปิดหน้านี้ตรงไม่ได้", "ไปที่ Settings › Apps › Special app access › Display over other apps › MCP");
        }
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
                    if (!u.startsWith("https://")) { showBig("ต้องขึ้นต้นด้วย https://"); return; }
                    RemoteService.prefs(this).edit().putString("server_url", u).remove("device_token").commit();
                    startPairing();
                })
                .setNegativeButton("ยกเลิก", null)
                .show();
    }
}

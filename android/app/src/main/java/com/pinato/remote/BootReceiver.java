package com.pinato.remote;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** เริ่ม service เองหลังบูตหรืออัปเดตแอป */
public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) { RemoteService.start(c); }
}

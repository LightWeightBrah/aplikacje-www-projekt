package pl.solid.dialer;

import android.Manifest;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.os.Build;
import android.provider.CallLog;
import android.provider.Telephony;
import android.telephony.SmsMessage;
import android.telephony.TelephonyManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Co dzieje się na telefonie, a CRM o tym nie wie (dialer 1.3):
 * - ktoś dzwoni → CRM od razu dostaje numer i pokazuje agentowi, który to klient,
 * - po rozmowie → połączenia z rejestru (klient oddzwonił, numer wybrany ręcznie) idą do CRM,
 *   CRM zapisuje tylko te z numerami klientów (prywatne rozmowy przepadają) i liczy je do minut rozmów,
 * - przyszedł SMS → idzie do CRM, CRM zapisuje tylko SMS-y od klientów i wiadomości od sieci
 *   o numerze klienta („numer jest już dostępny”).
 * Działa tylko wtedy, gdy dialer jest uruchomiony (usługa DialerService).
 */
final class PhoneWatcher {
    private static final String LOG_SINCE = "call_log_since";
    private final Context ctx;
    private final String base, token;
    private volatile boolean syncSoon = false;
    private volatile long idleAt = 0;
    private long lastSync = 0;
    private BroadcastReceiver phoneReceiver, smsReceiver;

    PhoneWatcher(Context ctx, String base, String token) {
        this.ctx = ctx;
        this.base = base;
        this.token = token;
        SharedPreferences p = Api.prefs(ctx);
        // pierwsze uruchomienie: bierzemy rejestr od teraz (stare rozmowy nie trafiają do CRM)
        if (p.getLong(LOG_SINCE, 0) == 0) p.edit().putLong(LOG_SINCE, System.currentTimeMillis()).apply();
    }

    private boolean has(String permission) {
        return ctx.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    void start() {
        phoneReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context c, Intent i) {
                String state = i.getStringExtra(TelephonyManager.EXTRA_STATE);
                @SuppressWarnings("deprecation")
                String number = i.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER);
                if (TelephonyManager.EXTRA_STATE_RINGING.equals(state) && number != null && !number.isEmpty()) {
                    post("/api/dialer/phone/ring/", body("phone", number));
                } else if (TelephonyManager.EXTRA_STATE_IDLE.equals(state)) {
                    idleAt = System.currentTimeMillis();   // rozmowa się skończyła: za chwilę wpis w rejestrze
                    syncSoon = true;
                }
            }
        };
        register(phoneReceiver, new IntentFilter(TelephonyManager.ACTION_PHONE_STATE_CHANGED), null);

        smsReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context c, Intent i) {
                SmsMessage[] parts = Telephony.Sms.Intents.getMessagesFromIntent(i);
                if (parts == null) return;
                // długi SMS przychodzi w kilku częściach: sklejamy po nadawcy
                Map<String, StringBuilder> texts = new LinkedHashMap<>();
                Map<String, Long> times = new LinkedHashMap<>();
                for (SmsMessage m : parts) {
                    if (m == null) continue;
                    String from = m.getDisplayOriginatingAddress();
                    if (from == null) continue;
                    texts.computeIfAbsent(from, k -> new StringBuilder()).append(m.getDisplayMessageBody());
                    times.putIfAbsent(from, m.getTimestampMillis());
                }
                for (Map.Entry<String, StringBuilder> e : texts.entrySet()) {
                    try {
                        JSONObject b = new JSONObject();
                        b.put("sender", e.getKey());
                        b.put("text", e.getValue().toString());
                        b.put("at", times.get(e.getKey()));
                        post("/api/dialer/phone/sms/", b);
                    } catch (Exception ignored) { /* zły SMS – pomijamy */ }
                }
            }
        };
        register(smsReceiver, new IntentFilter(Telephony.Sms.Intents.SMS_RECEIVED_ACTION), "android.permission.BROADCAST_SMS");
    }

    private void register(BroadcastReceiver r, IntentFilter f, String senderPermission) {
        if (Build.VERSION.SDK_INT >= 33) {
            ctx.registerReceiver(r, f, senderPermission, null, Context.RECEIVER_EXPORTED);
        } else {
            ctx.registerReceiver(r, f, senderPermission, null);
        }
    }

    void stop() {
        try { if (phoneReceiver != null) ctx.unregisterReceiver(phoneReceiver); } catch (Exception ignored) { }
        try { if (smsReceiver != null) ctx.unregisterReceiver(smsReceiver); } catch (Exception ignored) { }
    }

    /** Wołane z pętli dialera co 2 s: po rozmowie (3 s po rozłączeniu) i co 30 s wysyła nowe wpisy z rejestru. */
    void tick() {
        long now = System.currentTimeMillis();
        boolean afterCall = syncSoon && now - idleAt > 3000;
        if (!afterCall && now - lastSync < 30000) return;
        lastSync = now;
        syncSoon = false;
        try { syncCallLog(); } catch (Exception ignored) { /* brak sieci – następnym razem */ }
    }

    private void syncCallLog() throws Exception {
        if (!has(Manifest.permission.READ_CALL_LOG)) return;
        SharedPreferences p = Api.prefs(ctx);
        long since = p.getLong(LOG_SINCE, System.currentTimeMillis());
        String[] cols = {CallLog.Calls.NUMBER, CallLog.Calls.TYPE, CallLog.Calls.DURATION, CallLog.Calls.DATE};
        JSONArray calls = new JSONArray();
        long newest = since;
        try (Cursor c = ctx.getContentResolver().query(CallLog.Calls.CONTENT_URI, cols,
                CallLog.Calls.DATE + ">?", new String[]{String.valueOf(since)}, CallLog.Calls.DATE + " ASC")) {
            while (c != null && c.moveToNext() && calls.length() < 50) {
                String number = c.getString(0);
                int type = c.getInt(1);
                long seconds = c.getLong(2), date = c.getLong(3);
                newest = Math.max(newest, date);
                String kind = type == CallLog.Calls.INCOMING_TYPE ? "in"
                        : type == CallLog.Calls.OUTGOING_TYPE ? "out"
                        : (type == CallLog.Calls.MISSED_TYPE || type == CallLog.Calls.REJECTED_TYPE) ? "missed" : null;
                if (kind == null || number == null || number.isEmpty()) continue;
                JSONObject o = new JSONObject();
                o.put("phone", number);
                o.put("type", kind);
                o.put("seconds", seconds);
                o.put("at", date);
                calls.put(o);
            }
        }
        if (calls.length() > 0) {
            JSONObject b = new JSONObject();
            b.put("calls", calls);
            Api.Result r = Api.request("POST", base + "/api/dialer/phone/log/", token, b);
            if (r.status != 200) return;   // spróbujemy jeszcze raz (CRM pomija powtórzone wpisy)
        }
        if (newest > since) p.edit().putLong(LOG_SINCE, newest).apply();
    }

    private static JSONObject body(String key, String value) {
        JSONObject o = new JSONObject();
        try { o.put(key, value); } catch (Exception ignored) { }
        return o;
    }

    /** Zgłoszenie w tle (odbiorniki działają w wątku interfejsu, a sieci tam używać nie wolno). */
    private void post(String path, JSONObject b) {
        new Thread(() -> {
            for (int attempt = 0; attempt < 3; attempt++) {
                try {
                    Api.Result r = Api.request("POST", base + path, token, b);
                    if (r.status < 500) return;
                } catch (Exception ignored) { /* brak sieci */ }
                try { Thread.sleep(3000); } catch (InterruptedException e) { return; }
            }
        }, "dialer-report").start();
    }
}

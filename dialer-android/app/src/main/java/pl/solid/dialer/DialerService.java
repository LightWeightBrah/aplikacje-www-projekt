package pl.solid.dialer;

import android.Manifest;
import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;
import android.os.PowerManager;
import android.provider.CallLog;
import android.telecom.TelecomManager;
import android.telephony.SmsManager;
import android.telephony.SubscriptionManager;

import org.json.JSONObject;

import java.io.File;
import java.util.ArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Praca w tle: co 2 sekundy pyta CRM, czy jest numer do wybrania. Jeśli tak – wybiera go
 * (TelecomManager.placeCall), czeka na wpis w rejestrze połączeń i odsyła do CRM, czy klient
 * odebrał i ile trwała rozmowa.
 */
public class DialerService extends Service {
    static final String CHANNEL = "dialer";
    static final int NOTIFICATION_ID = 1;
    static volatile String lastState = "Uruchamiam…";
    static volatile boolean running = false;

    private Thread worker;
    private PowerManager.WakeLock wakeLock;
    private PhoneWatcher watcher;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        createChannel();
        Notification n = notification("Dialer gotowy do pracy");
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(NOTIFICATION_ID, n);
        }
        if (worker == null) {
            running = true;
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "SolidDialer:poll");
            wakeLock.acquire(12 * 60 * 60 * 1000L);   // maks. 12 h dnia pracy
            worker = new Thread(this::loop, "dialer-poll");
            worker.start();
            // kto dzwoni, rejestr połączeń i SMS-y od klientów (dialer 1.3)
            watcher = new PhoneWatcher(getApplicationContext(), Api.server(this), Api.token(this));
            watcher.start();
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        if (worker != null) worker.interrupt();
        if (watcher != null) watcher.stop();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        lastState = "Zatrzymany";
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    private void loop() {
        String base = Api.server(this), token = Api.token(this);
        int errors = 0;
        while (running) {
            try {
                // ?sms=1 tylko ze zgodą na SMS-y: wtedy CRM może zlecić SMS z przypomnieniem
                boolean canSms = checkSelfPermission(Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED;
                // rec=1/0: czy da się tu nagrywać rozmowy (CRM pokazuje to managerowi), v = wersja aplikacji
                String query = "?rec=" + (CallRecorder.ready(this) ? "1" : "0") + "&v=" + BuildConfig.VERSION_NAME
                        + (canSms ? "&sms=1" : "");
                Api.Result r = Api.request("GET", base + "/api/dialer/poll/" + query, token, null);
                if (r.status == 200) {
                    errors = 0;
                    if ("sms".equals(r.json.optString("type"))) handleSms(base, token, r.json);
                    else handleCall(base, token, r.json);
                } else if (r.status == 204) {
                    errors = 0;
                    setState("Dialer gotowy do pracy");
                    // nagrania, których nie udało się wysłać (np. brak internetu), próbujemy co minutę
                    if (System.currentTimeMillis() - lastUploadTry > 60000) uploadPending(base, token);
                    if (watcher != null) watcher.tick();
                } else if (r.status == 401) {
                    setState("Telefon odłączony od CRM – sparuj go ponownie");
                    running = false;
                    break;
                } else {
                    setState("CRM odpowiada błędem " + r.status);
                }
                Thread.sleep(2000);
            } catch (InterruptedException e) {
                break;
            } catch (Exception e) {
                errors++;
                setState("Brak połączenia z CRM (" + errors + ")… próbuję dalej");
                try { Thread.sleep(Math.min(30000, 2000L * errors)); } catch (InterruptedException ie) { break; }
            }
        }
        stopSelf();
    }

    private void handleCall(String base, String token, JSONObject job) throws Exception {
        long id = job.getLong("id");
        String phone = job.getString("phone");
        String name = job.optString("name", "");
        setState("Dzwonię: " + (name.isEmpty() ? phone : name));
        long placedAt = System.currentTimeMillis();
        if (checkSelfPermission(Manifest.permission.CALL_PHONE) != PackageManager.PERMISSION_GRANTED) {
            setState("Brak zgody na dzwonienie – otwórz aplikację");
            finish(base, token, id, false, 0);
            return;
        }
        TelecomManager tm = (TelecomManager) getSystemService(TELECOM_SERVICE);
        tm.placeCall(Uri.fromParts("tel", phone, null), new Bundle());
        // Nagrywanie (gdy manager włączył je w CRM, a na telefonie jest włączone): od wybrania numeru do końca rozmowy
        boolean recording = false;
        if (job.optBoolean("record", false) && CallRecorder.ready(this)) {
            Thread.sleep(800);   // system zaczyna połączenie
            recording = CallRecorder.start(getApplicationContext(), id);
            if (recording) setState("Dzwonię (nagrywam): " + (name.isEmpty() ? phone : name));
        }

        // Rozmowa kończy się wpisem w rejestrze połączeń: czekamy na niego (maks. 4 h – długa rozmowa też się liczy)
        long deadline = placedAt + 4 * 60 * 60 * 1000L;
        try {
            while (running && System.currentTimeMillis() < deadline) {
                Thread.sleep(2000);
                long[] entry = findCall(placedAt);
                if (entry != null) {
                    long seconds = entry[0];
                    if (recording) {
                        File part = CallRecorder.stop();
                        recording = false;
                        if (seconds > 0) CallRecorder.finish(part, id, seconds);
                        else if (part != null) part.delete();   // nie odebrał – nagranie sygnału nikomu niepotrzebne
                    }
                    finish(base, token, id, seconds > 0, seconds);
                    setState(seconds > 0 ? "Rozmowa: " + (seconds / 60) + " min " + (seconds % 60) + " s" : "Nie odebrał");
                    if (seconds > 0) uploadPending(base, token);
                    return;
                }
            }
            finish(base, token, id, false, 0);
        } finally {
            if (recording) {
                File part = CallRecorder.stop();
                if (part != null) part.delete();
            }
        }
    }

    private long lastUploadTry = 0;

    /**
     * Wysyła do CRM gotowe nagrania z pamięci telefonu (recordings/{id}_{sekundy}.ogg). Plik znika po przyjęciu
     * albo gdy CRM go nie chce (np. nagrywanie wyłączone); przy braku sieci lub błędzie Dysku czeka na kolejną próbę.
     * Nagrania starsze niż 7 dni usuwamy bez wysyłania.
     */
    private void uploadPending(String base, String token) {
        lastUploadTry = System.currentTimeMillis();
        File[] files = CallRecorder.dir(this).listFiles();
        if (files == null) return;
        long now = System.currentTimeMillis();
        for (File f : files) {
            String n = f.getName();
            if (n.endsWith(".part")) {
                if (now - f.lastModified() > 2 * 3600 * 1000L) f.delete();   // porzucone (np. telefon się wyłączył)
                continue;
            }
            if (now - f.lastModified() > 7L * 24 * 3600 * 1000L) { f.delete(); continue; }
            int dot = n.indexOf('.'), us = n.indexOf('_');
            if (us < 1 || dot < us) { f.delete(); continue; }
            String callId = n.substring(0, us), seconds = n.substring(us + 1, dot);
            try {
                setState("Wysyłam nagranie rozmowy do CRM…");
                int status = Api.upload(base + "/api/dialer/calls/" + callId + "/recording/?duration=" + seconds,
                        token, f, CallRecorder.contentType(f));
                if (status < 300 || (status >= 400 && status < 500 && status != 408 && status != 429)) f.delete();
                setState(status < 300 ? "Nagranie zapisane w CRM" : "Dialer gotowy do pracy");
            } catch (Exception e) {
                setState("Nagranie czeka na internet – wyślę je później");
                return;
            }
        }
    }

    /**
     * SMS z przypomnieniem: wysyłany z karty SIM, z której telefon dzwoni (jak SMS napisany ręcznie,
     * trafia też do „Wysłanych” w Wiadomościach). Czekamy na potwierdzenie z sieci (maks. 60 s)
     * i odsyłamy do CRM, czy wyszedł.
     */
    private void handleSms(String base, String token, JSONObject job) throws Exception {
        long id = job.getLong("id");
        String phone = job.getString("phone");
        String text = job.getString("text");
        String name = job.optString("name", "");
        setState("Wysyłam SMS z przypomnieniem: " + (name.isEmpty() ? phone : name));
        String error = null;
        try {
            SmsManager sms = smsManager();
            ArrayList<String> parts = sms.divideMessage(text);
            String action = getPackageName() + ".SMS_SENT." + id;
            CountDownLatch latch = new CountDownLatch(parts.size());
            AtomicReference<String> failure = new AtomicReference<>();
            BroadcastReceiver receiver = new BroadcastReceiver() {
                @Override
                public void onReceive(Context c, Intent i) {
                    if (getResultCode() != Activity.RESULT_OK) failure.set("błąd sieci " + getResultCode());
                    latch.countDown();
                }
            };
            if (Build.VERSION.SDK_INT >= 33) {
                registerReceiver(receiver, new IntentFilter(action), Context.RECEIVER_NOT_EXPORTED);
            } else {
                registerReceiver(receiver, new IntentFilter(action));
            }
            try {
                ArrayList<PendingIntent> sent = new ArrayList<>();
                for (int i = 0; i < parts.size(); i++) {
                    Intent intent = new Intent(action).setPackage(getPackageName());
                    sent.add(PendingIntent.getBroadcast(this, i, intent, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
                }
                sms.sendMultipartTextMessage(phone, null, parts, sent, null);
                if (!latch.await(60, TimeUnit.SECONDS)) error = "brak potwierdzenia z sieci";
                else error = failure.get();
            } finally {
                unregisterReceiver(receiver);
            }
        } catch (SecurityException e) {
            error = "brak zgody na SMS-y";
        } catch (Exception e) {
            error = String.valueOf(e.getMessage());
        }
        try {
            JSONObject body = new JSONObject();
            body.put("ok", error == null);
            if (error != null) body.put("error", error);
            Api.request("POST", base + "/api/dialer/sms/" + id + "/done/", token, body);
        } catch (Exception ignored) { /* CRM zostawi SMS jako „wysyła”, nie wyśle go drugi raz */ }
        setState(error == null ? "SMS z przypomnieniem wysłany" : "SMS nie wyszedł: " + error);
    }

    /** SmsManager dla karty SIM używanej do dzwonienia (przy dwóch kartach: ta sama co połączenia). */
    private SmsManager smsManager() {
        int sub = SubscriptionManager.getDefaultVoiceSubscriptionId();
        boolean valid = sub != SubscriptionManager.INVALID_SUBSCRIPTION_ID;
        if (Build.VERSION.SDK_INT >= 31) {
            SmsManager m = getSystemService(SmsManager.class);
            return valid ? m.createForSubscriptionId(sub) : m;
        }
        return valid ? SmsManager.getSmsManagerForSubscriptionId(sub) : SmsManager.getDefault();
    }

    /** Ostatnie połączenie wychodzące od chwili wybrania numeru: [czas rozmowy w sekundach]. */
    private long[] findCall(long since) {
        if (checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) return null;
        String[] cols = {CallLog.Calls.DURATION, CallLog.Calls.DATE};
        String where = CallLog.Calls.TYPE + "=? AND " + CallLog.Calls.DATE + ">=?";
        String[] args = {String.valueOf(CallLog.Calls.OUTGOING_TYPE), String.valueOf(since - 5000)};
        try (Cursor c = getContentResolver().query(CallLog.Calls.CONTENT_URI, cols, where, args, CallLog.Calls.DATE + " DESC")) {
            if (c != null && c.moveToFirst()) return new long[]{c.getLong(0)};
        } catch (SecurityException ignored) { /* brak zgody */ }
        return null;
    }

    private void finish(String base, String token, long id, boolean answered, long seconds) {
        try {
            JSONObject body = new JSONObject();
            body.put("answered", answered);
            body.put("duration_seconds", seconds);
            Api.request("POST", base + "/api/dialer/calls/" + id + "/finish/", token, body);
        } catch (Exception ignored) { /* CRM i tak pozwoli wpisać wynik ręcznie */ }
    }

    private void setState(String text) {
        lastState = text;
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.notify(NOTIFICATION_ID, notification(text));
    }

    private void createChannel() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        NotificationChannel ch = new NotificationChannel(CHANNEL, "Dialer", NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("Stan połączenia z Solid CRM");
        nm.createNotificationChannel(ch);
    }

    private Notification notification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_dialer)
                .setContentTitle("Solid Dialer")
                .setContentText(text)
                .setContentIntent(pi)
                .setOngoing(true)
                .build();
    }
}

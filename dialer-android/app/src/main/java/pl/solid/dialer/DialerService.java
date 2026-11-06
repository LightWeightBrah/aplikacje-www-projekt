package pl.solid.dialer;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
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

import org.json.JSONObject;

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
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        if (worker != null) worker.interrupt();
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
                Api.Result r = Api.request("GET", base + "/api/dialer/poll/", token, null);
                if (r.status == 200) {
                    errors = 0;
                    handleCall(base, token, r.json);
                } else if (r.status == 204) {
                    errors = 0;
                    setState("Dialer gotowy do pracy");
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

        // Rozmowa kończy się wpisem w rejestrze połączeń: czekamy na niego (maks. 30 min)
        long deadline = placedAt + 30 * 60 * 1000L;
        while (running && System.currentTimeMillis() < deadline) {
            Thread.sleep(2000);
            long[] entry = findCall(placedAt);
            if (entry != null) {
                long seconds = entry[0];
                finish(base, token, id, seconds > 0, seconds);
                setState(seconds > 0 ? "Rozmowa: " + (seconds / 60) + " min " + (seconds % 60) + " s" : "Nie odebrał");
                return;
            }
        }
        finish(base, token, id, false, 0);
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

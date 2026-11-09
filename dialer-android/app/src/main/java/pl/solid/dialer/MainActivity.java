package pl.solid.dialer;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.net.Uri;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** Ekran aplikacji: parowanie z CRM (adres + kod), start / stop dialera i stan. */
public class MainActivity extends Activity {
    private final Handler ui = new Handler(Looper.getMainLooper());
    private EditText server, code;
    private TextView status, info;
    private Button pairBtn, toggleBtn, unpairBtn;
    private LinearLayout pairBox, readyBox;
    private TextView recState, recHelp;
    private Button recEnable, recAppInfo;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setContentView(R.layout.activity_main);
        server = findViewById(R.id.server);
        code = findViewById(R.id.code);
        status = findViewById(R.id.status);
        info = findViewById(R.id.info);
        pairBtn = findViewById(R.id.pair);
        toggleBtn = findViewById(R.id.toggle);
        unpairBtn = findViewById(R.id.unpair);
        pairBox = findViewById(R.id.pair_box);
        readyBox = findViewById(R.id.ready_box);

        server.setText(Api.server(this));
        pairBtn.setOnClickListener(v -> pair());
        toggleBtn.setOnClickListener(v -> toggle());
        unpairBtn.setOnClickListener(v -> {
            stopService(new Intent(this, DialerService.class));
            Api.prefs(this).edit().remove("token").apply();
            refresh();
        });
        recState = findViewById(R.id.rec_state);
        recHelp = findViewById(R.id.rec_help);
        recEnable = findViewById(R.id.rec_enable);
        recAppInfo = findViewById(R.id.rec_appinfo);
        recEnable.setOnClickListener(v -> enableRecording());
        recAppInfo.setOnClickListener(v -> startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.fromParts("package", getPackageName(), null))));
        askPermissions();
        refresh();
        ui.postDelayed(this::tickStatus, 1000);
    }

    /**
     * Nagrywanie rozmów: najpierw zgoda na mikrofon, potem ekran ułatwień dostępu, gdzie agent włącza
     * „Solid Dialer – nagrywanie rozmów”. Na Androidzie 13+ aplikacja spoza Sklepu Play ma ten przełącznik
     * zablokowany, dopóki w „Informacjach o aplikacji” (⋮) nie wybierze się „Zezwól na ustawienia z ograniczeniami”.
     */
    private void enableRecording() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 2);
            return;
        }
        startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == 2 && results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
        }
        refresh();
    }

    private void refreshRecording() {
        boolean mic = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
        boolean service = RecorderService.enabled(this);
        if (mic && service) {
            recState.setText("✓ Włączone na tym telefonie. Rozmowy są nagrywane tylko wtedy, gdy manager włączył nagrywanie w CRM.");
            recEnable.setVisibility(View.GONE);
            recAppInfo.setVisibility(View.GONE);
            recHelp.setText("Jeśli głos klienta w nagraniach jest cichy, rozmawiaj na głośniku albo na słuchawkach.");
            return;
        }
        recEnable.setVisibility(View.VISIBLE);
        recAppInfo.setVisibility(service ? View.GONE : View.VISIBLE);
        recState.setText(mic ? "Jeszcze jeden krok: włącz usługę „Solid Dialer – nagrywanie rozmów”."
                : "Wyłączone. Potrzebne tylko wtedy, gdy manager włączył nagrywanie rozmów w CRM.");
        recEnable.setText(mic ? "Otwórz ułatwienia dostępu" : "Włącz nagrywanie rozmów");
        recHelp.setText("Jak włączyć (raz):\n"
                + "1. Kliknij przycisk wyżej i zgódź się na mikrofon.\n"
                + "2. Na liście ułatwień dostępu wejdź w „Zainstalowane aplikacje” (albo „Pobrane usługi”) → "
                + "„Solid Dialer – nagrywanie rozmów” → włącz.\n"
                + "3. Jeśli przełącznik jest szary albo telefon pisze „Ustawienie z ograniczeniami”: kliknij "
                + "„Otwórz informacje o aplikacji”, w prawym górnym rogu ⋮ → „Zezwól na ustawienia z ograniczeniami”, "
                + "i wróć do kroku 2.\n"
                + "Usługa nic nie czyta z ekranu – Android wymaga jej tylko, żeby można było nagrywać rozmowę.");
    }

    private void tickStatus() {
        if (isFinishing()) return;
        refresh();
        ui.postDelayed(this::tickStatus, 1500);
    }

    private void refresh() {
        boolean paired = !Api.token(this).isEmpty();
        pairBox.setVisibility(paired ? View.GONE : View.VISIBLE);
        readyBox.setVisibility(paired ? View.VISIBLE : View.GONE);
        if (paired) {
            status.setText(DialerService.running ? DialerService.lastState : "Dialer zatrzymany");
            toggleBtn.setText(DialerService.running ? "Zatrzymaj dialer" : "Uruchom dialer");
            info.setText("Połączono z: " + Api.server(this) + "\nKonto: " + Api.prefs(this).getString("user", "")
                    + "\nWersja aplikacji: " + BuildConfig.VERSION_NAME);
            refreshRecording();
        }
    }

    private void pair() {
        String base = Api.normalizeServer(server.getText().toString());
        String c = code.getText().toString().replaceAll("\\D", "");
        if (c.length() != 6) { status.setText("Kod ma 6 cyfr (znajdziesz go w CRM: menu konta → Telefon)."); return; }
        pairBtn.setEnabled(false);
        status.setText("Łączę z CRM…");
        new Thread(() -> {
            String msg;
            try {
                JSONObject body = new JSONObject();
                body.put("code", c);
                body.put("name", Build.MANUFACTURER + " " + Build.MODEL);
                Api.Result r = Api.request("POST", base + "/api/dialer/pair/", null, body);
                if (r.status == 200) {
                    Api.prefs(this).edit().putString("server", base)
                            .putString("token", r.json.getString("token"))
                            .putString("user", r.json.optString("user", "")).apply();
                    msg = "Połączono! Uruchamiam dialer…";
                    ui.post(this::startDialer);
                } else {
                    msg = r.json.optString("detail", "CRM odrzucił kod (" + r.status + ").");
                }
            } catch (Exception e) {
                msg = "Nie mogę połączyć się z " + base + ". Sprawdź adres i internet.";
            }
            String text = msg;
            ui.post(() -> { status.setText(text); pairBtn.setEnabled(true); refresh(); });
        }).start();
    }

    private void toggle() {
        if (DialerService.running) stopService(new Intent(this, DialerService.class));
        else startDialer();
        ui.postDelayed(this::refresh, 300);
    }

    private void startDialer() {
        askPermissions();
        startForegroundService(new Intent(this, DialerService.class));
    }

    private void askPermissions() {
        List<String> need = new ArrayList<>();
        need.add(Manifest.permission.CALL_PHONE);
        need.add(Manifest.permission.READ_CALL_LOG);
        need.add(Manifest.permission.READ_PHONE_STATE);
        need.add(Manifest.permission.SEND_SMS);   // SMS z przypomnieniem przed umówionym telefonem
        need.add(Manifest.permission.RECEIVE_SMS);   // SMS-y od klientów i „numer już dostępny” w CRM
        if (Build.VERSION.SDK_INT >= 33) need.add(Manifest.permission.POST_NOTIFICATIONS);
        List<String> missing = new ArrayList<>();
        for (String p : need) if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) missing.add(p);
        if (!missing.isEmpty()) requestPermissions(missing.toArray(new String[0]), 1);
    }
}

package pl.solid.dialer;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Rozmowa z API Solid CRM (/api/dialer/...). Wywoływać tylko poza wątkiem interfejsu. */
final class Api {
    static final String PREFS = "solid_dialer";

    static final class Result {
        final int status;
        final JSONObject json;
        Result(int status, JSONObject json) { this.status = status; this.json = json; }
    }

    private Api() {}

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static String server(Context c) { return prefs(c).getString("server", ""); }
    static String token(Context c) { return prefs(c).getString("token", ""); }

    /** Adres wpisany przez użytkownika: bez ukośnika na końcu, z http(s)://. */
    static String normalizeServer(String raw) {
        String s = raw.trim();
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        if (!s.startsWith("http://") && !s.startsWith("https://")) s = "https://" + s;
        return s;
    }

    static Result request(String method, String url, String token, JSONObject body) throws IOException {
        HttpURLConnection con = (HttpURLConnection) new URL(url).openConnection();
        con.setRequestMethod(method);
        con.setConnectTimeout(8000);
        con.setReadTimeout(10000);
        con.setRequestProperty("Accept", "application/json");
        if (token != null && !token.isEmpty()) con.setRequestProperty("X-Dialer-Token", token);
        if (body != null) {
            con.setDoOutput(true);
            con.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            try (OutputStream out = con.getOutputStream()) {
                out.write(body.toString().getBytes(StandardCharsets.UTF_8));
            }
        }
        int status = con.getResponseCode();
        InputStream in = status >= 400 ? con.getErrorStream() : con.getInputStream();
        JSONObject json = new JSONObject();
        if (in != null && status != 204) {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int n;
            while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
            in.close();
            String text = buf.toString("UTF-8").trim();
            if (text.startsWith("{")) {
                try { json = new JSONObject(text); } catch (Exception ignored) { /* nie-JSON */ }
            }
        }
        con.disconnect();
        return new Result(status, json);
    }
}

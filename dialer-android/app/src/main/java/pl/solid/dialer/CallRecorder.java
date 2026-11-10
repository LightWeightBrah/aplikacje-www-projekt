package pl.solid.dialer;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.MediaRecorder;
import android.os.Build;

import java.io.File;

/**
 * Nagrywanie jednej rozmowy do pliku w pamięci aplikacji (recordings/), potem DialerService wysyła go do CRM.
 * Format: Opus w pliku .ogg (Android 10+, ok. 0,18 MB na minutę) albo AAC w .m4a na starszych telefonach.
 * Nazwa pliku: {id połączenia}_{sekundy rozmowy}.ogg – sekundy dopisujemy po rozmowie (rename).
 */
final class CallRecorder {
    private static MediaRecorder recorder;
    private static File current;

    private CallRecorder() {}

    /** Telefon jest gotowy do nagrywania: zgoda na mikrofon i włączona usługa ułatwień dostępu. */
    static boolean ready(Context c) {
        return c.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
                && RecorderService.enabled(c);
    }

    static boolean opus() { return Build.VERSION.SDK_INT >= 29; }

    static String extension() { return opus() ? "ogg" : "m4a"; }

    static String contentType(File f) { return f.getName().endsWith(".ogg") ? "audio/ogg" : "audio/mp4"; }

    static File dir(Context c) {
        File d = new File(c.getFilesDir(), "recordings");
        if (!d.exists()) d.mkdirs();
        return d;
    }

    /** Zaczyna nagrywać. Zwraca false, gdy telefon nie pozwolił (wtedy rozmowa idzie bez nagrania). */
    static synchronized boolean start(Context app, long callId) {
        stop();
        // nagrywamy w kontekście usługi ułatwień dostępu, gdy działa (to ona ma prawo do mikrofonu w trakcie rozmowy)
        Context c = RecorderService.instance != null ? RecorderService.instance : app;
        File file = new File(dir(app), callId + "_rec." + extension() + ".part");
        int[] sources = {MediaRecorder.AudioSource.VOICE_RECOGNITION, MediaRecorder.AudioSource.MIC};
        for (int source : sources) {
            MediaRecorder r = Build.VERSION.SDK_INT >= 31 ? new MediaRecorder(c) : new MediaRecorder();
            try {
                r.setAudioSource(source);
                if (opus()) {
                    r.setOutputFormat(MediaRecorder.OutputFormat.OGG);
                    r.setAudioEncoder(MediaRecorder.AudioEncoder.OPUS);
                    r.setAudioSamplingRate(16000);
                    r.setAudioEncodingBitRate(24000);
                } else {
                    r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
                    r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
                    r.setAudioSamplingRate(16000);
                    r.setAudioEncodingBitRate(32000);
                }
                r.setAudioChannels(1);
                r.setOutputFile(file.getAbsolutePath());
                r.prepare();
                r.start();
                recorder = r;
                current = file;
                return true;
            } catch (Exception e) {
                try { r.release(); } catch (Exception ignored) { }
                file.delete();
            }
        }
        return false;
    }

    /** Kończy nagrywanie; zwraca plik roboczy (.part) albo null. */
    static synchronized File stop() {
        if (recorder == null) return null;
        File f = current;
        try { recorder.stop(); } catch (Exception e) { if (f != null) f.delete(); f = null; }   // za krótkie / pusty
        try { recorder.release(); } catch (Exception ignored) { }
        recorder = null;
        current = null;
        return f;
    }

    /** Plik gotowy do wysłania: {id}_{sekundy}.ogg */
    static File finish(File part, long callId, long seconds) {
        if (part == null || !part.exists()) return null;
        File done = new File(part.getParentFile(), callId + "_" + seconds + "." + extension());
        return part.renameTo(done) ? done : null;
    }
}

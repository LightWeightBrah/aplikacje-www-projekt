package pl.solid.dialer;

import android.accessibilityservice.AccessibilityService;
import android.content.ComponentName;
import android.content.Context;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.accessibility.AccessibilityEvent;

/**
 * „Usługa ułatwień dostępu” Solid Dialera. Nic nie czyta z ekranu – jest potrzebna tylko dlatego, że od Androida 10
 * zwykła aplikacja dostaje w trakcie rozmowy telefonicznej ciszę z mikrofonu, a aplikacja z włączoną taką usługą
 * może nagrywać. Agent włącza ją raz: Solid Dialer → „Włącz nagrywanie rozmów”.
 */
public class RecorderService extends AccessibilityService {
    static volatile RecorderService instance;

    @Override
    protected void onServiceConnected() {
        instance = this;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) { /* nic nie czytamy */ }

    @Override
    public void onInterrupt() { }

    @Override
    public void onDestroy() {
        if (instance == this) instance = null;
        super.onDestroy();
    }

    /** Czy agent włączył usługę w ustawieniach ułatwień dostępu. */
    static boolean enabled(Context c) {
        String list = Settings.Secure.getString(c.getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        if (TextUtils.isEmpty(list)) return false;
        ComponentName me = new ComponentName(c, RecorderService.class);
        for (String item : list.split(":")) {
            ComponentName cn = ComponentName.unflattenFromString(item);
            if (me.equals(cn)) return true;
        }
        return false;
    }
}

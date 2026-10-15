package by.zadachi.calls;

import android.net.Uri;
import android.os.PowerManager;
import android.telecom.Call;
import android.util.Log;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Reliable caller identification.
 *
 * Ordinary PHONE_STATE broadcasts often arrive without the number on
 * modern Android/OEM builds. When the user grants Zadachi the system
 * "Caller ID & spam" role (ROLE_CALL_SCREENING), Telecom invokes this
 * service for every incoming call BEFORE/at the start of ringing and
 * hands us the caller's handle (the actual number).
 *
 * We never block or reject anything: the call is always allowed through
 * unchanged, and we only mirror it to the PC when the current Wi-Fi is
 * on the user's white-list (same gate as CallService).
 */
public class ZadachiCallScreeningService
        extends android.telecom.CallScreeningService {

    private ExecutorService io;

    @Override
    public void onCreate() {
        super.onCreate();
        io = Executors.newSingleThreadExecutor();
    }

    @Override
    public void onDestroy() {
        if (io != null) io.shutdownNow();
        super.onDestroy();
    }

    @Override
    public void onScreenCall(Call.Details callDetails) {
        // Always let the call through exactly as if we were not here.
        respondToCall(callDetails, new CallResponse.Builder()
                .setDisallowCall(false)
                .setRejectCall(false)
                .setSkipCallLog(false)
                .setSkipNotification(false)
                .build());
        try {
            Uri handle = callDetails.getHandle();
            if (handle == null || !"tel".equals(handle.getScheme())) return;
            String number = handle.getSchemeSpecificPart();
            if (number == null || number.trim().isEmpty()) return;

            Prefs prefs = new Prefs(getApplicationContext());
            String ssid = Net.currentSsid(getApplicationContext());
            if (!prefs.isActiveHere(ssid)) return; // not on an allowed Wi-Fi

            final String num = number.trim();
            io.execute(() -> {
                PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
                PowerManager.WakeLock wl = pm != null
                        ? pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,
                                "zadachi:screening") : null;
                if (wl != null) wl.acquire(20000);
                try {
                    CallReporter.send(getApplicationContext(), "incoming", num);
                } catch (RuntimeException e) {
                    Log.e("Zadachi", "Screened call report failed", e);
                } finally {
                    if (wl != null && wl.isHeld()) wl.release();
                }
            });
        } catch (RuntimeException e) {
            Log.e("Zadachi", "onScreenCall failed", e);
        }
    }
}

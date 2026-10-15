package by.zadachi.calls;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.telephony.TelephonyManager;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Tracks incoming calls while CallService is stopped outside the
 * allowed Wi-Fi networks.
 *
 * ACTION_PHONE_STATE_CHANGED is on Android's implicit-broadcast
 * exemption list, so this manifest receiver can wake briefly for call
 * state changes without running a service or polling in the background.
 * ZadachiCallScreeningService (when granted the Caller ID role) supplies
 * the number as a second source; the call log is checked at the end.
 */
public class OfflineCallReceiver extends BroadcastReceiver {
    private static final ExecutorService IO = Executors.newSingleThreadExecutor();

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !TelephonyManager.ACTION_PHONE_STATE_CHANGED.equals(intent.getAction())) {
            return;
        }
        Context app = context.getApplicationContext();
        Prefs prefs = new Prefs(app);
        if (!prefs.enabled()) {
            OfflineReminders.clearActive(app);
            return;
        }

        String state = intent.getStringExtra(TelephonyManager.EXTRA_STATE);
        if (TelephonyManager.EXTRA_STATE_RINGING.equals(state)) {
            String ssid = Net.currentSsid(app);
            if (prefs.isActiveHere(ssid)) {
                // The ordinary active path reports this call to the PC.
                OfflineReminders.clearActive(app);
                return;
            }
            String number = intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER);
            OfflineReminders.rememberActive(
                    app, number, "", System.currentTimeMillis());
        } else if (TelephonyManager.EXTRA_STATE_IDLE.equals(state)) {
            OfflineReminders.Item active = OfflineReminders.active(app);
            if (active == null) return;
            // Keep the process alive until the just-written call-log row
            // has been checked and the notification posted.
            PendingResult pending = goAsync();
            IO.execute(() -> {
                try {
                    try {
                        Thread.sleep(700L);
                    } catch (InterruptedException ignored) {
                        Thread.currentThread().interrupt();
                    }
                    CallerIdLog.Hit hit = CallerIdLog.recentIncoming(
                            app, active.callTimestamp);
                    String number = active.number;
                    String name = active.name;
                    if (hit != null) {
                        number = hit.number;
                        name = hit.name;
                    }
                    if ((name == null || name.trim().isEmpty())
                            && number != null && !number.trim().isEmpty()) {
                        name = CallReporter.contactName(app, number);
                    }
                    OfflineReminders.updateActive(app, number, name);
                    OfflineReminders.Item finished = OfflineReminders.finishActive(app);
                    if (finished != null) {
                        OfflineReminderNotifier.show(app, finished);
                    }
                } finally {
                    pending.finish();
                }
            });
        }
    }
}

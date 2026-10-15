package by.zadachi.calls;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Handles "Напомнить / Отклонить" in the offline-call notification. */
public class OfflineReminderActionReceiver extends BroadcastReceiver {
    static final String ACTION_REMIND = "by.zadachi.calls.action.OFFLINE_REMIND";
    static final String ACTION_DISMISS = "by.zadachi.calls.action.OFFLINE_DISMISS";
    static final String EXTRA_ID = "offline_id";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        long id = intent.getLongExtra(EXTRA_ID, 0L);
        if (id <= 0) return;
        Context app = context.getApplicationContext();
        String action = intent.getAction();
        OfflineReminderNotifier.cancel(app, id);
        if (ACTION_REMIND.equals(action)) {
            if (OfflineReminders.markQueued(app, id)) {
                // If an allowed network is already available, deliver
                // now; otherwise the item stays queued until WifiGate /
                // CallService next starts on an allowed network.
                OfflineReminderDelivery.flushAsync(app);
                WifiGate.evaluate(app);
            }
        } else if (ACTION_DISMISS.equals(action)) {
            OfflineReminders.remove(app, id);
        }
    }
}

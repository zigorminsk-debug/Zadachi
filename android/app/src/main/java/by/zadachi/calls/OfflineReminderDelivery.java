package by.zadachi.calls;

import android.content.Context;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Delivers user-approved offline calls when an allowed network returns.
 *
 * No polling is introduced: flushes happen only when Wi-Fi changes, the
 * active service starts, the app is opened, a gate job runs, or the user
 * taps "Напомнить" in the end-of-call notification.
 */
final class OfflineReminderDelivery {
    private static final ExecutorService IO = Executors.newSingleThreadExecutor();
    private static final AtomicBoolean RUNNING = new AtomicBoolean(false);

    private OfflineReminderDelivery() {}

    static void flushAsync(Context context) {
        Context app = context.getApplicationContext();
        if (!RUNNING.compareAndSet(false, true)) return;
        IO.execute(() -> {
            try {
                flush(app);
            } finally {
                RUNNING.set(false);
            }
        });
    }

    static int flush(Context context) {
        Context app = context.getApplicationContext();
        Prefs prefs = new Prefs(app);
        if (!prefs.enabled()) return 0;
        String ssid = Net.currentSsid(app);
        if (!prefs.isActiveHere(ssid)) return 0;

        int delivered = 0;
        List<OfflineReminders.Item> queued = OfflineReminders.queuedItems(app);
        for (OfflineReminders.Item item : queued) {
            if (CallReporter.sendOfflineReminder(app, item)) {
                OfflineReminders.remove(app, item.id);
                delivered++;
            } else {
                // The PC is not actually reachable yet. Keep this and
                // all later items queued for the next network/app event
                // instead of repeatedly scanning/discovering now.
                break;
            }
        }
        return delivered;
    }
}

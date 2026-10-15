package by.zadachi.calls;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

import java.util.concurrent.TimeUnit;

/**
 * Wi-Fi gate: CallService runs ONLY on white-listed Wi-Fi networks.
 *
 * Battery strategy (no continuous work at all):
 *  - JobScheduler periodic check (system-batched, minimum 15 min, doze-aware);
 *  - checks on boot and whenever the user opens the app / changes settings;
 *  - no polling loops, no held wakelocks, no Wi-Fi scans initiated by us.
 * Outside allowed networks the service is fully stopped (zero consumption);
 * the system job is the only thing that ever wakes the app, a few ms at a time.
 */
final class WifiGate {
    private static final int JOB_ID = 39271;

    private WifiGate() {}

    static void schedule(Context c) {
        JobScheduler js = (JobScheduler) c.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js == null) return;
        JobInfo info = new JobInfo.Builder(JOB_ID, new ComponentName(c, GateJobService.class))
                .setPeriodic(TimeUnit.MINUTES.toMillis(15))
                .setPersisted(true)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .build();
        js.schedule(info);
    }

    /** Start/stop CallService to match the current Wi-Fi network. */
    static void evaluate(Context c) {
        try {
            Prefs prefs = new Prefs(c);
            String ssid = Net.currentSsid(c);
            if (prefs.isActiveHere(ssid)) {
                Intent i = new Intent(c, CallService.class);
                if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i);
                else c.startService(i);
            } else {
                c.stopService(new Intent(c, CallService.class));
            }
        } catch (RuntimeException e) {
            // Never let a platform FGS restriction crash the whole app.
            Log.e("Zadachi", "Unable to update CallService for current Wi-Fi", e);
        }
    }
}

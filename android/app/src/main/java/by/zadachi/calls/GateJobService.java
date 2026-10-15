package by.zadachi.calls;

import android.app.job.JobParameters;
import android.app.job.JobService;

/** Periodic (system-scheduled) Wi-Fi gate check. Runs for milliseconds. */
public class GateJobService extends JobService {
    @Override
    public boolean onStartJob(JobParameters params) {
        WifiGate.evaluate(getApplicationContext());
        jobFinished(params, false);
        return false; // no long work on this thread
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return false;
    }
}

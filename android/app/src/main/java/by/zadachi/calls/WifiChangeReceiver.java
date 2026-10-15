package by.zadachi.calls;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Reacts immediately when Wi-Fi changes instead of waiting up to 15 min
 * for the periodic JobScheduler check. The broadcast costs nothing while
 * the network is stable; WifiGate still decides whether the service may
 * run on the newly connected network.
 */
public class WifiChangeReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        WifiGate.schedule(context);
        WifiGate.evaluate(context);
    }
}

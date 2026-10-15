package by.zadachi.calls;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

/**
 * "Display over other apps" (SYSTEM_ALERT_WINDOW) permission.
 *
 * Android's BackgroundActivityStartController explicitly allows an app
 * process holding this permission to start activities from the
 * background (BAL_ALLOW_SAW_PERMISSION in AOSP, still true in Android
 * 15/16). That is the only self-grantable exemption that lets our
 * service open the system dialer directly on a PC command.
 *
 * Zadachi never actually draws overlay windows; the permission is used
 * solely as the system's background-activity-launch exemption.
 */
final class BackgroundLaunch {
    private BackgroundLaunch() {}

    static boolean canDrawOverlays(Context context) {
        return Build.VERSION.SDK_INT < 23 || Settings.canDrawOverlays(context);
    }

    /** Open the system "Display over other apps" page for this app. */
    static void openOverlaySettings(Activity activity) {
        try {
            Intent i = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + activity.getPackageName()));
            activity.startActivity(i);
            return;
        } catch (RuntimeException ignored) {
            // Some OEM builds route this differently; use app details.
        }
        Intent details = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + activity.getPackageName()));
        activity.startActivity(details);
    }
}

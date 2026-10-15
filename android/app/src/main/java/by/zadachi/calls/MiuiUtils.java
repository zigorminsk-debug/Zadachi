package by.zadachi.calls;

import android.app.Activity;
import android.app.AppOpsManager;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Process;
import android.provider.Settings;

import java.lang.reflect.Method;
import java.util.Locale;

/**
 * Xiaomi/MIUI extras.
 *
 * MIUI has its own per-app switch "Display pop-up windows while running
 * in the background". While it is off, MIUI silently blocks activities
 * started from background services (the system dialer included), and
 * only notifications get through. The switch cannot be granted
 * programmatically, so we detect it and deep-link the user to it.
 */
final class MiuiUtils {
    private MiuiUtils() {}

    static boolean isMiui() {
        String make = ((Build.MANUFACTURER == null ? "" : Build.MANUFACTURER)
                + " " + (Build.BRAND == null ? "" : Build.BRAND))
                .toLowerCase(Locale.ROOT);
        if (make.contains("xiaomi") || make.contains("redmi")
                || make.contains("poco")) {
            return true;
        }
        try {
            Class<?> sp = Class.forName("android.os.SystemProperties");
            Method get = sp.getMethod("get", String.class);
            Object v = get.invoke(null, "ro.miui.ui.version.name");
            return v != null && !String.valueOf(v).isEmpty();
        } catch (Exception ignored) {
            return false;
        }
    }

    /**
     * True when background pop-ups are allowed (or cannot be determined,
     * in which case we optimistically assume the direct launch works).
     */
    static boolean backgroundPopupAllowed(Context context) {
        if (!isMiui()) return true;
        AppOpsManager ops = (AppOpsManager)
                context.getSystemService(Context.APP_OPS_SERVICE);
        if (ops == null) return true;
        int uid = Process.myUid();
        String pkg = context.getPackageName();
        try {
            return ops.checkOpNoThrow("android:background_start_activity", uid, pkg)
                    == AppOpsManager.MODE_ALLOWED;
        } catch (RuntimeException ignored) {
            // op string unknown on this build; try the numeric MIUI op.
        }
        try {
            Method m = ops.getClass().getMethod(
                    "checkOpNoThrow", int.class, int.class, String.class);
            Object r = m.invoke(ops, 10021, uid, pkg); // OP_BACKGROUND_START_ACTIVITY
            return r instanceof Integer && (Integer) r == AppOpsManager.MODE_ALLOWED;
        } catch (Exception ignored) {
            return true;
        }
    }

    /** Open MIUI's per-app permission editor (fallback: app details). */
    static void openBackgroundPopupSettings(Activity activity) {
        try {
            Intent i = new Intent("miui.intent.action.APP_PERM_EDITOR");
            i.setClassName("com.miui.securitycenter",
                    "com.miui.permcenter.permissions.PermissionsEditorActivity");
            i.putExtra("extra_pkgname", activity.getPackageName());
            activity.startActivity(i);
            return;
        } catch (RuntimeException ignored) {
            // Not a MIUI build or editor moved; use the standard page.
        }
        Intent details = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + activity.getPackageName()));
        activity.startActivity(details);
    }
}

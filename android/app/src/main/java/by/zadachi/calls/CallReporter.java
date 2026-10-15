package by.zadachi.calls;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.provider.ContactsContract;

/**
 * Sends one call event to the PC (used by CallService and by the call
 * screening service). Adds the caller's name from the phone's own
 * address book when READ_CONTACTS is granted; the PC additionally knows
 * names from its accumulated call history.
 */
final class CallReporter {
    private CallReporter() {}

    static boolean send(Context context, String event, String number) {
        Context app = context.getApplicationContext();
        Prefs prefs = new Prefs(app);
        String num = number == null ? "" : number.trim();
        String name = contactName(app, num);
        String json = "{\"event\":\"" + Net.jsonEscape(event) + "\""
                + ",\"number\":\"" + Net.jsonEscape(num) + "\""
                + ",\"name\":\"" + Net.jsonEscape(name) + "\""
                + ",\"device\":\"" + Net.jsonEscape(Net.deviceName()) + "\""
                + ",\"ts\":" + System.currentTimeMillis() + "}";

        boolean sent = false;
        String ip = prefs.pcIp();
        if (!ip.isEmpty()) {
            sent = tryPost(ip, prefs.pcPort(), json, prefs.token());
        }
        if (!sent) {
            // Saved IP may be stale or point at another server: one
            // discovery attempt, then give up (battery-friendly).
            Net.PcEndpoint pc = Net.discoverPc();
            if (pc != null) {
                prefs.setPcIp(pc.ip);
                prefs.setPcPort(pc.port);
                sent = tryPost(pc.ip, pc.port, json, prefs.token());
            }
        }

        String when = new java.text.SimpleDateFormat(
                "dd.MM HH:mm:ss", java.util.Locale.getDefault())
                .format(new java.util.Date());
        prefs.setLastEvent(when + " • " + event + " "
                + (num.isEmpty() ? "(номер скрыт)" : num)
                + (name.isEmpty() ? "" : " • " + name)
                + (sent ? " • отправлено на ПК ✔" : " • не отправлено на ПК ✘"));
        return sent;
    }

    private static boolean tryPost(String ip, int port, String json, String token) {
        try {
            String body = Net.postJson("http://" + ip + ":" + port + "/api/call",
                    json, token, 4000);
            return body != null && body.contains("\"ok\":true");
        } catch (Exception e) {
            return false;
        }
    }

    /** Display name from the phone address book, or "" (needs READ_CONTACTS). */
    static String contactName(Context context, String number) {
        if (number == null || number.trim().isEmpty()) return "";
        if (Build.VERSION.SDK_INT >= 23
                && context.checkSelfPermission(Manifest.permission.READ_CONTACTS)
                != PackageManager.PERMISSION_GRANTED) {
            return "";
        }
        Cursor cursor = null;
        try {
            Uri uri = Uri.withAppendedPath(
                    ContactsContract.PhoneLookup.CONTENT_FILTER_URI,
                    Uri.encode(number.trim()));
            cursor = context.getContentResolver().query(
                    uri,
                    new String[]{ContactsContract.PhoneLookup.DISPLAY_NAME},
                    null, null, null);
            if (cursor != null && cursor.moveToFirst()) {
                String name = cursor.getString(0);
                return name == null ? "" : name.trim();
            }
        } catch (RuntimeException ignored) {
            // Permission revoked between check and query, OEM provider, ...
        } finally {
            if (cursor != null) cursor.close();
        }
        return "";
    }
}

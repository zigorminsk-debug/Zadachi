package by.zadachi.calls;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.provider.CallLog;

/**
 * Identifies a just-finished call from Android's own call log.
 *
 * Used for calls outside the allowed Wi-Fi networks, where neither the
 * screening service nor a PHONE_STATE broadcast is guaranteed to expose
 * the number on every OEM build. The system dialer has normally written
 * the real number and cached contact name by the time the call ends.
 */
final class CallerIdLog {
    static final class Hit {
        final String number;
        final String name;
        final long date;

        Hit(String number, String name, long date) {
            this.number = number;
            this.name = name;
            this.date = date;
        }
    }

    private CallerIdLog() {}

    static Hit recentIncoming(Context context, long callStartedAtMillis) {
        Context app = context.getApplicationContext();
        if (Build.VERSION.SDK_INT >= 23
                && app.checkSelfPermission(Manifest.permission.READ_CALL_LOG)
                != PackageManager.PERMISSION_GRANTED) {
            return null;
        }
        Cursor cursor = null;
        try {
            String[] projection = {
                    CallLog.Calls.NUMBER,
                    CallLog.Calls.CACHED_NAME,
                    CallLog.Calls.DATE,
                    CallLog.Calls.TYPE,
            };
            cursor = app.getContentResolver().query(
                    CallLog.Calls.CONTENT_URI, projection, null, null,
                    CallLog.Calls.DATE + " DESC");
            if (cursor == null) return null;

            long earliest = callStartedAtMillis > 0
                    ? callStartedAtMillis - 120000L : 0L;
            int checked = 0;
            while (cursor.moveToNext() && checked++ < 16) {
                long date = cursor.getLong(2);
                if (earliest > 0 && date < earliest) break;
                int type = cursor.getInt(3);
                if (type != CallLog.Calls.INCOMING_TYPE
                        && type != CallLog.Calls.MISSED_TYPE
                        && type != CallLog.Calls.REJECTED_TYPE
                        && type != CallLog.Calls.BLOCKED_TYPE) {
                    continue;
                }
                String number = cleanNumber(cursor.getString(0));
                if (!usableNumber(number)) continue;
                String name = cursor.getString(1);
                name = name == null ? "" : name.trim();
                if (name.isEmpty()) name = CallReporter.contactName(app, number);
                return new Hit(number, name, date);
            }
        } catch (RuntimeException ignored) {
            // Provider/permission differences must never break call handling.
        } finally {
            if (cursor != null) cursor.close();
        }
        return null;
    }

    private static String cleanNumber(String raw) {
        return raw == null ? "" : Uri.decode(raw.trim());
    }

    private static boolean usableNumber(String number) {
        if (number == null) return false;
        String n = number.trim();
        // Many builds store withheld/unknown callers as -1, -2 or -3.
        if (n.isEmpty() || "-1".equals(n) || "-2".equals(n) || "-3".equals(n)) {
            return false;
        }
        int digits = 0;
        for (int i = 0; i < n.length(); i++) {
            if (Character.isDigit(n.charAt(i))) digits++;
        }
        return digits >= 3;
    }
}

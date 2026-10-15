package by.zadachi.calls;

import android.app.Activity;
import android.app.NotificationManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.widget.Toast;

/**
 * Trampoline that opens the system default dialer.
 *
 * Android 10+ often silently blocks startActivity() calls made directly
 * from a background/foreground service, which made PC-initiated calls
 * look as if "nothing happened". CallService therefore launches this
 * activity directly AND posts a full-screen/tappable notification with
 * the same intent: one of the two paths always reaches the user.
 */
public class DialActivity extends Activity {
    public static final String EXTRA_NUMBER = "number";
    public static final String EXTRA_NOTIFICATION_ID = "notificationId";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        handle(getIntent());
        finish();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handle(intent);
        finish();
    }

    private void handle(Intent intent) {
        if (intent == null) return;
        int notificationId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, 0);
        if (notificationId != 0) {
            NotificationManager nm = (NotificationManager)
                    getSystemService(NOTIFICATION_SERVICE);
            if (nm != null) nm.cancel(notificationId);
        }
        String number = intent.getStringExtra(EXTRA_NUMBER);
        if (number == null || number.trim().isEmpty()) return;
        try {
            Intent dial = new Intent(Intent.ACTION_DIAL,
                    Uri.parse("tel:" + Uri.encode(number.trim())));
            dial.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(dial); // default dialer; the user confirms the call
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, "На телефоне не найдена звонилка",
                    Toast.LENGTH_LONG).show();
        }
    }
}

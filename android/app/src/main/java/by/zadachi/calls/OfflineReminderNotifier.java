package by.zadachi.calls;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/** Notification shown after an incoming call outside the allowed Wi-Fi. */
final class OfflineReminderNotifier {
    private static final String CHANNEL = "zadachi_offline_calls";

    private OfflineReminderNotifier() {}

    static void show(Context context, OfflineReminders.Item item) {
        Context app = context.getApplicationContext();
        NotificationManager nm = (NotificationManager)
                app.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL, "Звонки вне сети", NotificationManager.IMPORTANCE_HIGH);
            channel.setDescription(
                    "Входящие вне разрешённой Wi-Fi: создать напоминание на ПК?");
            nm.createNotificationChannel(channel);
        }

        Intent remind = new Intent(app, OfflineReminderActionReceiver.class);
        remind.setAction(OfflineReminderActionReceiver.ACTION_REMIND);
        remind.putExtra(OfflineReminderActionReceiver.EXTRA_ID, item.id);
        PendingIntent remindPi = PendingIntent.getBroadcast(
                app, item.notificationId(), remind,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Intent dismiss = new Intent(app, OfflineReminderActionReceiver.class);
        dismiss.setAction(OfflineReminderActionReceiver.ACTION_DISMISS);
        dismiss.putExtra(OfflineReminderActionReceiver.EXTRA_ID, item.id);
        PendingIntent dismissPi = PendingIntent.getBroadcast(
                app, item.notificationId() + 100000, dismiss,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Intent open = new Intent(app, MainActivity.class);
        open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent openPi = PendingIntent.getActivity(
                app, item.notificationId(), open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        String caller = item.displayName();
        String text = caller + " • отправить напоминание на ПК, когда появится сеть?";
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(app, CHANNEL)
                : new Notification.Builder(app);
        Notification notification = builder
                .setContentTitle("Входящий звонок вне сети")
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text
                        + "\n\n«Напомнить»: через 5 минут после доставки на ПК, "
                        + "затем каждые 15 минут, пока задача не будет завершена."))
                .setSmallIcon(android.R.drawable.sym_call_missed)
                .setContentIntent(openPi)
                .setAutoCancel(false)
                .addAction(android.R.drawable.ic_menu_send, "Напомнить", remindPi)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel,
                        "Отклонить", dismissPi)
                .build();
        try {
            nm.notify(item.notificationId(), notification);
        } catch (RuntimeException ignored) {
            // E.g. notification permission revoked on Android 13+.
        }
    }

    static void cancel(Context context, long itemId) {
        NotificationManager nm = (NotificationManager) context
                .getApplicationContext().getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        OfflineReminders.Item item = OfflineReminders.find(context, itemId);
        int notificationId = item != null
                ? item.notificationId()
                : 30000 + (int) Math.abs(itemId % 50000L);
        nm.cancel(notificationId);
    }
}

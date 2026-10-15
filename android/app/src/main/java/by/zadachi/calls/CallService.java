package by.zadachi.calls;

import android.app.ActivityOptions;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ServiceInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.telephony.PhoneStateListener;
import android.telephony.TelephonyCallback;
import android.telephony.TelephonyManager;
import android.util.Log;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.lang.reflect.Method;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Active mode (only on allowed Wi-Fi):
 *  - listens for incoming calls and pushes them to the PC (popup there);
 *  - listens on TCP 39273: PC can ask to open the default dialer with a
 *    number ("click-to-call" from anywhere on the PC).
 * Outside allowed Wi-Fi WifiGate stops this service completely.
 *
 * Power: single blocked ServerSocket thread + system telephony callbacks.
 * A partial wakelock is taken only for max 15 s while actually delivering
 * a call event. No timers, no scans, no polling.
 */
public class CallService extends Service {
    private static final String CHANNEL = "zadachi_active";
    private ServerSocket server;
    private volatile boolean running;
    private ExecutorService io;
    private BroadcastReceiver phoneReceiver;
    private Object telephonyListener;
    private String lastRingingNumber = "";
    private boolean wasRinging = false;
    private boolean wasOffhook = false;
    private boolean incomingSent = false;
    private boolean incomingNumberSent = false;
    private static volatile boolean serviceRunning = false;

    /** Used by the settings screen to show that call tracking is really on. */
    static boolean isRunning() { return serviceRunning; }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onCreate() {
        super.onCreate();
        io = Executors.newCachedThreadPool();
        if (!startForegroundNotification()) {
            stopSelf();
            return;
        }
        registerPhoneReceiver();
        registerTelephonyListener();
        running = true;
        serviceRunning = true;
        io.execute(this::serverLoop);
        // An allowed network has (re)appeared: deliver any calls the
        // user approved with "Напомнить" while outside the network.
        OfflineReminderDelivery.flushAsync(getApplicationContext());
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // WifiGate re-starts an already running service when a network
        // returns or its periodic job runs; that is a delivery retry.
        OfflineReminderDelivery.flushAsync(getApplicationContext());
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        serviceRunning = false;
        unregisterTelephonyListener();
        if (phoneReceiver != null) {
            try { unregisterReceiver(phoneReceiver); } catch (Exception ignored) {}
        }
        try { if (server != null) server.close(); } catch (Exception ignored) {}
        if (io != null) io.shutdownNow();
        super.onDestroy();
    }

    // ------------------------------------------------------------ foreground
    private boolean startForegroundNotification() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26 && nm != null) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL, "Zadachi активна", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Работает в разрешённой Wi-Fi сети");
            nm.createNotificationChannel(ch);
        }
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n = (Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this))
                .setContentTitle("Zadachi активна")
                .setContentText("Сеть разрешена: звонки уходят на ПК")
                .setSmallIcon(android.R.drawable.sym_call_incoming)
                .setContentIntent(pi)
                .setOngoing(true)
                .build();
        try {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
            } else {
                startForeground(1, n);
            }
            return true;
        } catch (RuntimeException e) {
            // E.g. SecurityException if Android tightens FGS-type rules.
            // Stop this service instead of taking down the whole process.
            Log.e("Zadachi", "Unable to enter foreground mode", e);
            return false;
        }
    }

    // ------------------------------------------------------------ call events
    private void registerPhoneReceiver() {
        phoneReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context ctx, Intent intent) {
                String state = intent.getStringExtra(TelephonyManager.EXTRA_STATE);
                String number = intent.getStringExtra(TelephonyManager.EXTRA_INCOMING_NUMBER);
                int callState = TelephonyManager.CALL_STATE_IDLE;
                if (TelephonyManager.EXTRA_STATE_RINGING.equals(state)) {
                    callState = TelephonyManager.CALL_STATE_RINGING;
                } else if (TelephonyManager.EXTRA_STATE_OFFHOOK.equals(state)) {
                    callState = TelephonyManager.CALL_STATE_OFFHOOK;
                }
                handleCallState(callState, number);
            }
        };
        IntentFilter f = new IntentFilter(TelephonyManager.ACTION_PHONE_STATE_CHANGED);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(phoneReceiver, f, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(phoneReceiver, f);
        }
    }

    /**
     * Second, independent call-state source. Some OEM builds deliver the
     * PHONE_STATE broadcast without a number or not at all; the telephony
     * listener sees the same transitions, and events are de-duplicated.
     */
    @SuppressWarnings("deprecation")
    private void registerTelephonyListener() {
        try {
            TelephonyManager tm = (TelephonyManager) getSystemService(TELEPHONY_SERVICE);
            if (tm == null) return;
            if (Build.VERSION.SDK_INT >= 31) {
                CallStateCallback callback = new CallStateCallback();
                telephonyListener = callback;
                tm.registerTelephonyCallback(getMainExecutor(), callback);
            } else {
                PhoneStateListener listener = new PhoneStateListener() {
                    @Override
                    public void onCallStateChanged(int state, String phoneNumber) {
                        handleCallState(state, phoneNumber);
                    }
                };
                telephonyListener = listener;
                tm.listen(listener, PhoneStateListener.LISTEN_CALL_STATE);
            }
        } catch (Throwable e) {
            // Missing READ_PHONE_STATE, or TelephonyCallback unavailable on
            // an old Android build. The broadcast path may still work; the
            // settings screen asks the user for permissions.
            Log.e("Zadachi", "Unable to register telephony listener", e);
        }
    }

    private final class CallStateCallback extends TelephonyCallback
            implements TelephonyCallback.CallStateListener {
        @Override
        public void onCallStateChanged(int state) {
            // TelephonyCallback carries no number; the broadcast and the
            // call log permissions supply it. State alone is enough to
            // open the task window on the PC immediately.
            handleCallState(state, "");
        }
    }

    @SuppressWarnings("deprecation")
    private void unregisterTelephonyListener() {
        if (telephonyListener == null) return;
        try {
            TelephonyManager tm = (TelephonyManager) getSystemService(TELEPHONY_SERVICE);
            if (tm == null) return;
            if (Build.VERSION.SDK_INT >= 31
                    && telephonyListener instanceof TelephonyCallback) {
                tm.unregisterTelephonyCallback((TelephonyCallback) telephonyListener);
            } else if (telephonyListener instanceof PhoneStateListener) {
                tm.listen((PhoneStateListener) telephonyListener, PhoneStateListener.LISTEN_NONE);
            }
        } catch (Throwable ignored) {
        } finally {
            telephonyListener = null;
        }
    }

    private synchronized void handleCallState(int state, String number) {
        if (number != null && !number.trim().isEmpty()) {
            lastRingingNumber = number.trim();
        }
        if (state == TelephonyManager.CALL_STATE_RINGING) {
            wasRinging = true;
            if (!incomingSent) {
                incomingSent = true;
                incomingNumberSent = !lastRingingNumber.isEmpty();
                sendCallEvent("incoming", lastRingingNumber);
            } else if (!incomingNumberSent && !lastRingingNumber.isEmpty()) {
                // First event had a hidden/empty number; now it is known.
                incomingNumberSent = true;
                sendCallEvent("incoming", lastRingingNumber);
            }
        } else if (state == TelephonyManager.CALL_STATE_OFFHOOK) {
            wasOffhook = true;
        } else if (state == TelephonyManager.CALL_STATE_IDLE) {
            if (wasRinging && !wasOffhook) {
                sendCallEvent("missed", lastRingingNumber);
            } else if (wasOffhook) {
                sendCallEvent("ended", lastRingingNumber);
            }
            wasRinging = false;
            wasOffhook = false;
            incomingSent = false;
            incomingNumberSent = false;
            lastRingingNumber = "";
        }
    }

    private void sendCallEvent(String event, String number) {
        final String num = number == null ? "" : number;
        io.execute(() -> {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            PowerManager.WakeLock wl = pm != null
                    ? pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "zadachi:call") : null;
            if (wl != null) wl.acquire(20000);
            try {
                CallReporter.send(getApplicationContext(), event, num);
            } finally {
                if (wl != null && wl.isHeld()) wl.release();
            }
        });
    }

    // ------------------------------------------------------------ dial server
    private void serverLoop() {
        try {
            server = new ServerSocket(Net.PHONE_PORT);
            while (running) {
                Socket s = server.accept();
                io.execute(() -> handleClient(s));
            }
        } catch (Exception ignored) {
            // port busy or closed on destroy
        }
    }

    private void handleClient(Socket s) {
        try (Socket sock = s) {
            sock.setSoTimeout(5000);
            BufferedReader in = new BufferedReader(
                    new InputStreamReader(sock.getInputStream(), StandardCharsets.UTF_8));
            String line;
            StringBuilder headers = new StringBuilder();
            int contentLength = 0;
            String token = "";
            boolean isDial = false, isPing = false;
            while ((line = in.readLine()) != null && !line.isEmpty()) {
                headers.append(line).append('\n');
                String low = line.toLowerCase();
                if (low.startsWith("content-length:")) {
                    contentLength = Integer.parseInt(line.substring(15).trim());
                } else if (low.startsWith("x-zadachi-token:")) {
                    token = line.substring(16).trim();
                } else if (line.startsWith("POST /dial")) {
                    isDial = true;
                } else if (line.startsWith("POST /ping")) {
                    isPing = true;
                }
            }
            char[] buf = new char[contentLength];
            int read = 0;
            while (read < contentLength) {
                int n = in.read(buf, read, contentLength - read);
                if (n < 0) break;
                read += n;
            }
            String body = new String(buf, 0, read);
            Prefs prefs = new Prefs(getApplicationContext());
            boolean ok = prefs.token().isEmpty() || prefs.token().equals(token);
            String resp;
            int code = 200;
            if (!ok) {
                code = 403;
                resp = "{\"ok\":false,\"error\":\"bad token\"}";
            } else if (isPing) {
                resp = "{\"ok\":true,\"app\":\"Zadachi-Phone\"}";
            } else if (isDial) {
                String number = Net.extractJsonString(body, "number");
                if (number.isEmpty()) {
                    code = 400;
                    resp = "{\"ok\":false,\"error\":\"no number\"}";
                } else {
                    openDialer(number);
                    resp = "{\"ok\":true}";
                }
            } else {
                code = 404;
                resp = "{\"ok\":false}";
            }
            byte[] out = ("HTTP/1.1 " + code + " OK\r\n"
                    + "Content-Type: application/json\r\n"
                    + "Content-Length: " + resp.getBytes(StandardCharsets.UTF_8).length + "\r\n"
                    + "Connection: close\r\n\r\n" + resp).getBytes(StandardCharsets.UTF_8);
            OutputStream os = sock.getOutputStream();
            os.write(out);
            os.flush();
        } catch (Exception ignored) {}
    }

    private static final String DIAL_CHANNEL = "zadachi_outgoing";

    /**
     * Open the number in the phone's default dialer — directly, not as a
     * notification.
     *
     * Android (and MIUI in particular) silently blocks most activity
     * starts from background services. With the "display over other
     * apps" permission, Android explicitly allows this process to start
     * activities from the background (AOSP BAL_ALLOW_SAW_PERMISSION),
     * so DialActivity opens and launches the system dialer. Without it
     * we try a PendingIntent with the Android 14 background-start
     * opt-in. The old notification is only a delayed fallback: it is
     * posted solely when DialActivity demonstrably did not run — and
     * then tapping it still opens the dialer.
     */
    private void openDialer(String number) {
        int notificationId = 2000
                + (int) (System.currentTimeMillis() & 0x3fffffff);
        Intent trampoline = new Intent(this, DialActivity.class);
        trampoline.putExtra(DialActivity.EXTRA_NUMBER, number);
        trampoline.putExtra(DialActivity.EXTRA_NOTIFICATION_ID, notificationId);
        trampoline.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                | Intent.FLAG_ACTIVITY_CLEAR_TOP);

        boolean directTried = false;
        if (BackgroundLaunch.canDrawOverlays(this)) {
            // The reliable path: our process holds the SAW exemption.
            directTried = true;
            try {
                startActivity(trampoline);
            } catch (RuntimeException e) {
                Log.e("Zadachi", "Dialer launch with overlay exemption failed", e);
            }
        }
        boolean sent = false;
        if (!directTried && Build.VERSION.SDK_INT >= 34) {
            sent = sendWithBackgroundStartOptIn(trampoline, notificationId);
        }
        if (!directTried && !sent) {
            try {
                startActivity(trampoline);
            } catch (RuntimeException e) {
                Log.e("Zadachi", "Direct dialer launch blocked", e);
            }
        }

        // Delayed fallback: only if the trampoline never ran.
        PendingIntent tapIntent = PendingIntent.getActivity(
                this, notificationId, trampoline,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        new Handler(Looper.getMainLooper()).postDelayed(() -> {
            if (!DialActivity.handledRecently(number)) {
                postDialNotification(tapIntent, notificationId, number);
            }
        }, 900);
    }

    /**
     * PendingIntent.send() with the Android 14 BAL opt-in
     * (MODE_BACKGROUND_ACTIVITY_START_ALLOWED = 1) set for both the
     * creator and the sender side, via reflection where needed.
     */
    private boolean sendWithBackgroundStartOptIn(Intent intent, int requestCode) {
        try {
            ActivityOptions createOpts = ActivityOptions.makeBasic();
            setBackgroundStartMode(createOpts,
                    "setPendingIntentCreatorBackgroundActivityStartMode");
            setBackgroundStartMode(createOpts,
                    "setPendingIntentBackgroundActivityStartMode");
            PendingIntent pi = PendingIntent.getActivity(
                    this, requestCode, intent,
                    PendingIntent.FLAG_CANCEL_CURRENT | PendingIntent.FLAG_IMMUTABLE,
                    createOpts.toBundle());

            ActivityOptions sendOpts = ActivityOptions.makeBasic();
            setBackgroundStartMode(sendOpts,
                    "setPendingIntentSenderBackgroundActivityStartMode");
            setBackgroundStartMode(sendOpts,
                    "setPendingIntentBackgroundActivityStartMode");
            Bundle sendBundle = sendOpts.toBundle();
            pi.send(this, 0, null, null, null, null, sendBundle);
            return true;
        } catch (Throwable e) {
            Log.e("Zadachi", "BAL opt-in dialer launch failed", e);
            return false;
        }
    }

    private void setBackgroundStartMode(ActivityOptions options, String method) {
        try {
            Method m = options.getClass().getMethod(method, int.class);
            m.invoke(options, 1); // MODE_BACKGROUND_ACTIVITY_START_ALLOWED
        } catch (Exception ignored) {
            // Method renamed/absent on this build; the other name may work.
        }
    }

    private void postDialNotification(PendingIntent pi, int notificationId,
                                      String number) {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm == null) return;
        try {
            Notification.Builder builder;
            if (Build.VERSION.SDK_INT >= 26) {
                NotificationChannel ch = new NotificationChannel(
                        DIAL_CHANNEL, "Исходящие звонки с ПК",
                        NotificationManager.IMPORTANCE_HIGH);
                ch.setDescription("Звонки, запущенные кликом на компьютере");
                nm.createNotificationChannel(ch);
                builder = new Notification.Builder(this, DIAL_CHANNEL);
            } else {
                builder = new Notification.Builder(this)
                        .setPriority(Notification.PRIORITY_HIGH);
            }
            boolean miuiBlocked = MiuiUtils.isMiui()
                    && !MiuiUtils.backgroundPopupAllowed(this);
            Notification notification = builder
                    .setContentTitle(miuiBlocked
                            ? "Нажмите, чтобы открыть звонилку (MIUI блокирует автозапуск)"
                            : "Нажмите, чтобы набрать номер")
                    .setContentText(number)
                    .setSmallIcon(android.R.drawable.sym_call_outgoing)
                    .setContentIntent(pi)
                    .setFullScreenIntent(pi, true)
                    .setAutoCancel(true)
                    .build();
            nm.notify(notificationId, notification);
        } catch (RuntimeException e) {
            Log.e("Zadachi", "Unable to post dial notification", e);
        }
    }
}

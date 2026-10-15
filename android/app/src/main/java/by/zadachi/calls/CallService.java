package by.zadachi.calls;

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
import android.os.IBinder;
import android.os.PowerManager;
import android.telephony.TelephonyManager;
import android.util.Log;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
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
    private String lastRingingNumber = "";
    private boolean wasRinging = false;
    private boolean wasOffhook = false;

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
        running = true;
        io.execute(this::serverLoop);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
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
                if (TelephonyManager.EXTRA_STATE_RINGING.equals(state)) {
                    wasRinging = true;
                    if (number != null && !number.isEmpty()) lastRingingNumber = number;
                    sendCallEvent("incoming", lastRingingNumber);
                } else if (TelephonyManager.EXTRA_STATE_OFFHOOK.equals(state)) {
                    wasOffhook = true;
                } else if (TelephonyManager.EXTRA_STATE_IDLE.equals(state)) {
                    if (wasRinging && !wasOffhook) {
                        sendCallEvent("missed", lastRingingNumber);
                    } else if (wasOffhook) {
                        sendCallEvent("ended", lastRingingNumber);
                    }
                    wasRinging = false;
                    wasOffhook = false;
                }
            }
        };
        IntentFilter f = new IntentFilter(TelephonyManager.ACTION_PHONE_STATE_CHANGED);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(phoneReceiver, f, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(phoneReceiver, f);
        }
    }

    private void sendCallEvent(String event, String number) {
        final String num = number == null ? "" : number;
        io.execute(() -> {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            PowerManager.WakeLock wl = pm != null
                    ? pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "zadachi:call") : null;
            if (wl != null) wl.acquire(15000);
            try {
                Prefs prefs = new Prefs(getApplicationContext());
                String ip = prefs.pcIp();
                if (ip.isEmpty()) {
                    ip = Net.discoverPcIp();
                    if (!ip.isEmpty()) {
                        prefs.setPcIp(ip);
                        // port might come in discovery reply; default is fixed
                        prefs.setPcPort(39272);
                    }
                }
                if (ip.isEmpty()) return;
                String json = "{\"event\":\"" + Net.jsonEscape(event) + "\""
                        + ",\"number\":\"" + Net.jsonEscape(num) + "\""
                        + ",\"device\":\"" + Net.jsonEscape(Net.deviceName()) + "\""
                        + ",\"ts\":" + System.currentTimeMillis() + "}";
                Net.postJson("http://" + ip + ":" + prefs.pcPort() + "/api/call",
                        json, prefs.token(), 4000);
            } catch (Exception ignored) {
                // PC offline: nothing to do, never retry in a loop (battery).
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

    private void openDialer(String number) {
        try {
            Intent dial = new Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(number)));
            dial.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(dial); // default system dialer, user confirms the call
        } catch (Exception ignored) {}
    }
}

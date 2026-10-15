package by.zadachi.calls;

import android.content.Context;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** LAN helpers: SSID, PC discovery (UDP), tiny JSON POST. SDK only. */
final class Net {
    static final int UDP_PORT = 39271;
    static final int PHONE_PORT = 39273;
    static final String DISCOVERY = "ZADACHI_DISCOVER_PC";

    private Net() {}

    static String currentSsid(Context c) {
        try {
            WifiManager wm = (WifiManager) c.getApplicationContext()
                    .getSystemService(Context.WIFI_SERVICE);
            if (wm == null) return "";
            WifiInfo info = wm.getConnectionInfo();
            if (info == null) return "";
            String ssid = info.getSSID();
            if (ssid == null) return "";
            return ssid.replaceAll("^\"|\"$", "");
        } catch (Exception e) {
            return "";
        }
    }

    static String jsonEscape(String s) {
        return s == null ? "" : s.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\n", "\\n").replace("\r", "\\r");
    }

    static String extractJsonString(String json, String key) {
        if (json == null) return "";
        Matcher m = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]*)\"")
                .matcher(json);
        return m.find() ? m.group(1) : "";
    }

    static int extractJsonInt(String json, String key, int def) {
        if (json == null) return def;
        Matcher m = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*(\\d+)")
                .matcher(json);
        return m.find() ? Integer.parseInt(m.group(1)) : def;
    }

    /** POST JSON with pairing token; returns response body. Throws on error. */
    static String postJson(String urlStr, String json, String token, int timeoutMs)
            throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(urlStr).openConnection();
        conn.setConnectTimeout(timeoutMs);
        conn.setReadTimeout(timeoutMs);
        conn.setRequestMethod("POST");
        conn.setRequestProperty("Content-Type", "application/json");
        if (token != null && !token.isEmpty()) {
            conn.setRequestProperty("X-Zadachi-Token", token);
        }
        conn.setDoOutput(true);
        byte[] body = json.getBytes(StandardCharsets.UTF_8);
        try (OutputStream os = conn.getOutputStream()) {
            os.write(body);
        }
        int code = conn.getResponseCode();
        InputStream is = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
        String resp = readAll(is);
        conn.disconnect();
        if (code >= 400) throw new Exception("HTTP " + code + ": " + resp);
        return resp;
    }

    private static String readAll(InputStream is) throws Exception {
        if (is == null) return "";
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        int n;
        while ((n = is.read(buf)) > 0) bos.write(buf, 0, n);
        return bos.toString("UTF-8");
    }

    /**
     * Broadcast discovery of the PC. Returns PC IP or "".
     * One short UDP exchange — no scanning loops (battery friendly).
     */
    static String discoverPcIp() {
        DatagramSocket socket = null;
        try {
            socket = new DatagramSocket();
            socket.setBroadcast(true);
            socket.setSoTimeout(2500);
            byte[] msg = DISCOVERY.getBytes(StandardCharsets.UTF_8);
            DatagramPacket packet = new DatagramPacket(msg, msg.length,
                    InetAddress.getByName("255.255.255.255"), UDP_PORT);
            socket.send(packet);
            byte[] buf = new byte[1024];
            DatagramPacket reply = new DatagramPacket(buf, buf.length);
            socket.receive(reply);
            return reply.getAddress().getHostAddress();
        } catch (Exception e) {
            return "";
        } finally {
            if (socket != null) socket.close();
        }
    }

    static String deviceName() {
        String m = Build.MANUFACTURER + " " + Build.MODEL;
        return m.trim();
    }
}

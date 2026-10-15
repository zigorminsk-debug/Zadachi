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

    static final class PcEndpoint {
        final String ip;
        final int port;

        PcEndpoint(String ip, int port) {
            this.ip = ip;
            this.port = port;
        }
    }

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
        conn.setFixedLengthStreamingMode(body.length);
        try (OutputStream os = conn.getOutputStream()) {
            os.write(body);
        }
        int code = conn.getResponseCode();
        InputStream is = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
        String resp = readAll(is);
        conn.disconnect();
        if (code >= 400) throw new Exception(friendlyHttpError(code, resp));
        return resp;
    }

    /** GET text/JSON; throws a user-readable exception on HTTP errors. */
    static String get(String urlStr, int timeoutMs) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(urlStr).openConnection();
        conn.setConnectTimeout(timeoutMs);
        conn.setReadTimeout(timeoutMs);
        conn.setRequestMethod("GET");
        int code = conn.getResponseCode();
        InputStream is = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
        String resp = readAll(is);
        conn.disconnect();
        if (code >= 400) throw new Exception(friendlyHttpError(code, resp));
        return resp;
    }

    static String friendlyHttpError(int code, String resp) {
        String body = resp == null ? "" : resp.trim();
        if (body.startsWith("<") || body.contains("<html")) {
            // Our PC API always answers JSON. HTML means some other HTTP
            // server (router, another program) sits on this IP/port.
            return "HTTP " + code + " — по этому адресу отвечает не Zadachi, "
                    + "а другой сервер. Проверьте IP ПК или нажмите «Найти ПК автоматически»";
        }
        if (body.length() > 180) body = body.substring(0, 180) + "…";
        return "HTTP " + code + (body.isEmpty() ? "" : ": " + body);
    }

    /** True only if http://ip:port/api/health answers as Zadachi-PC. */
    static boolean isZadachiPc(String ip, int port) {
        if (ip == null || ip.trim().isEmpty()) return false;
        try {
            String body = get("http://" + ip.trim() + ":" + port + "/api/health", 2500);
            return "Zadachi-PC".equals(extractJsonString(body, "app"));
        } catch (Exception e) {
            return false;
        }
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
     * Broadcast discovery of the PC. Returns endpoint or null.
     * One short UDP exchange — no scanning loops (battery friendly).
     * The reply is validated (must identify itself as Zadachi-PC) and its
     * advertised port is honoured.
     */
    static PcEndpoint discoverPc() {
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
            String body = new String(reply.getData(), reply.getOffset(),
                    reply.getLength(), StandardCharsets.UTF_8);
            if (!"Zadachi-PC".equals(extractJsonString(body, "app"))) return null;
            int port = extractJsonInt(body, "port", 39272);
            return new PcEndpoint(reply.getAddress().getHostAddress(), port);
        } catch (Exception e) {
            return null;
        } finally {
            if (socket != null) socket.close();
        }
    }

    /** Backwards-compatible: just the IP, or "" when not found. */
    static String discoverPcIp() {
        PcEndpoint pc = discoverPc();
        return pc == null ? "" : pc.ip;
    }

    static String deviceName() {
        String m = Build.MANUFACTURER + " " + Build.MODEL;
        return m.trim();
    }
}

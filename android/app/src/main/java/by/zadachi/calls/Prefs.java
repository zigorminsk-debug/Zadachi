package by.zadachi.calls;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.HashSet;
import java.util.Set;

final class Prefs {
    private static final String FILE = "zadachi";
    private final SharedPreferences sp;

    Prefs(Context c) {
        sp = c.getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    boolean enabled() { return sp.getBoolean("enabled", true); }
    void setEnabled(boolean v) { sp.edit().putBoolean("enabled", v).apply(); }

    String token() { return sp.getString("token", ""); }
    void setToken(String t) { sp.edit().putString("token", t == null ? "" : t.trim()).apply(); }

    String pcIp() { return sp.getString("pcIp", ""); }
    void setPcIp(String ip) { sp.edit().putString("pcIp", ip == null ? "" : ip.trim()).apply(); }

    int pcPort() { return sp.getInt("pcPort", 39272); }
    void setPcPort(int p) { sp.edit().putInt("pcPort", p).apply(); }

    /** Human-readable diagnostics of the most recent real call event. */
    String lastEvent() { return sp.getString("lastEvent", ""); }
    void setLastEvent(String text) {
        sp.edit().putString("lastEvent", text == null ? "" : text).apply();
    }

    Set<String> allowedSsids() {
        return new HashSet<>(sp.getStringSet("ssids", new HashSet<>()));
    }

    void addSsid(String ssid) {
        Set<String> s = allowedSsids();
        s.add(ssid);
        sp.edit().putStringSet("ssids", s).apply();
    }

    void removeSsid(String ssid) {
        Set<String> s = allowedSsids();
        s.remove(ssid);
        sp.edit().putStringSet("ssids", s).apply();
    }

    /** Master gate: app setting ON and current SSID in the white-list. */
    boolean isActiveHere(String ssid) {
        return enabled() && ssid != null && !ssid.isEmpty()
                && !"<unknown ssid>".equals(ssid) && allowedSsids().contains(ssid);
    }
}

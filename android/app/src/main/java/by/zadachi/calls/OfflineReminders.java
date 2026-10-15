package by.zadachi.calls;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * Calls received while the phone is outside every allowed Wi-Fi network.
 *
 * Such calls cannot be reported to the PC immediately. Zadachi remembers
 * the call locally; when it ends, the user chooses whether it should be
 * queued. Queued items survive reboots/app restarts and are delivered
 * automatically as soon as an allowed network (and the PC) is reachable.
 */
final class OfflineReminders {
    private static final String FILE = "zadachi_offline";
    private static final String KEY_ACTIVE = "active";
    private static final String KEY_ITEMS = "items";

    static final class Item {
        final long id;
        String number;
        String name;
        final long callTimestamp;
        boolean queued;

        Item(long id, String number, String name, long callTimestamp, boolean queued) {
            this.id = id;
            this.number = number == null ? "" : number;
            this.name = name == null ? "" : name;
            this.callTimestamp = callTimestamp;
            this.queued = queued;
        }

        int notificationId() {
            return 30000 + (int) Math.abs(id % 50000L);
        }

        String displayName() {
            if (!name.trim().isEmpty()) return name.trim();
            if (!number.trim().isEmpty()) return number.trim();
            return "Номер скрыт";
        }
    }

    private OfflineReminders() {}

    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext()
                .getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    private static JSONObject toJson(Item item) {
        JSONObject obj = new JSONObject();
        try {
            obj.put("id", item.id);
            obj.put("number", item.number);
            obj.put("name", item.name);
            obj.put("ts", item.callTimestamp);
            obj.put("queued", item.queued);
        } catch (JSONException ignored) {
            // Building this fixed-shape object cannot fail in practice.
        }
        return obj;
    }

    private static Item fromJson(JSONObject obj) {
        if (obj == null) return null;
        long id = obj.optLong("id", 0L);
        long ts = obj.optLong("ts", id);
        if (id <= 0 && ts > 0) id = ts;
        if (id <= 0) return null;
        return new Item(id, obj.optString("number", ""),
                obj.optString("name", ""), ts, obj.optBoolean("queued", false));
    }

    static synchronized Item rememberActive(Context context, String number,
                                            String name, long timestamp) {
        SharedPreferences sp = prefs(context);
        long ts = timestamp > 0 ? timestamp : System.currentTimeMillis();
        Item active = active(context);
        if (active != null && Math.abs(active.callTimestamp - ts) < 5 * 60_000L) {
            boolean changed = false;
            if ((active.number == null || active.number.trim().isEmpty())
                    && number != null && !number.trim().isEmpty()) {
                active.number = number.trim();
                changed = true;
            }
            if ((active.name == null || active.name.trim().isEmpty())
                    && name != null && !name.trim().isEmpty()) {
                active.name = name.trim();
                changed = true;
            }
            if (changed) {
                sp.edit().putString(KEY_ACTIVE, toJson(active).toString()).apply();
            }
            return active;
        }
        Item item = new Item(ts, clean(number), clean(name), ts, false);
        sp.edit().putString(KEY_ACTIVE, toJson(item).toString()).apply();
        return item;
    }

    static synchronized Item active(Context context) {
        String raw = prefs(context).getString(KEY_ACTIVE, "");
        if (raw == null || raw.trim().isEmpty()) return null;
        try {
            return fromJson(new JSONObject(raw));
        } catch (JSONException e) {
            return null;
        }
    }

    static synchronized void updateActive(Context context, String number, String name) {
        Item active = active(context);
        if (active == null) return;
        if (number != null && !number.trim().isEmpty()) active.number = number.trim();
        if (name != null && !name.trim().isEmpty()) active.name = name.trim();
        prefs(context).edit()
                .putString(KEY_ACTIVE, toJson(active).toString()).apply();
    }

    static synchronized void clearActive(Context context) {
        prefs(context).edit().remove(KEY_ACTIVE).apply();
    }

    /** Moves the currently tracked call to the awaiting-decision list. */
    static synchronized Item finishActive(Context context) {
        Item active = active(context);
        if (active == null) return null;
        List<Item> items = items(context);
        for (int i = 0; i < items.size(); i++) {
            if (items.get(i).id == active.id) {
                items.set(i, active);
                saveItems(context, items);
                clearActive(context);
                return active;
            }
        }
        items.add(active);
        saveItems(context, items);
        clearActive(context);
        return active;
    }

    static synchronized List<Item> items(Context context) {
        List<Item> result = new ArrayList<>();
        String raw = prefs(context).getString(KEY_ITEMS, "[]");
        try {
            JSONArray array = new JSONArray(raw == null ? "[]" : raw);
            for (int i = 0; i < array.length(); i++) {
                Item item = fromJson(array.optJSONObject(i));
                if (item != null) result.add(item);
            }
        } catch (JSONException ignored) {
            // A corrupted queue must not break call handling.
        }
        return result;
    }

    static synchronized List<Item> queuedItems(Context context) {
        List<Item> result = new ArrayList<>();
        for (Item item : items(context)) {
            if (item.queued) result.add(item);
        }
        return result;
    }

    static synchronized Item find(Context context, long id) {
        for (Item item : items(context)) {
            if (item.id == id) return item;
        }
        return null;
    }

    static synchronized boolean markQueued(Context context, long id) {
        List<Item> items = items(context);
        for (Item item : items) {
            if (item.id == id) {
                item.queued = true;
                saveItems(context, items);
                return true;
            }
        }
        return false;
    }

    static synchronized void remove(Context context, long id) {
        List<Item> items = items(context);
        List<Item> kept = new ArrayList<>();
        for (Item item : items) {
            if (item.id != id) kept.add(item);
        }
        saveItems(context, kept);
    }

    /**
     * A call may still be delivered by the ordinary active path (for
     * example CallService was already running even though the SSID could
     * not be read at screening time). In that case an unqueued offline
     * copy must not also ask the user. Returns cancelled item IDs.
     */
    static synchronized List<Long> suppressDelivered(Context context, String number) {
        List<Long> cancelled = new ArrayList<>();
        String delivered = digits(number);
        Item active = active(context);
        if (active != null && sameNumber(active.number, delivered)) {
            cancelled.add(active.id);
            clearActive(context);
        }
        List<Item> kept = new ArrayList<>();
        for (Item item : items(context)) {
            if (!item.queued && sameNumber(item.number, delivered)) {
                cancelled.add(item.id);
            } else {
                kept.add(item);
            }
        }
        saveItems(context, kept);
        return cancelled;
    }

    private static boolean sameNumber(String stored, String deliveredDigits) {
        if (deliveredDigits == null || deliveredDigits.isEmpty()) return false;
        String existing = digits(stored);
        if (existing.isEmpty()) return false;
        if (existing.equals(deliveredDigits)) return true;
        String shorter = existing.length() < deliveredDigits.length()
                ? existing : deliveredDigits;
        String longer = existing.length() < deliveredDigits.length()
                ? deliveredDigits : existing;
        return shorter.length() >= 9 && longer.endsWith(shorter);
    }

    private static String digits(String value) {
        if (value == null) return "";
        StringBuilder result = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (Character.isDigit(c)) result.append(c);
        }
        return result.toString();
    }

    static synchronized String summary(Context context) {
        int waiting = 0;
        int queued = 0;
        for (Item item : items(context)) {
            if (item.queued) queued++;
            else waiting++;
        }
        if (waiting == 0 && queued == 0) return "";
        StringBuilder text = new StringBuilder("Звонки вне сети: ");
        if (waiting > 0) text.append("ждут решения — ").append(waiting);
        if (waiting > 0 && queued > 0) text.append(", ");
        if (queued > 0) text.append("к отправке на ПК — ").append(queued);
        return text.toString();
    }

    private static void saveItems(Context context, List<Item> items) {
        JSONArray array = new JSONArray();
        for (Item item : items) array.put(toJson(item));
        prefs(context).edit().putString(KEY_ITEMS, array.toString()).apply();
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}

package by.zadachi.calls;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.Executors;

/**
 * Settings screen (built in code — no layout deps):
 *  - white-list of Wi-Fi networks where the app is active;
 *  - PC pairing (token + IP / auto-discovery);
 *  - permissions & battery-optimization status.
 */
public class MainActivity extends Activity {
    private Prefs prefs;
    private TextView statusView;
    private LinearLayout ssidList;
    private EditText tokenInput;
    private EditText pcIpInput;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = new Prefs(this);
        buildUi();
        WifiGate.schedule(this);
        WifiGate.evaluate(this);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshStatus();
        rebuildSsidList();
        WifiGate.evaluate(this);
    }

    // ------------------------------------------------------------------- UI
    private TextView label(String text, float size, boolean bold) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(size);
        if (bold) t.setTypeface(t.getTypeface(), android.graphics.Typeface.BOLD);
        t.setPadding(0, 16, 0, 6);
        return t;
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(32, 32, 32, 32);

        root.addView(label("Zadachi — звонки на ПК", 20, true));
        statusView = label("", 14, false);
        root.addView(statusView);

        Switch enabled = new Switch(this);
        enabled.setText("Работа программы включена");
        enabled.setChecked(prefs.enabled());
        enabled.setOnCheckedChangeListener((b, v) -> {
            prefs.setEnabled(v);
            WifiGate.evaluate(this);
            refreshStatus();
        });
        root.addView(enabled);

        root.addView(label("Wi-Fi сети, где программа активна", 16, true));
        root.addView(label("Вне этих сетей сервис полностью остановлен "
                + "и батарею не расходует (проверка — системным JobScheduler).", 13, false));
        Button addCurrent = new Button(this);
        addCurrent.setText("Разрешить текущую сеть");
        addCurrent.setOnClickListener(v -> {
            String ssid = Net.currentSsid(this);
            if (ssid.isEmpty() || "<unknown ssid>".equals(ssid)) {
                toast("Не вижу SSID. Выдайте разрешение на геолокацию и включите Wi-Fi.");
                requestPermissions();
                return;
            }
            prefs.addSsid(ssid);
            rebuildSsidList();
            WifiGate.evaluate(this);
            refreshStatus();
        });
        root.addView(addCurrent);
        ssidList = new LinearLayout(this);
        ssidList.setOrientation(LinearLayout.VERTICAL);
        root.addView(ssidList);

        root.addView(label("Сопряжение с ПК", 16, true));
        root.addView(label("Токен (показан в Windows-приложении):", 13, false));
        tokenInput = new EditText(this);
        tokenInput.setSingleLine(true);
        tokenInput.setText(prefs.token());
        root.addView(tokenInput);

        root.addView(label("IP компьютера (можно пустым — найдётся сам):", 13, false));
        pcIpInput = new EditText(this);
        pcIpInput.setSingleLine(true);
        pcIpInput.setText(prefs.pcIp());
        root.addView(pcIpInput);

        Button save = new Button(this);
        save.setText("Сохранить сопряжение");
        save.setOnClickListener(v -> {
            prefs.setToken(tokenInput.getText().toString());
            prefs.setPcIp(pcIpInput.getText().toString());
            toast("Сохранено");
            WifiGate.evaluate(this);
        });
        root.addView(save);

        Button discover = new Button(this);
        discover.setText("Найти ПК автоматически");
        discover.setOnClickListener(v -> Executors.newSingleThreadExecutor().execute(() -> {
            String ip = Net.discoverPcIp();
            runOnUiThread(() -> {
                if (ip.isEmpty()) {
                    toast("ПК не найден. Одна Wi-Fi сеть? Zadachi запущена на ПК?");
                } else {
                    prefs.setPcIp(ip);
                    pcIpInput.setText(ip);
                    toast("Найден ПК: " + ip);
                }
            });
        }));
        root.addView(discover);

        Button test = new Button(this);
        test.setText("Тест: тестовый звонок на ПК");
        test.setOnClickListener(v -> Executors.newSingleThreadExecutor().execute(() -> {
            String ip = prefs.pcIp().isEmpty() ? Net.discoverPcIp() : prefs.pcIp();
            if (!ip.isEmpty()) prefs.setPcIp(ip);
            String err = "";
            try {
                if (ip.isEmpty()) throw new Exception("ПК не найден");
                String json = "{\"event\":\"incoming\",\"number\":\"+375290000000\""
                        + ",\"device\":\"test\"}";
                Net.postJson("http://" + ip + ":" + prefs.pcPort() + "/api/call",
                        json, prefs.token(), 4000);
            } catch (Exception e) {
                err = e.getMessage();
            }
            final String msg = err.isEmpty()
                    ? "Тест отправлен — на ПК должно всплыть окно" : "Ошибка: " + err;
            runOnUiThread(() -> toast(msg));
        }));
        root.addView(test);

        root.addView(label("Разрешения и батарея", 16, true));
        Button perms = new Button(this);
        perms.setText("Выдать разрешения (звонки, Wi-Fi, уведомления)");
        perms.setOnClickListener(v -> requestPermissions());
        root.addView(perms);

        Button battery = new Button(this);
        battery.setText("Исключение из оптимизации батареи");
        battery.setOnClickListener(v -> {
            try {
                Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:" + getPackageName()));
                startActivity(i);
            } catch (Exception e) {
                toast("Откройте вручную: Настройки → Приложения → Zadachi → Батарея");
            }
        });
        root.addView(battery);

        root.addView(label("В активном режиме расход минимален: нет опросов и сканирований, "
                + "wakelock только на отправку звонка (≤15 c). Вне разрешённых сетей — 0%.", 13, false));

        android.widget.ScrollView scroll = new android.widget.ScrollView(this);
        scroll.addView(root, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(scroll);
    }

    private void rebuildSsidList() {
        if (ssidList == null) return;
        ssidList.removeAllViews();
        Set<String> ssids = new TreeSet<>(prefs.allowedSsids());
        if (ssids.isEmpty()) {
            ssidList.addView(label("— пока пусто: подключитесь к домашней/офисной сети и нажмите кнопку выше", 13, false));
        }
        for (String ssid : ssids) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            TextView t = new TextView(this);
            t.setText("• " + ssid);
            t.setTextSize(15);
            row.addView(t, new LinearLayout.LayoutParams(0,
                    ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            Button del = new Button(this);
            del.setText("Удалить");
            del.setOnClickListener(v -> {
                prefs.removeSsid(ssid);
                rebuildSsidList();
                WifiGate.evaluate(this);
                refreshStatus();
            });
            row.addView(del);
            ssidList.addView(row);
        }
    }

    private void refreshStatus() {
        if (statusView == null) return;
        String ssid = Net.currentSsid(this);
        boolean active = prefs.isActiveHere(ssid);
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        boolean noOpt = pm != null && pm.isIgnoringBatteryOptimizations(getPackageName());
        statusView.setText("Текущая сеть: " + (ssid.isEmpty() ? "—" : ssid)
                + "\nСтатус: " + (!prefs.enabled() ? "выключено"
                        : active ? "АКТИВНО (звонки уходят на ПК)" : "в фоне, сеть не разрешена")
                + "\nПК: " + (prefs.pcIp().isEmpty() ? "не найден" : prefs.pcIp())
                + "\nОптимизация батареи: " + (noOpt ? "отключена ✔" : "включена (рекомендуется отключить)"));
    }

    // ------------------------------------------------------------- permissions
    private void requestPermissions() {
        java.util.List<String> list = new java.util.ArrayList<>();
        list.add(Manifest.permission.READ_PHONE_STATE);
        list.add(Manifest.permission.ACCESS_FINE_LOCATION);
        if (Build.VERSION.SDK_INT >= 31) list.add(Manifest.permission.READ_PHONE_NUMBERS);
        if (Build.VERSION.SDK_INT >= 33) list.add(Manifest.permission.POST_NOTIFICATIONS);
        String[] arr = list.toArray(new String[0]);
        if (Build.VERSION.SDK_INT >= 23) requestPermissions(arr, 101);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        super.onRequestPermissionsResult(code, perms, results);
        boolean phoneOk = false;
        for (int i = 0; i < perms.length; i++) {
            if (Manifest.permission.READ_PHONE_STATE.equals(perms[i])
                    && results[i] == PackageManager.PERMISSION_GRANTED) phoneOk = true;
        }
        if (!phoneOk) toast("Без разрешения на звонки приложение не увидит входящие вызовы.");
        refreshStatus();
        WifiGate.evaluate(this);
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_LONG).show();
    }
}

package by.zadachi.calls;

import android.Manifest;
import android.app.Activity;
import android.app.role.RoleManager;
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
        // Without these permissions Android will not report real incoming
        // calls/numbers (a manual test call would still work). Ask once.
        if (Build.VERSION.SDK_INT >= 23
                && (!hasCallPermissions() || !hasPermission(Manifest.permission.READ_CONTACTS))) {
            requestPermissions();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        rebuildSsidList();
        WifiGate.evaluate(this);
        refreshStatus();
        if (statusView != null) {
            statusView.postDelayed(this::refreshStatus, 700);
        }
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
            syncPairingFromFields();
            toast("Сохранено");
            WifiGate.evaluate(this);
        });
        root.addView(save);

        Button discover = new Button(this);
        discover.setText("Найти ПК автоматически");
        discover.setOnClickListener(v -> Executors.newSingleThreadExecutor().execute(() -> {
            Net.PcEndpoint pc = Net.discoverPc();
            runOnUiThread(() -> {
                if (pc == null) {
                    toast("ПК не найден. Одна Wi-Fi сеть? Zadachi запущена на ПК?");
                } else {
                    prefs.setPcIp(pc.ip);
                    prefs.setPcPort(pc.port);
                    pcIpInput.setText(pc.ip);
                    toast("Найден ПК: " + pc.ip + ":" + pc.port);
                }
            });
        }));
        root.addView(discover);

        Button test = new Button(this);
        test.setText("Тест: тестовый звонок на ПК");
        test.setOnClickListener(v -> {
            // What the user sees in the fields is what the test must use,
            // even if they forgot to press "Сохранить сопряжение".
            syncPairingFromFields();
            if (prefs.allowedSsids().isEmpty()) {
                toast("Внимание: список разрешённых Wi-Fi пуст — реальные звонки "
                        + "отслеживаться не будут. Нажмите «Разрешить текущую сеть». "
                        + "Тест всё равно отправляю.");
            }
            if (prefs.token().isEmpty()) {
                toast("Токен не заполнен: если на ПК задан токен, ПК отклонит тест. "
                        + "Продолжаю проверку…");
            }
            Executors.newSingleThreadExecutor().execute(() -> {
                String ip = prefs.pcIp();
                int port = prefs.pcPort();
                String note = "";
                String err = "";
                try {
                    boolean verified = Net.isZadachiPc(ip, port);
                    if (!verified) {
                        Net.PcEndpoint found = Net.discoverPc();
                        if (found != null && Net.isZadachiPc(found.ip, found.port)) {
                            ip = found.ip;
                            port = found.port;
                            prefs.setPcIp(ip);
                            prefs.setPcPort(port);
                            verified = true;
                            note = "Нашёл настоящий ПК: " + ip + ":" + port + ". ";
                        }
                    }
                    if (ip.isEmpty()) throw new Exception("ПК не найден. Запустите Zadachi на ПК и нажмите «Найти ПК автоматически»");
                    if (!verified) {
                        throw new Exception("По адресу " + ip + ":" + port
                                + " отвечает не Zadachi (или Zadachi на ПК не запущена). "
                                + "Проверьте IP либо воспользуйтесь автопоиском.");
                    }
                    String json = "{\"event\":\"incoming\",\"number\":\"+375290000000\""
                            + ",\"device\":\"test\"}";
                    Net.postJson("http://" + ip + ":" + port + "/api/call",
                            json, prefs.token(), 4000);
                } catch (Exception e) {
                    err = e.getMessage();
                }
                final String usedIp = ip;
                final String msg = err.isEmpty()
                        ? note + "Тест отправлен на " + usedIp + " — на ПК должно всплыть окно"
                        : "Ошибка: " + err;
                runOnUiThread(() -> {
                    if (!usedIp.isEmpty()) pcIpInput.setText(usedIp);
                    toast(msg);
                });
            });
        });
        root.addView(test);

        root.addView(label("Разрешения и батарея", 16, true));
        Button perms = new Button(this);
        perms.setText("Выдать разрешения (звонки, журнал, контакты, Wi-Fi)");
        perms.setOnClickListener(v -> requestPermissions());
        root.addView(perms);

        Button callerId = new Button(this);
        callerId.setText("Включить определение номера входящих");
        callerId.setOnClickListener(v -> requestCallerIdRole());
        root.addView(callerId);
        root.addView(label("Если номер входящего не определяется: включите Zadachi как "
                + "«АОН и спам» кнопкой выше — тогда Android будет показывать приложению "
                + "настоящий номер звонящего. Звонки при этом не блокируются.", 13, false));

        Button overlay = new Button(this);
        overlay.setText("Разрешить открытие звонилки поверх других приложений");
        overlay.setOnClickListener(v -> BackgroundLaunch.openOverlaySettings(this));
        root.addView(overlay);
        root.addView(label("Нужно, чтобы по клику на ПК штатная звонилка открывалась "
                + "сама, а не уведомлением. Zadachi не рисует окна поверх приложений — "
                + "Android просто требует это разрешение для запуска из фона.", 13, false));

        if (MiuiUtils.isMiui()) {
            Button popup = new Button(this);
            popup.setText("Разрешить открытие звонилки из фона (Xiaomi/MIUI)");
            popup.setOnClickListener(v -> MiuiUtils.openBackgroundPopupSettings(this));
            root.addView(popup);
            root.addView(label("На Xiaomi без разрешения «Всплывающие окна в фоне» "
                    + "(«Отображать всплывающие окна, когда приложение работает в фоне») "
                    + "звонилка не сможет открываться сама — вместо неё будет уведомление. "
                    + "Включите разрешение кнопкой выше.", 13, false));
        }

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

    /** Persist whatever is currently typed in the pairing fields. */
    private void syncPairingFromFields() {
        if (tokenInput != null) prefs.setToken(tokenInput.getText().toString());
        if (pcIpInput != null) prefs.setPcIp(pcIpInput.getText().toString());
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
        String serviceState;
        if (!prefs.enabled()) serviceState = "выключено";
        else if (!active) serviceState = "в фоне, сеть не разрешена";
        else if (CallService.isRunning()) serviceState = "АКТИВНО (сервис запущен)";
        else serviceState = "сеть разрешена, но сервис НЕ запущен";
        statusView.setText("Текущая сеть: " + (ssid.isEmpty() ? "—" : ssid)
                + "\nСтатус: " + serviceState
                + "\nДоступ к звонкам и номерам: "
                        + (hasCallPermissions() ? "есть ✔" : "НЕТ — приложение не увидит реальный входящий номер")
                + "\nОпределение номера (АОН и спам): "
                        + (hasCallerIdRole() ? "включено ✔"
                                : "выключено — включите кнопкой ниже, иначе номер может быть скрыт")
                + "\nИмя из контактов телефона: "
                        + (hasPermission(Manifest.permission.READ_CONTACTS) ? "доступно ✔" : "нет доступа")
                + "\nОткрытие звонилки поверх приложений: "
                        + (BackgroundLaunch.canDrawOverlays(this)
                                ? "разрешено ✔"
                                : "НЕ разрешено — вместо звонилки будет уведомление, включите кнопкой ниже")
                + (!MiuiUtils.isMiui() ? ""
                        : "\nОткрытие звонилки из фона (MIUI): "
                                + (MiuiUtils.backgroundPopupAllowed(this)
                                        ? "разрешено ✔"
                                        : "ЗАПРЕЩЕНО — звонилка не откроется сама, включите кнопкой ниже"))
                + "\nПК: " + (prefs.pcIp().isEmpty() ? "не найден" : prefs.pcIp())
                + (prefs.lastEvent().isEmpty() ? ""
                        : "\nПоследний звонок: " + prefs.lastEvent())
                + "\nОптимизация батареи: " + (noOpt ? "отключена ✔" : "включена (рекомендуется отключить)"));
    }

    // ------------------------------------------------------------- permissions
    private boolean hasPermission(String permission) {
        return Build.VERSION.SDK_INT < 23
                || checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    /** Phone state + call log are both needed to see a real caller's number. */
    private boolean hasCallPermissions() {
        return hasPermission(Manifest.permission.READ_PHONE_STATE)
                && hasPermission(Manifest.permission.READ_CALL_LOG);
    }

    /** The system "Caller ID & spam" role exposes the real incoming number. */
    private boolean hasCallerIdRole() {
        if (Build.VERSION.SDK_INT < 29) return false;
        try {
            RoleManager rm = (RoleManager) getSystemService(ROLE_SERVICE);
            return rm != null && rm.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING)
                    && rm.isRoleHeld(RoleManager.ROLE_CALL_SCREENING);
        } catch (RuntimeException e) {
            return false;
        }
    }

    private void requestCallerIdRole() {
        if (Build.VERSION.SDK_INT < 29) {
            toast("На этой версии Android номер дают разрешения «Телефон» и «Журнал звонков».");
            return;
        }
        if (hasCallerIdRole()) {
            toast("Определение номера уже включено ✔");
            return;
        }
        try {
            RoleManager rm = (RoleManager) getSystemService(ROLE_SERVICE);
            if (rm == null || !rm.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING)) {
                toast("Роль «АОН и спам» недоступна на этом устройстве. "
                        + "Включите Zadachi вручную: Настройки → Приложения → "
                        + "Приложения по умолчанию → АОН и спам.");
                return;
            }
            Intent intent = rm.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING);
            startActivityForResult(intent, 102);
        } catch (RuntimeException e) {
            toast("Откройте вручную: Настройки → Приложения → Приложения по умолчанию "
                    + "→ АОН и спам → Zadachi");
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == 102) {
            toast(hasCallerIdRole()
                    ? "Определение номера включено ✔"
                    : "Роль не выдана: без неё номер входящего может не определяться");
            refreshStatus();
            WifiGate.evaluate(this);
        }
    }

    private void requestPermissions() {
        java.util.List<String> list = new java.util.ArrayList<>();
        list.add(Manifest.permission.READ_PHONE_STATE);
        // Since Android 9 the caller's number is hidden without this.
        list.add(Manifest.permission.READ_CALL_LOG);
        // Caller name from the phone's address book.
        list.add(Manifest.permission.READ_CONTACTS);
        list.add(Manifest.permission.ACCESS_FINE_LOCATION);
        if (Build.VERSION.SDK_INT >= 31) list.add(Manifest.permission.READ_PHONE_NUMBERS);
        if (Build.VERSION.SDK_INT >= 33) list.add(Manifest.permission.POST_NOTIFICATIONS);
        String[] arr = list.toArray(new String[0]);
        if (Build.VERSION.SDK_INT >= 23) requestPermissions(arr, 101);
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        super.onRequestPermissionsResult(code, perms, results);
        if (!hasCallPermissions()) {
            toast("Без разрешений «Телефон» и «Журнал звонков» Android не покажет "
                    + "реальный входящий звонок и его номер.");
        }
        refreshStatus();
        WifiGate.evaluate(this);
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_LONG).show();
    }
}

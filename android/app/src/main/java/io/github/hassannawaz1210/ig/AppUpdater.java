package io.github.hassannawaz1210.ig;

import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.lang.ref.WeakReference;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** One updater per process; downloads never occupy the Python/backend worker. */
final class AppUpdater {
    interface Listener {
        void onUpdateStatus(String message, String action, boolean canDismiss);
        void onUpdateInfo(String message);
        void onUpdateInstall(File apk);
    }
    private enum Phase { IDLE, CHECKING, AVAILABLE, DOWNLOADING, READY, VERIFYING, FAILED }
    private static AppUpdater instance;
    private static final long DAY = 24L * 60 * 60 * 1000;
    private final Context context;
    private final SharedPreferences preferences;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private WeakReference<Listener> listener = new WeakReference<>(null);
    private volatile boolean cancelled;
    private volatile HttpURLConnection activeConnection;
    private Phase phase = Phase.IDLE;
    private UpdatePackage update;
    private File downloaded;
    private boolean visible;
    private boolean checkDismissed;
    private String message = "";

    static synchronized AppUpdater get(Context context) {
        if (instance == null) instance = new AppUpdater(context.getApplicationContext());
        return instance;
    }

    private AppUpdater(Context context) {
        this.context = context;
        preferences = context.getSharedPreferences("app-updates", Context.MODE_PRIVATE);
        // An install permission screen may outlive this process. Restore only
        // metadata here; the file is verified again on the worker before use.
        try {
            String ready = preferences.getString("ready", null);
            if (ready != null) {
                UpdatePackage candidate = UpdatePackage.parse(ready);
                File apk = new File(directory(context), candidate.sha256 + ".apk");
                if (candidate.versionCode > installedVersion() && apk.isFile()) {
                    update = candidate;
                    downloaded = apk;
                    phase = Phase.READY;
                    message = "Update ready to install";
                } else {
                    preferences.edit().remove("ready").remove("awaitingPermission").apply();
                    if (apk.exists()) apk.delete();
                }
            }
        } catch (Exception error) {
            preferences.edit().remove("ready").remove("awaitingPermission").apply();
        }
    }

    static File directory(Context context) { return new File(context.getFilesDir(), "app-updates"); }

    void attach(Listener target) {
        listener = new WeakReference<>(target);
        render();
        if (preferences.getBoolean("awaitingPermission", false) && canInstall()) permissionReturned();
    }

    void detach(Listener target) {
        if (listener.get() == target) listener.clear();
    }

    private long installedVersion() throws Exception {
        return UpdatePackage.code(context.getPackageManager().getPackageInfo(context.getPackageName(), 0));
    }

    static long failureDelay(int failures) {
        return Math.min(DAY, (15L * 60 * 1000) << Math.min(Math.max(0, failures - 1), 7));
    }

    void check(boolean manual) {
        if (phase == Phase.CHECKING || phase == Phase.DOWNLOADING || phase == Phase.VERIFYING) {
            if (manual) { checkDismissed = false; visible = true; render(); }
            return;
        }
        if (phase == Phase.READY) {
            if (manual) { visible = true; render(); }
            return;
        }
        long now = System.currentTimeMillis();
        if (!manual && (context.getSharedPreferences("ig", Context.MODE_PRIVATE).getBoolean("disableUpdateChecks", false)
                || now < preferences.getLong("nextCheck", 0))) return;
        // Prevent restart loops while a request is in flight, including process death.
        preferences.edit().putLong("nextCheck", now + failureDelay(1)).apply();
        phase = Phase.CHECKING;
        checkDismissed = false;
        visible = manual;
        message = "Checking for updates…";
        render();
        worker.execute(() -> {
            try {
                UpdatePackage candidate;
                HttpURLConnection connection = open(UpdatePackage.MANIFEST_URL);
                try (InputStream input = connection.getInputStream()) {
                    candidate = UpdatePackage.parse(new String(UpdatePackage.readManifest(input), StandardCharsets.UTF_8));
                } finally { connection.disconnect(); }
                long installed = installedVersion();
                preferences.edit().putInt("failures", 0).putLong("nextCheck", System.currentTimeMillis() + DAY).apply();
                main.post(() -> {
                    if (candidate.versionCode <= installed) {
                        update = null;
                        phase = Phase.IDLE;
                        visible = false;
                        render();
                        if (manual && !checkDismissed) info("ig is up to date");
                        return;
                    }
                    update = candidate;
                    phase = Phase.AVAILABLE;
                    message = "ig " + candidate.versionName + " is available";
                    visible = !checkDismissed && (manual || candidate.versionCode > preferences.getLong("noticedVersion", 0));
                    render();
                });
            } catch (Exception error) {
                int failures = Math.min(8, preferences.getInt("failures", 0) + 1);
                preferences.edit().putInt("failures", failures)
                        .putLong("nextCheck", System.currentTimeMillis() + failureDelay(failures)).apply();
                main.post(() -> {
                    phase = Phase.FAILED;
                    message = "Couldn’t check for updates";
                    visible = manual && !checkDismissed;
                    render();
                });
            }
        });
    }

    void action() {
        if (phase == Phase.AVAILABLE) download();
        else if (phase == Phase.READY) install();
        else if (phase == Phase.DOWNLOADING) cancelDownload();
        else if (phase == Phase.FAILED) check(true);
    }

    void dismiss() {
        if (phase == Phase.DOWNLOADING || phase == Phase.VERIFYING) return;
        visible = false;
        if (phase == Phase.CHECKING) checkDismissed = true;
        render();
    }

    private void download() {
        if (update == null) return;
        UpdatePackage candidate = update;
        phase = Phase.DOWNLOADING;
        visible = true;
        cancelled = false;
        message = "Downloading update…";
        render();
        worker.execute(() -> {
            File partial = new File(directory(context), "download.part.apk");
            HttpURLConnection connection = null;
            try {
                File directory = directory(context);
                if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("No update directory");
                connection = open(candidate.apkUrl);
                activeConnection = connection;
                long expected = connection.getContentLengthLong();
                if (expected >= 0 && expected != candidate.size) throw new IOException("Unexpected APK size");
                long deadline = SystemClock.elapsedRealtime() + 5 * 60 * 1000;
                int[] lastPercent = {-1};
                try (InputStream input = connection.getInputStream(); FileOutputStream output = new FileOutputStream(partial)) {
                    candidate.copyVerified(input, output, () -> cancelled || SystemClock.elapsedRealtime() > deadline, bytes -> {
                        int percent = (int) (bytes * 100 / candidate.size);
                        if (percent != lastPercent[0]) {
                            lastPercent[0] = percent;
                            main.post(() -> {
                                if (phase == Phase.DOWNLOADING) {
                                    message = "Downloading update… " + percent + "%";
                                    render();
                                }
                            });
                        }
                    });
                    output.getFD().sync();
                }
                candidate.verifyArchive(context, partial);
                if (cancelled) throw new IOException("Update cancelled");
                File ready = new File(directory, candidate.sha256 + ".apk");
                if (ready.exists() && !ready.delete()) throw new IOException("Could not replace staged update");
                if (!partial.renameTo(ready)) throw new IOException("Could not stage update");
                preferences.edit().putString("ready", candidate.json()).apply();
                main.post(() -> {
                    downloaded = ready;
                    phase = Phase.READY;
                    message = "Update ready to install";
                    render();
                    // Download was explicitly requested. Continue to Android's
                    // confirmation only while the activity is still visible.
                    if (listener.get() != null) install();
                });
            } catch (Exception error) {
                partial.delete();
                main.post(() -> {
                    phase = Phase.AVAILABLE;
                    message = cancelled ? "Update download cancelled" : "Couldn’t download or verify update";
                    render();
                });
            } finally {
                activeConnection = null;
                if (connection != null) connection.disconnect();
            }
        });
    }

    private void cancelDownload() {
        cancelled = true;
        HttpURLConnection connection = activeConnection;
        if (connection != null) workerCancel(connection);
        message = "Cancelling download…";
        render();
    }

    private static void workerCancel(HttpURLConnection connection) {
        // disconnect may block until the current network read finishes.
        Thread cancel = new Thread(connection::disconnect, "ig-update-cancel");
        cancel.setDaemon(true);
        cancel.start();
    }

    private void install() {
        if (phase != Phase.READY || update == null || downloaded == null) return;
        UpdatePackage candidate = update;
        File apk = downloaded;
        phase = Phase.VERIFYING;
        visible = true;
        message = "Verifying update…";
        render();
        worker.execute(() -> {
            try {
                candidate.verifyFile(context, apk);
                main.post(() -> {
                    phase = Phase.READY;
                    message = "Update ready to install";
                    render();
                    Listener target = listener.get();
                    if (target != null) target.onUpdateInstall(apk);
                });
            } catch (Exception error) {
                preferences.edit().remove("ready").remove("awaitingPermission").apply();
                apk.delete();
                main.post(() -> {
                    downloaded = null;
                    phase = Phase.AVAILABLE;
                    message = "Update verification failed · download again";
                    render();
                });
            }
        });
    }

    boolean canInstall() {
        return Build.VERSION.SDK_INT < 26 || context.getPackageManager().canRequestPackageInstalls();
    }

    void awaitingPermission() { preferences.edit().putBoolean("awaitingPermission", true).apply(); }

    void permissionReturned() {
        if (!preferences.getBoolean("awaitingPermission", false)) return;
        preferences.edit().remove("awaitingPermission").apply();
        if (canInstall()) install();
        else installFinished("Allow updates from ig to install");
    }

    void installFinished(String result) {
        preferences.edit().remove("awaitingPermission").apply();
        message = result;
        visible = true;
        render();
    }

    void installSucceeded() {
        preferences.edit().remove("ready").remove("awaitingPermission").apply();
        if (downloaded != null) downloaded.delete();
        downloaded = null;
        update = null;
        phase = Phase.IDLE;
        visible = false;
        render();
        info("Update installed");
    }

    static Intent installIntent(Context context, File apk) throws IOException {
        File directory = directory(context).getCanonicalFile();
        if (!apk.getCanonicalFile().getParentFile().equals(directory) || !apk.getName().matches("[a-f0-9]{64}\\.apk"))
            throw new IOException("Invalid staged update");
        Uri uri = new Uri.Builder().scheme("content").authority(context.getPackageName() + ".updates")
                .appendPath(apk.getName()).build();
        return new Intent(Intent.ACTION_INSTALL_PACKAGE).setDataAndType(uri, "application/vnd.android.package-archive")
                .setClipData(ClipData.newRawUri("ig update", uri))
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                .putExtra(Intent.EXTRA_RETURN_RESULT, true);
    }

    private void info(String text) {
        Listener target = listener.get();
        if (target != null) target.onUpdateInfo(text);
    }

    void render() {
        Listener target = listener.get();
        if (target == null) return;
        if (visible && update != null) preferences.edit().putLong("noticedVersion", update.versionCode).apply();
        String action = phase == Phase.AVAILABLE ? "Update" : phase == Phase.DOWNLOADING ? "Cancel"
                : phase == Phase.READY ? "Install" : phase == Phase.FAILED ? "Retry" : "";
        target.onUpdateStatus(visible ? message : "", action,
                phase != Phase.DOWNLOADING && phase != Phase.VERIFYING);
    }

    private static HttpURLConnection open(String address) throws IOException {
        URL url = new URL(address);
        for (int redirects = 0; redirects <= 5; redirects++) {
            if (!UpdatePackage.allowedRedirect(url.toString())) throw new IOException("Untrusted update address");
            HttpURLConnection connection = (HttpURLConnection) url.openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(15000);
            connection.setReadTimeout(30000);
            connection.setRequestProperty("User-Agent", "ig-android-updater");
            connection.setRequestProperty("Accept-Encoding", "identity");
            try {
                int status = connection.getResponseCode();
                if (status == 200) return connection;
                if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
                    String location = connection.getHeaderField("Location");
                    if (location == null) throw new IOException("Missing update redirect");
                    url = new URL(url, location);
                    connection.disconnect();
                    continue;
                }
                throw new IOException("Update request failed");
            } catch (IOException error) {
                connection.disconnect();
                throw error;
            }
        }
        throw new IOException("Too many update redirects");
    }
}

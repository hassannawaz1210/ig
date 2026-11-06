package io.github.hassannawaz1210.ig;

import android.content.Context;
import android.content.ContextWrapper;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.ProviderInfo;
import android.net.Uri;
import android.os.Build;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Collections;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.junit.Assert.*;

/** Offline updater checks. Never contacts GitHub or installs an APK. */
@RunWith(AndroidJUnit4.class)
public class UpdateTest {
    private static final String APK_URL = "https://github.com/hassannawaz1210/ig/releases/download/android-build-123456-1/ig-android.apk";
    private static final byte[] PAYLOAD = "a signed APK is validated separately".getBytes(StandardCharsets.UTF_8);

    private interface Checked { void run() throws Exception; }
    private static void rejected(Checked action) throws Exception {
        try { action.run(); fail("Invalid update was accepted"); }
        catch (java.io.IOException | org.json.JSONException expected) { }
    }

    private static JSONObject metadata() throws Exception {
        return new JSONObject().put("version_code", 1234).put("version_name", "0.5.0-preview.2.1")
                .put("apk_url", APK_URL).put("sha256", UpdatePackage.hex(MessageDigest.getInstance("SHA-256").digest(PAYLOAD)))
                .put("size", PAYLOAD.length);
    }

    private static final class Status implements AppUpdater.Listener {
        final String expected;
        final CountDownLatch ready = new CountDownLatch(1);
        final CountDownLatch hidden = new CountDownLatch(1);
        volatile String message, action;
        volatile boolean dismissible;
        Status(String expected) { this.expected = expected; }
        @Override public void onUpdateStatus(String message, String action, boolean dismissible) {
            this.message = message; this.action = action; this.dismissible = dismissible;
            if (expected.equals(message)) ready.countDown();
            if (message.isEmpty() && ready.getCount() == 0) hidden.countDown();
        }
        @Override public void onUpdateInfo(String message) { fail("Check result should stay visible, not use a toast"); }
        @Override public void onUpdateInstall(File file) { fail("A check must not install anything"); }
    }

    private static AppUpdater testUpdater(AppUpdater.ManifestSource source) {
        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        SharedPreferences preferences = app.getSharedPreferences("update-ui-test-" + UUID.randomUUID(), Context.MODE_PRIVATE);
        Context context = new ContextWrapper(app) {
            @Override public SharedPreferences getSharedPreferences(String name, int mode) {
                return preferences;
            }
        };
        return new AppUpdater(context, source, preferences);
    }

    @Test public void manualSuccessExpiresWhileActionableResultsCanBeDismissed() throws Exception {
        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        long current = UpdatePackage.code(app.getPackageManager().getPackageInfo(app.getPackageName(), 0));
        UpdatePackage same = new UpdatePackage(current, "current", APK_URL, "0000000000000000000000000000000000000000000000000000000000000000", 1);
        UpdatePackage newer = new UpdatePackage(current + 1, "next", APK_URL, "0000000000000000000000000000000000000000000000000000000000000000", 1);
        AppUpdater.ManifestSource[] sources = {() -> same, () -> newer, () -> { throw new java.io.IOException("offline"); }};
        String[] messages = {"ig is up to date", "ig next is available", "Couldn’t check for updates"};
        String[] actions = {"", "Update", "Retry"};
        for (int i = 0; i < sources.length; i++) {
            AppUpdater updater = testUpdater(sources[i]);
            Status status = new Status(messages[i]);
            InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> { updater.attach(status); updater.check(true); });
            assertTrue("No visible check result", status.ready.await(10, TimeUnit.SECONDS));
            assertEquals(actions[i], status.action);
            if (i == 0) {
                assertFalse("Success must not offer a dismiss icon", status.dismissible);
                assertTrue("Success notice did not expire", status.hidden.await(5, TimeUnit.SECONDS));
            } else {
                assertTrue(status.dismissible);
                InstrumentationRegistry.getInstrumentation().runOnMainSync(updater::dismiss);
            }
            assertEquals("", status.message);
        }
    }

    @Test public void manualTapDuringAutomaticCheckStillGetsVisibleSuccess() throws Exception {
        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        long current = UpdatePackage.code(app.getPackageManager().getPackageInfo(app.getPackageName(), 0));
        CountDownLatch started = new CountDownLatch(1), finish = new CountDownLatch(1);
        AppUpdater updater = testUpdater(() -> {
            started.countDown();
            if (!finish.await(10, TimeUnit.SECONDS)) throw new java.io.IOException("test timed out");
            return new UpdatePackage(current, "current", APK_URL, "0000000000000000000000000000000000000000000000000000000000000000", 1);
        });
        Status status = new Status("ig is up to date");
        InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> { updater.attach(status); updater.check(false); });
        assertTrue(started.await(10, TimeUnit.SECONDS));
        InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> updater.check(true));
        finish.countDown();
        assertTrue("Manual check lost its result", status.ready.await(10, TimeUnit.SECONDS));
        assertFalse(status.dismissible);
    }

    @Test public void manifestIsStrictAndBounded() throws Exception {
        UpdatePackage update = UpdatePackage.parse(metadata().toString());
        assertEquals(1234, update.versionCode);
        assertEquals(APK_URL, update.apkUrl);
        assertEquals(PAYLOAD.length, update.size);
        assertEquals(update.sha256, UpdatePackage.parse(update.json()).sha256);
        for (Object version : new Object[] {0, -1, 1.5, true, "1234", ((long) Integer.MAX_VALUE) + 1})
            rejected(() -> UpdatePackage.parse(metadata().put("version_code", version).toString()));
        for (Object size : new Object[] {0, -1, UpdatePackage.MAX_BYTES + 1, "30", 1.5})
            rejected(() -> UpdatePackage.parse(metadata().put("size", size).toString()));
        rejected(() -> UpdatePackage.parse(metadata().put("sha256", "1234").toString()));
        rejected(() -> UpdatePackage.parse(metadata().put("unexpected", "value").toString()));
        rejected(() -> UpdatePackage.parse(metadata().put("version_name", "bad\nname").toString()));
        rejected(() -> UpdatePackage.readManifest(new ByteArrayInputStream(new byte[UpdatePackage.MAX_MANIFEST_BYTES + 1])));
    }

    @Test public void onlyRepositoryReleaseAssetsAndHttpsRedirectsAreAccepted() throws Exception {
        assertTrue(UpdatePackage.allowedApkUrl(APK_URL));
        assertTrue(UpdatePackage.allowedRedirect(UpdatePackage.MANIFEST_URL));
        assertTrue(UpdatePackage.allowedRedirect("https://release-assets.githubusercontent.com/github-production-release-asset/123/a?sig=abc"));
        assertTrue(UpdatePackage.allowedRedirect("https://objects.githubusercontent.com/github-production-release-asset/123/a"));
        for (String url : new String[] {
                APK_URL.replace("https:", "http:"), APK_URL.replace("/ig/", "/other/"),
                APK_URL.replace("android-build-123456-1", "main"), APK_URL + "?other=1", APK_URL + "#fragment",
                APK_URL.replace("github.com", "github.com.evil.example"), APK_URL.replace("github.com", "user@github.com"),
                APK_URL.replace("ig-android.apk", "../ig-android.apk"),
                "https://release-assets.githubusercontent.com/ig.apk", "file:///tmp/update.apk"}) {
            assertFalse(url, UpdatePackage.allowedApkUrl(url));
            rejected(() -> UpdatePackage.parse(metadata().put("apk_url", url).toString()));
        }
        for (String url : new String[] {"http://release-assets.githubusercontent.com/file", "https://evil.example/file",
                "https://github.com/other/ig/releases/download/android-build-1-1/a.apk", "https://github.com:444/file",
                "https://release-assets.githubusercontent.com.evil.example/file"}) assertFalse(url, UpdatePackage.allowedRedirect(url));
    }

    @Test public void downloadedBytesMustMatchSizeAndHashAndCanBeCancelled() throws Exception {
        UpdatePackage update = UpdatePackage.parse(metadata().toString());
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        update.copyVerified(new ByteArrayInputStream(PAYLOAD), output, () -> false, ignored -> {});
        assertArrayEquals(PAYLOAD, output.toByteArray());
        rejected(() -> update.copyVerified(new ByteArrayInputStream(new byte[PAYLOAD.length]), null, () -> false, ignored -> {}));
        rejected(() -> update.copyVerified(new ByteArrayInputStream(new byte[PAYLOAD.length + 1]), null, () -> false, ignored -> {}));
        rejected(() -> update.copyVerified(new ByteArrayInputStream(new byte[PAYLOAD.length - 1]), null, () -> false, ignored -> {}));
        rejected(() -> update.copyVerified(new ByteArrayInputStream(PAYLOAD), null, () -> true, ignored -> {}));
        assertEquals(15L * 60 * 1000, AppUpdater.failureDelay(1));
        assertEquals(30L * 60 * 1000, AppUpdater.failureDelay(2));
        assertEquals(24L * 60 * 60 * 1000, AppUpdater.failureDelay(8));
    }

    @Test public void apkIdentityRequiresNewerVersionAndSameCurrentSigner() throws Exception {
        UpdatePackage update = UpdatePackage.parse(metadata().toString());
        String packageName = "io.github.hassannawaz1210.ig";
        Set<String> signer = Collections.singleton("installed signer");
        update.verifyIdentity(packageName, 1234, update.versionName, signer, packageName, 1233, signer);
        rejected(() -> update.verifyIdentity("other.app", 1234, update.versionName, signer, packageName, 1233, signer));
        rejected(() -> update.verifyIdentity(packageName, 1233, update.versionName, signer, packageName, 1233, signer));
        rejected(() -> update.verifyIdentity(packageName, 1234, update.versionName, signer, packageName, 1234, signer));
        rejected(() -> update.verifyIdentity(packageName, 1234, update.versionName, signer, packageName, 1235, signer));
        rejected(() -> update.verifyIdentity(packageName, 1234, "wrong name", signer, packageName, 1233, signer));
        rejected(() -> update.verifyIdentity(packageName, 1234, update.versionName, Collections.singleton("other signer"), packageName, 1233, signer));
        rejected(() -> update.verifyIdentity(packageName, 1234, update.versionName, Collections.emptySet(), packageName, 1233, signer));

        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        int flags = Build.VERSION.SDK_INT >= 28 ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        PackageInfo installed = app.getPackageManager().getPackageInfo(app.getPackageName(), flags);
        PackageInfo archive = app.getPackageManager().getPackageArchiveInfo(app.getApplicationInfo().sourceDir, flags);
        assertNotNull(archive);
        assertFalse(UpdatePackage.signers(installed).isEmpty());
        assertEquals(UpdatePackage.signers(installed), UpdatePackage.signers(archive));
        // A correctly signed current APK is still not a newer update.
        UpdatePackage current = new UpdatePackage(UpdatePackage.code(installed), installed.versionName, APK_URL, update.sha256, update.size);
        rejected(() -> current.verifyArchive(app, new File(app.getApplicationInfo().sourceDir)));
    }

    @Test public void completeDownloadReportsVersionMismatchAfterPrivateApkVerification() throws Exception {
        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        PackageInfo installed = app.getPackageManager().getPackageInfo(app.getPackageName(), 0);
        File source = new File(app.getApplicationInfo().sourceDir);
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new java.io.FileInputStream(source)) {
            byte[] buffer = new byte[64 * 1024];
            int count;
            while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
        }
        // A valid real signed APK, but a deliberately incorrect advertised version.
        // Exercise the actual worker through download, checksum, fsync and the
        // Android parser against the app-private staged file, without network.
        UpdatePackage candidate = new UpdatePackage(UpdatePackage.code(installed) + 1,
                installed.versionName, APK_URL, UpdatePackage.hex(digest.digest()), source.length());
        SharedPreferences preferences = app.getSharedPreferences("update-stage-test-" + UUID.randomUUID(), Context.MODE_PRIVATE);
        AppUpdater updater = new AppUpdater(app, () -> candidate, preferences, address ->
                new java.net.HttpURLConnection(new java.net.URL(address)) {
                    @Override public void connect() { }
                    @Override public void disconnect() { }
                    @Override public boolean usingProxy() { return false; }
                    @Override public long getContentLengthLong() { return source.length(); }
                    @Override public InputStream getInputStream() throws java.io.IOException {
                        return new java.io.FileInputStream(source);
                    }
                });
        CountDownLatch available = new CountDownLatch(1), complete = new CountDownLatch(1), failed = new CountDownLatch(1);
        java.util.concurrent.atomic.AtomicReference<String> error = new java.util.concurrent.atomic.AtomicReference<>();
        AppUpdater.Listener listener = new AppUpdater.Listener() {
            @Override public void onUpdateStatus(String message, String action, boolean dismissible) {
                if (message.endsWith(" is available")) available.countDown();
                if (message.equals("Downloading update… 100%")) complete.countDown();
                if (action.equals("Update") && !message.endsWith(" is available")) {
                    error.set(message);
                    failed.countDown();
                }
            }
            @Override public void onUpdateInfo(String message) { fail(message); }
            @Override public void onUpdateInstall(File apk) { fail("Mismatched APK reached installation"); }
        };
        InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> { updater.attach(listener); updater.check(true); });
        assertTrue(available.await(10, TimeUnit.SECONDS));
        InstrumentationRegistry.getInstrumentation().runOnMainSync(updater::action);
        assertTrue("APK was not completely downloaded", complete.await(60, TimeUnit.SECONDS));
        assertTrue("APK verification did not finish", failed.await(30, TimeUnit.SECONDS));
        assertEquals("APK version does not match the release", error.get());
        assertFalse(new File(AppUpdater.directory(app), "download.part.apk").exists());
        assertFalse(preferences.contains("ready"));
        InstrumentationRegistry.getInstrumentation().runOnMainSync(() -> updater.detach(listener));
    }

    @Test public void installIntentGrantsOnlyReadAccessToOnePrivateApk() throws Exception {
        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        File directory = AppUpdater.directory(app);
        assertTrue(directory.isDirectory() || directory.mkdirs());
        String filename = UUID.randomUUID().toString().replace("-", "") + UUID.randomUUID().toString().replace("-", "") + ".apk";
        File file = new File(directory, filename);
        try (FileOutputStream output = new FileOutputStream(file)) { output.write(PAYLOAD); }
        Uri uri = null;
        try {
            Intent intent = AppUpdater.installIntent(app, file);
            uri = intent.getData();
            assertEquals(Intent.ACTION_INSTALL_PACKAGE, intent.getAction());
            assertEquals("content", uri.getScheme());
            assertEquals(app.getPackageName() + ".updates", uri.getAuthority());
            assertEquals("application/vnd.android.package-archive", intent.getType());
            assertTrue(intent.getBooleanExtra(Intent.EXTRA_RETURN_RESULT, false));
            assertEquals(Intent.FLAG_GRANT_READ_URI_PERMISSION, intent.getFlags());
            assertEquals(uri, intent.getClipData().getItemAt(0).getUri());
            ProviderInfo provider = app.getPackageManager().resolveContentProvider(uri.getAuthority(), 0);
            assertNotNull(provider);
            assertFalse(provider.exported);
            assertTrue(provider.grantUriPermissions);
            try (InputStream input = app.getContentResolver().openInputStream(uri)) {
                assertNotNull(input);
                assertEquals(PAYLOAD[0], input.read());
            }
            Context test = InstrumentationRegistry.getInstrumentation().getContext();
            assertFalse(test.getContentResolver().call(FollowingFixtureProvider.FILE, "canReadUpdate", uri.toString(), null).getBoolean("readable"));
            app.grantUriPermission(test.getPackageName(), uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
            assertTrue(test.getContentResolver().call(FollowingFixtureProvider.FILE, "canReadUpdate", uri.toString(), null).getBoolean("readable"));
            final Uri selected = uri;
            rejected(() -> app.getContentResolver().openOutputStream(selected, "w"));
            rejected(() -> AppUpdater.installIntent(app, new File(app.getFilesDir(), filename)));
            rejected(() -> AppUpdater.installIntent(app, new File(directory, "download.part")));
        } finally {
            if (uri != null) app.revokeUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION);
            file.delete();
        }
    }
}

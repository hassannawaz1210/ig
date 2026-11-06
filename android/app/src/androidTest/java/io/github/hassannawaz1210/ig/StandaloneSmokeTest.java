package io.github.hassannawaz1210.ig;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.app.Activity;
import android.app.Instrumentation;
import android.net.Uri;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.view.MotionEvent;
import android.view.InputDevice;
import android.webkit.WebView;
import android.webkit.WebChromeClient;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.Assert.*;

/** Runs the packaged Python backend and actual WebView on Android, offline. */
@RunWith(AndroidJUnit4.class)
public class StandaloneSmokeTest {
    private static WebView findWebView(View view) {
        if (view instanceof WebView) return (WebView) view;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                WebView found = findWebView(group.getChildAt(i));
                if (found != null) return found;
            }
        }
        return null;
    }

    private static String evaluate(ActivityScenario<MainActivity> scenario, String script)
            throws InterruptedException {
        CountDownLatch ready = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>();
        scenario.onActivity(activity -> {
            WebView web = findWebView(activity.getWindow().getDecorView());
            if (web == null) {
                ready.countDown();
            } else {
                web.evaluateJavascript(script, value -> {
                    result.set(value);
                    ready.countDown();
                });
            }
        });
        assertTrue("WebView JavaScript callback timed out", ready.await(5, TimeUnit.SECONDS));
        return result.get();
    }

    private static void awaitPlayer(ActivityScenario<MainActivity> scenario) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + 60000;
        String state = null;
        do {
            state = evaluate(scenario, "Boolean(document.body && document.body.classList.contains('android-app') "
                    + "&& document.getElementById('feed') && !document.getElementById('local-page') && typeof boot !== 'undefined' && boot.android)");
            if ("true".equals(state)) return;
            SystemClock.sleep(100);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Packaged player never loaded in WebView; last readiness result: " + state);
    }

    private static IntentFilter documentIntent(String action) throws IntentFilter.MalformedMimeTypeException {
        IntentFilter filter = new IntentFilter(action);
        // A filter without a MIME type does not match OPEN/CREATE_DOCUMENT,
        // both of which carry a type, and would launch the real picker in CI.
        filter.addDataType("*/*");
        filter.addCategory(Intent.CATEGORY_OPENABLE);
        return filter;
    }

    private static void awaitSettings(ActivityScenario<MainActivity> scenario) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + 15000;
        String state;
        do {
            state = evaluate(scenario, "(()=>{const w=document.querySelector('#local-page iframe')?.contentWindow||window;const d=w.document;"
                    + "return Boolean(w.location.pathname==='/settings' && d.body?.classList.contains('android-app') "
                    + "&& d.querySelector('a[href=\"/feeds/export\"]') "
                    + "&& d.querySelector('#scroll-mode input[value=\"smooth\"]') "
                    + "&& d.querySelector('#scroll-mode input[value=\"snappy\"]') "
                    + "&& d.querySelector('#scroll-mode input:checked') && !d.getElementById('sound-on') "
                    + "&& d.getElementById('auto-scroll-on') && !d.getElementById('auto-scroll-on').disabled "
                    + "&& /feeds? \u00b7 /.test(d.getElementById('feeds-total')?.textContent || '') "
                    + "&& d.querySelector('a[href=\"/settings/feeds\"]') "
                    + "&& typeof w.importFollowing==='function' && typeof w.importFeeds==='function')})()");
            if ("true".equals(state)) return;
            SystemClock.sleep(100);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Settings and saved scrolling preference did not load in WebView: " + state);
    }

    private static void checkReelSharing(ActivityScenario<MainActivity> scenario) throws Exception {
        Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
        AtomicReference<Intent> shared = new AtomicReference<>();
        CountDownLatch opened = new CountDownLatch(1);
        Instrumentation.ActivityMonitor monitor = new Instrumentation.ActivityMonitor() {
            @Override public Instrumentation.ActivityResult onStartActivity(Intent intent) {
                if (!Intent.ACTION_CHOOSER.equals(intent.getAction())) return null;
                shared.set(intent.getParcelableExtra(Intent.EXTRA_INTENT));
                opened.countDown();
                return new Instrumentation.ActivityResult(Activity.RESULT_CANCELED, null);
            }
        };
        instrumentation.addMonitor(monitor);
        try {
            evaluate(scenario, "window.beforeShareUrl=location.href;window.beforeShareFeed=document.getElementById('feed');"
                    + "posts.push({shortcode:'DSmBF6-k8rM'});shareReel(posts.length-1);posts.pop()");
            assertTrue("Share chooser did not open", opened.await(5, TimeUnit.SECONDS));
            assertEquals(Intent.ACTION_SEND, shared.get().getAction());
            assertEquals("text/plain", shared.get().getType());
            assertEquals("https://www.instagram.com/reel/DSmBF6-k8rM/", shared.get().getStringExtra(Intent.EXTRA_TEXT));
            assertEquals("true", evaluate(scenario, "location.href===beforeShareUrl && document.getElementById('feed')===beforeShareFeed"));
        } finally { instrumentation.removeMonitor(monitor); }
    }

    private static void checkSettingsBack(ActivityScenario<MainActivity> scenario) throws Exception {
        String initialSound = evaluate(scenario, "desiredSound");
        evaluate(scenario, "window.playerStateMarker={};window.originalPlayerStateMarker=window.playerStateMarker;"
                + "if(!desiredSound)document.getElementById('mute').click();setCleanView(true);document.getElementById('settings-link').click()");
        awaitSettings(scenario);
        checkNativeUpdateLink(scenario);
        assertEquals("true", evaluate(scenario, "Boolean(document.querySelector('#local-page iframe'))"));
        evaluate(scenario, "(()=>{const d=document.querySelector('#local-page iframe').contentDocument;"
                + "const dialog=d.createElement('dialog');dialog.id='native-back-test';d.body.append(dialog);dialog.showModal()})()");
        scenario.onActivity(MainActivity::onBackPressed);
        long deadline = SystemClock.elapsedRealtime() + 5000;
        String closed;
        do {
            closed = evaluate(scenario, "Boolean(document.querySelector('#local-page iframe') && "
                    + "!document.querySelector('#local-page iframe').contentDocument.getElementById('native-back-test')?.open)");
            if ("true".equals(closed)) break;
            SystemClock.sleep(100);
        } while (SystemClock.elapsedRealtime() < deadline);
        assertEquals("Native Back did not close the child dialog first", "true", closed);
        scenario.onActivity(MainActivity::onBackPressed);
        awaitPlayer(scenario);
        assertEquals("true", evaluate(scenario, "Boolean(window.playerStateMarker) && window.playerStateMarker===window.originalPlayerStateMarker && desiredSound===true && document.body.classList.contains('clean-view')"));
        evaluate(scenario, "setCleanView(false)");
        assertTrue("Sound choice was not saved", Python.getInstance().getModule("ig.config")
                .callAttr("playback_preferences").callAttr("__getitem__", "sound").toBoolean());
        if ("false".equals(initialSound)) evaluate(scenario, "document.getElementById('mute').click()");
    }

    private static void checkNativeUpdateLink(ActivityScenario<MainActivity> scenario) throws Exception {
        Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
        Context app = instrumentation.getTargetContext();
        long version = UpdatePackage.code(app.getPackageManager().getPackageInfo(app.getPackageName(), 0));
        CountDownLatch checked = new CountDownLatch(1);
        AppUpdater fake = new AppUpdater(app, () -> {
            checked.countDown();
            return new UpdatePackage(version, "current", "", "", 1);
        }, app.getSharedPreferences("update-navigation-test", Context.MODE_PRIVATE));
        java.lang.reflect.Field field = MainActivity.class.getDeclaredField("updater");
        field.setAccessible(true);
        AtomicReference<AppUpdater> original = new AtomicReference<>();
        scenario.onActivity(activity -> {
            try {
                original.set((AppUpdater) field.get(activity));
                original.get().detach(activity);
                field.set(activity, fake);
                fake.attach(activity);
            } catch (IllegalAccessException error) { throw new AssertionError(error); }
        });
        try {
            evaluate(scenario, "window.updateBeforeFrame=document.querySelector('#local-page iframe');"
                    + "window.updateBeforeUrl=location.href;window.updateBeforeFrameUrl=updateBeforeFrame.contentWindow.location.href;"
                    + "updateBeforeFrame.contentDocument.querySelector('a[href=\"/app/update/check\"]').click()");
            assertTrue("Settings iframe did not invoke native updater", checked.await(10, TimeUnit.SECONDS));
            AtomicReference<String> text = new AtomicReference<>();
            long deadline = SystemClock.elapsedRealtime() + 10000;
            do {
                scenario.onActivity(activity -> {
                    android.widget.TextView label = activity.getWindow().getDecorView().findViewWithTag("app-update-message");
                    text.set(label == null ? null : label.getText().toString());
                });
                if ("ig is up to date".equals(text.get())) break;
                SystemClock.sleep(100);
            } while (SystemClock.elapsedRealtime() < deadline);
            assertEquals("Check success was not visible", "ig is up to date", text.get());
            assertEquals("true", evaluate(scenario, "location.href===updateBeforeUrl && document.querySelector('#local-page iframe')===updateBeforeFrame && updateBeforeFrame.contentWindow.location.href===updateBeforeFrameUrl"));
        } finally {
            scenario.onActivity(activity -> {
                try { fake.detach(activity); field.set(activity, original.get()); original.get().attach(activity); }
                catch (IllegalAccessException error) { throw new AssertionError(error); }
            });
        }
    }

    private static void checkCleanViewBack(ActivityScenario<MainActivity> scenario) throws Exception {
        AtomicReference<WebView> original = new AtomicReference<>();
        scenario.onActivity(activity -> original.set(findWebView(activity.getWindow().getDecorView())));
        evaluate(scenario, "window.cleanBackUrl=location.href;window.cleanBackFeed=document.getElementById('feed')");
        for (int repeat = 0; repeat < 3; repeat++) {
            evaluate(scenario, "setCleanView(true)");
            assertEquals("true", evaluate(scenario, "document.body.classList.contains('clean-view')"));
            scenario.onActivity(MainActivity::onBackPressed);
            long deadline = SystemClock.elapsedRealtime() + 5000;
            String state;
            do {
                state = evaluate(scenario, "!document.body.classList.contains('clean-view')");
                if ("true".equals(state)) break;
                SystemClock.sleep(100);
            } while (SystemClock.elapsedRealtime() < deadline);
            assertEquals("Back did not leave Clean View", "true", state);
            assertEquals("true", evaluate(scenario, "location.href===cleanBackUrl && document.getElementById('feed')===cleanBackFeed"));
            scenario.onActivity(activity -> assertSame("Back recreated WebView", original.get(), findWebView(activity.getWindow().getDecorView())));
        }
        // Older/local pages without the hook must retain normal dialog Back.
        evaluate(scenario, "(()=>{window.savedCleanExit=window.exitCleanView;delete window.exitCleanView;"
                + "const d=document.createElement('dialog');d.id='legacy-back-test';document.body.append(d);d.showModal()})()");
        try {
            scenario.onActivity(MainActivity::onBackPressed);
            long deadline = SystemClock.elapsedRealtime() + 5000;
            String state;
            do {
                state = evaluate(scenario, "!document.getElementById('legacy-back-test').open && location.href===cleanBackUrl");
                if ("true".equals(state)) break;
                SystemClock.sleep(100);
            } while (SystemClock.elapsedRealtime() < deadline);
            assertEquals("Back failed without Clean View hook", "true", state);
        } finally {
            evaluate(scenario, "window.exitCleanView=window.savedCleanExit;document.getElementById('legacy-back-test')?.remove()");
        }
    }

    private static void checkFilePickerCancellation(ActivityScenario<MainActivity> scenario) throws Exception {
        Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
        // Intercept the actual platform intent so no document picker UI or
        // external files are needed. A rejected result must reset the callback
        // so another selection can be opened immediately.
        for (Intent result : new Intent[] {null, new Intent().setData(Uri.parse("file:///data/private.json"))}) {
            Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(
                    documentIntent(Intent.ACTION_OPEN_DOCUMENT),
                    new Instrumentation.ActivityResult(result == null ? Activity.RESULT_CANCELED : Activity.RESULT_OK, result), true);
            CountDownLatch completed = new CountDownLatch(1);
            AtomicReference<Uri[]> selected = new AtomicReference<>();
            try {
                scenario.onActivity(activity -> {
                    WebView web = findWebView(activity.getWindow().getDecorView());
                    assertNotNull(web);
                    WebChromeClient.FileChooserParams params = new WebChromeClient.FileChooserParams() {
                        @Override public int getMode() { return MODE_OPEN; }
                        @Override public String[] getAcceptTypes() { return new String[] {"application/json"}; }
                        @Override public boolean isCaptureEnabled() { return false; }
                        @Override public CharSequence getTitle() { return null; }
                        @Override public String getFilenameHint() { return null; }
                        @Override public Intent createIntent() { return new Intent(Intent.ACTION_OPEN_DOCUMENT); }
                    };
                    assertTrue(web.getWebChromeClient().onShowFileChooser(web, files -> {
                        selected.set(files);
                        completed.countDown();
                    }, params));
                });
                assertTrue("File picker did not finish", completed.await(5, TimeUnit.SECONDS));
                assertEquals("Document picker was not launched", 1, monitor.getHits());
                assertNull("Cancelled or unsafe file selection was accepted", selected.get());
            } finally {
                instrumentation.removeMonitor(monitor);
            }
        }
    }

    private static void checkFileUpload(ActivityScenario<MainActivity> scenario) throws Exception {
        Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
        instrumentation.getContext().getContentResolver().call(FollowingFixtureProvider.FILE, "grant", null, null);
        Context app = instrumentation.getTargetContext();
        assertEquals("Fixture did not grant read access to target app", PackageManager.PERMISSION_GRANTED,
                app.checkUriPermission(FollowingFixtureProvider.FILE, android.os.Process.myPid(), android.os.Process.myUid(),
                        Intent.FLAG_GRANT_READ_URI_PERMISSION));
        try (java.io.InputStream input = app.getContentResolver().openInputStream(FollowingFixtureProvider.FILE)) {
            assertNotNull("Fixture provider did not open", input);
            assertEquals("Fixture was unreadable", '{', input.read());
        }
        try (java.io.InputStream denied = app.getContentResolver().openInputStream(
                Uri.parse("content://io.github.hassannawaz1210.ig.test.following/private.json"))) {
            fail("Android allowed reading an ungranted protected document");
        } catch (SecurityException expected) {
            // Selection does not bypass the platform's document permissions.
        }
        Intent result = new Intent().setData(FollowingFixtureProvider.FILE)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(
                documentIntent(Intent.ACTION_OPEN_DOCUMENT),
                new Instrumentation.ActivityResult(Activity.RESULT_OK, result), true);
        try {
            evaluate(scenario, "(()=>{window.fixtureUpload='waiting';const input=document.createElement('input');"
                    + "input.id='fixture-file';input.type='file';input.accept='.json';"
                    + "input.style='position:fixed;left:10px;top:10px;width:200px;height:80px;z-index:2147483647;background:white';"
                    + "input.onclick=()=>window.fixtureClicked=true;input.oncancel=()=>window.fixtureUpload='cancelled';"
                    + "input.onchange=async()=>{try{const file=input.files[0];"
                    + "const text=await file.text();if(!text.includes('fixture_creator'))throw Error('File unreadable: size='+file.size+', read='+text.length);"
                    + "const response=await fetch('/following/preview',{method:'POST',body:file});"
                    + "const data=await response.json();window.fixtureUpload=response.ok&&data.accounts.includes('fixture_creator')?'ok':JSON.stringify(data);"
                    + "}catch(error){window.fixtureUpload=String(error)}};document.body.append(input);return true})()");
            CountDownLatch painted = new CountDownLatch(1);
            scenario.onActivity(activity -> findWebView(activity.getWindow().getDecorView())
                    .postVisualStateCallback(1, new WebView.VisualStateCallback() {
                        @Override public void onComplete(long requestId) { painted.countDown(); }
                    }));
            assertTrue("File input was not painted", painted.await(10, TimeUnit.SECONDS));
            float cssX = Float.parseFloat(evaluate(scenario,
                    "(()=>{const r=document.getElementById('fixture-file').getBoundingClientRect();return r.x+r.width/2})()"));
            float cssY = Float.parseFloat(evaluate(scenario,
                    "(()=>{const r=document.getElementById('fixture-file').getBoundingClientRect();return r.y+r.height/2})()"));
            float[] point = new float[2];
            scenario.onActivity(activity -> {
                WebView web = findWebView(activity.getWindow().getDecorView());
                int[] screen = new int[2];
                web.getLocationOnScreen(screen);
                point[0] = screen[0] + cssX * web.getScale();
                point[1] = screen[1] + cssY * web.getScale();
            });
            long now = SystemClock.uptimeMillis();
            MotionEvent down = MotionEvent.obtain(now, now, MotionEvent.ACTION_DOWN, point[0], point[1], 0);
            down.setSource(InputDevice.SOURCE_TOUCHSCREEN);
            instrumentation.sendPointerSync(down);
            SystemClock.sleep(50);
            MotionEvent up = MotionEvent.obtain(now, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, point[0], point[1], 0);
            up.setSource(InputDevice.SOURCE_TOUCHSCREEN);
            instrumentation.sendPointerSync(up);
            down.recycle();
            up.recycle();
            long pickerDeadline = SystemClock.elapsedRealtime() + 5000;
            while (monitor.getHits() == 0 && SystemClock.elapsedRealtime() < pickerDeadline) SystemClock.sleep(100);
            assertEquals("File input tap did not open picker; clicked=" + evaluate(scenario, "Boolean(window.fixtureClicked)"),
                    1, monitor.getHits());
            long deadline = SystemClock.elapsedRealtime() + 15000;
            String state;
            do {
                state = evaluate(scenario, "window.fixtureUpload");
                if (!"\"waiting\"".equals(state)) break;
                SystemClock.sleep(100);
            } while (SystemClock.elapsedRealtime() < deadline);
            assertEquals("File was not read and parsed by the local backend", "\"ok\"", state);
            assertEquals("System document picker was not opened", 1, monitor.getHits());
        } finally {
            evaluate(scenario, "document.getElementById('fixture-file')?.remove()");
            instrumentation.removeMonitor(monitor);
        }
    }

    private static void checkBackupDownload(ActivityScenario<MainActivity> scenario) throws Exception {
        Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
        String originalPath = evaluate(scenario, "location.pathname");
        instrumentation.getContext().getContentResolver().call(FollowingFixtureProvider.FILE, "grant", null, null);
        assertEquals("Fixture did not grant write access to target app", PackageManager.PERMISSION_GRANTED,
                instrumentation.getTargetContext().checkUriPermission(FollowingFixtureProvider.BACKUP,
                        android.os.Process.myPid(), android.os.Process.myUid(), Intent.FLAG_GRANT_WRITE_URI_PERMISSION));
        Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(
                documentIntent(Intent.ACTION_CREATE_DOCUMENT),
                new Instrumentation.ActivityResult(Activity.RESULT_OK,
                        new Intent().setData(FollowingFixtureProvider.BACKUP)
                                .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)), true);
        try {
            evaluate(scenario, "document.querySelector('a[href=\"/feeds/export\"]').click()");
            long deadline = SystemClock.elapsedRealtime() + 15000;
            String backup;
            do {
                backup = instrumentation.getContext().getContentResolver()
                        .call(FollowingFixtureProvider.FILE, "readBackup", null, null).getString("backup");
                if (backup != null) break;
                SystemClock.sleep(100);
            } while (SystemClock.elapsedRealtime() < deadline);
            assertNotNull("Authenticated feed backup was not saved to selected document", backup);
            JSONObject exported = new JSONObject(backup);
            assertEquals("ig-feeds", exported.getString("format"));
            assertEquals(2, exported.getInt("version"));
            assertNotNull(exported.getJSONObject("feeds"));
            assertNotNull(exported.getJSONArray("saved_reels"));
            JSONObject settings = exported.getJSONObject("settings");
            assertTrue(settings.has("sound") && settings.has("auto_scroll")
                    && settings.has("scroll_animation") && settings.has("selected_feeds"));
            assertEquals("Save document picker was not opened", 1, monitor.getHits());
            assertEquals("Export navigated away from Settings", originalPath, evaluate(scenario, "location.pathname"));
        } finally {
            instrumentation.removeMonitor(monitor);
        }
    }

    /** The one-time "Open Instagram links in ig?" dialog would cover the player and take test taps. */
    @Before
    public void skipLinkHandlingPrompt() {
        InstrumentationRegistry.getInstrumentation().getTargetContext()
                .getSharedPreferences("ig", Context.MODE_PRIVATE).edit()
                .putBoolean("linkHandlingPromptShown", true)
                .putBoolean("disableUpdateChecks", true).commit();
    }

    private static boolean appHandles(String url) {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        Intent view = new Intent(Intent.ACTION_VIEW, Uri.parse(url))
                .addCategory(Intent.CATEGORY_BROWSABLE).setPackage(context.getPackageName());
        return !context.getPackageManager().queryIntentActivities(view, 0).isEmpty();
    }

    @Test
    public void instagramReelLinksCanOpenInApp() {
        for (String url : new String[] {"https://www.instagram.com/reel/ABCDE123/",
                "https://instagram.com/p/ABCDE123/?igsh=x", "http://m.instagram.com/reels/ABCDE123"})
            assertTrue("ig offers to open " + url, appHandles(url));
        for (String url : new String[] {"https://www.instagram.com/someone/",
                "https://www.instagram.com/stories/someone/1/", "https://example.com/reel/ABCDE123/"})
            assertFalse("ig leaves " + url + " to other apps", appHandles(url));
    }

    @Test
    public void tappedReelLinkOpensInPlayer() throws Exception {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        Intent tap = new Intent(Intent.ACTION_VIEW, Uri.parse("https://www.instagram.com/reel/ABCDE123/?igsh=x"))
                .setClass(context, MainActivity.class);
        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(tap)) {
            String path = null;
            for (int i = 0; i < 600 && !"\"/reel/ABCDE123\"".equals(path); i++) {
                SystemClock.sleep(100);
                path = evaluate(scenario, "location.pathname");
            }
            assertEquals("\"/reel/ABCDE123\"", path);
        }
    }

    @Test
    public void backendAndPlayer() throws Exception {
        Context app = InstrumentationRegistry.getInstrumentation().getTargetContext();
        String mode = InstrumentationRegistry.getArguments().getString("persistence", "");
        if (!mode.isEmpty()) {
            // start uses precisely the same directories as MainActivity, not a
            // test-only DB. No Activity or WebView is started in these phases.
            if (!Python.isStarted()) Python.start(new AndroidPlatform(app));
            Python.getInstance().getModule("ig_android").callAttr("start",
                    app.getFilesDir().getAbsolutePath(), app.getCacheDir().getAbsolutePath());
            String code;
            if ("seed".equals(mode)) {
                code = "import json, os\nfrom pathlib import Path\n"
                        + "from ig.config import edit_feeds, playback_preferences, selected_feeds, set_playback_preferences, set_selected_feeds\n"
                        + "backup = Path(os.environ['XDG_CACHE_HOME']) / 'android-ci-preferences.json'\n"
                        + "backup.write_text(json.dumps({'playback': playback_preferences(), 'selected': selected_feeds()}))\n"
                        + "with edit_feeds() as feeds:\n"
                        + "    assert 'android_ci' not in feeds\n"
                        + "    feeds['android_ci'] = ['ci_creator']\n"
                        + "set_playback_preferences({'sound': True, 'auto_scroll': True})\n"
                        + "set_selected_feeds(['android_ci'])\n";
            } else {
                assertEquals("Unknown persistence mode", "verify", mode);
                code = "import json, os\nfrom pathlib import Path\n"
                        + "from ig.config import load_feeds, edit_feeds, playback_preferences, selected_feeds, set_playback_preferences, set_selected_feeds\n"
                        + "assert load_feeds().get('android_ci') == ['ci_creator'], 'Feed lost after process restart'\n"
                        + "assert playback_preferences() == {'sound': True, 'auto_scroll': True}, 'Playback preferences lost after process restart'\n"
                        + "assert selected_feeds() == ['android_ci'], 'Feed selection lost after process restart'\n"
                        + "backup = Path(os.environ['XDG_CACHE_HOME']) / 'android-ci-preferences.json'\n"
                        + "previous = json.loads(backup.read_text())\n"
                        + "with edit_feeds() as feeds:\n"
                        + "    del feeds['android_ci']\n"
                        + "set_playback_preferences(previous['playback'])\n"
                        + "set_selected_feeds(previous['selected'])\n"
                        + "backup.unlink()\n";
            }
            // Java has no Python caller frame for exec to inherit globals from.
            Python.getInstance().getModule("builtins").callAttr("exec", code,
                    Python.getInstance().getModule("builtins").callAttr("dict"));
            return;
        }

        try (ActivityScenario<MainActivity> scenario = ActivityScenario.launch(MainActivity.class)) {
            awaitPlayer(scenario);
            JSONObject report = new JSONObject(Python.getInstance().getModule("ig_android")
                    .callAttr("smoke_test", app.getFilesDir().getAbsolutePath(),
                            app.getCacheDir().getAbsolutePath()).toString());
            assertTrue(report.toString(), report.getBoolean("success"));
            assertTrue("Backend smoke checks were empty", report.getJSONArray("tests").length() >= 4);
            assertEquals("true", evaluate(scenario,
                    "getComputedStyle(document.getElementById('shortcuts')).display === 'none'"));
            checkCleanViewBack(scenario);
            checkSettingsBack(scenario);
            checkReelSharing(scenario);
            evaluate(scenario, "window.location.href='/settings'");
            awaitSettings(scenario);
            checkFilePickerCancellation(scenario);
            checkFileUpload(scenario);
            checkBackupDownload(scenario);
            evaluate(scenario, "window.location.href='/'");
            awaitPlayer(scenario);
            scenario.recreate();
            awaitPlayer(scenario);
        }
    }
}

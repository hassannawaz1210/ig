package io.github.hassannawaz1210.ig;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.app.Activity;
import android.app.Instrumentation;
import android.net.Uri;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.view.MotionEvent;
import android.webkit.WebView;
import android.webkit.WebChromeClient;

import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;
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
                    + "&& document.getElementById('feed') && typeof boot !== 'undefined' && boot.android)");
            if ("true".equals(state)) return;
            SystemClock.sleep(100);
        } while (SystemClock.elapsedRealtime() < deadline);
        fail("Packaged player never loaded in WebView; last readiness result: " + state);
    }

    private static void checkFilePickerCancellation(ActivityScenario<MainActivity> scenario) throws Exception {
        Instrumentation instrumentation = InstrumentationRegistry.getInstrumentation();
        // Intercept the actual platform intent so no document picker UI or
        // external files are needed. A rejected result must reset the callback
        // so another selection can be opened immediately.
        for (Intent result : new Intent[] {null, new Intent().setData(Uri.parse("file:///data/private.json"))}) {
            Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(
                    new IntentFilter(Intent.ACTION_OPEN_DOCUMENT),
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
        Intent result = new Intent().setData(FollowingFixtureProvider.FILE)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(
                new IntentFilter(Intent.ACTION_OPEN_DOCUMENT),
                new Instrumentation.ActivityResult(Activity.RESULT_OK, result), true);
        try {
            evaluate(scenario, "(()=>{window.fixtureUpload='waiting';const input=document.createElement('input');"
                    + "input.id='fixture-file';input.type='file';input.accept='.json';"
                    + "input.style='position:fixed;left:10px;top:10px;width:200px;height:80px;z-index:2147483647;background:white';"
                    + "input.onchange=async()=>{try{const file=input.files[0];"
                    + "if(!(await file.text()).includes('fixture_creator'))throw Error('File unreadable');"
                    + "const response=await fetch('/following/preview',{method:'POST',body:file});"
                    + "const data=await response.json();window.fixtureUpload=response.ok&&data.accounts.includes('fixture_creator')?'ok':JSON.stringify(data);"
                    + "}catch(error){window.fixtureUpload=String(error)}};document.body.append(input);return true})()");
            scenario.onActivity(activity -> {
                WebView web = findWebView(activity.getWindow().getDecorView());
                float density = activity.getResources().getDisplayMetrics().density;
                long now = SystemClock.uptimeMillis();
                MotionEvent down = MotionEvent.obtain(now, now, MotionEvent.ACTION_DOWN, 50 * density, 40 * density, 0);
                MotionEvent up = MotionEvent.obtain(now, now + 30, MotionEvent.ACTION_UP, 50 * density, 40 * density, 0);
                web.dispatchTouchEvent(down);
                web.dispatchTouchEvent(up);
                down.recycle();
                up.recycle();
            });
            long deadline = SystemClock.elapsedRealtime() + 10000;
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
        instrumentation.getContext().getContentResolver().call(FollowingFixtureProvider.FILE, "grant", null, null);
        Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(
                new IntentFilter(Intent.ACTION_CREATE_DOCUMENT),
                new Instrumentation.ActivityResult(Activity.RESULT_OK,
                        new Intent().setData(FollowingFixtureProvider.BACKUP)
                                .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION)), true);
        try {
            evaluate(scenario, "window.location.href='/feeds/export'");
            long deadline = SystemClock.elapsedRealtime() + 15000;
            String backup;
            do {
                backup = instrumentation.getContext().getContentResolver()
                        .call(FollowingFixtureProvider.FILE, "readBackup", null, null).getString("backup");
                if (backup != null) break;
                SystemClock.sleep(100);
            } while (SystemClock.elapsedRealtime() < deadline);
            assertNotNull("Authenticated feed backup was not saved to selected document", backup);
            assertTrue("Feed backup did not contain feeds", new JSONObject(backup).has("feeds"));
            assertEquals("Save document picker was not opened", 1, monitor.getHits());
            awaitPlayer(scenario);
        } finally {
            instrumentation.removeMonitor(monitor);
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
                code = "from ig.config import edit_feeds\n"
                        + "with edit_feeds() as feeds:\n"
                        + "    assert 'android_ci' not in feeds\n"
                        + "    feeds['android_ci'] = ['ci_creator']\n";
            } else {
                assertEquals("Unknown persistence mode", "verify", mode);
                code = "from ig.config import load_feeds, edit_feeds\n"
                        + "assert load_feeds().get('android_ci') == ['ci_creator'], 'Feed lost after process restart'\n"
                        + "with edit_feeds() as feeds:\n"
                        + "    del feeds['android_ci']\n";
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
            checkFilePickerCancellation(scenario);
            checkFileUpload(scenario);
            checkBackupDownload(scenario);
            scenario.recreate();
            awaitPlayer(scenario);
        }
    }
}

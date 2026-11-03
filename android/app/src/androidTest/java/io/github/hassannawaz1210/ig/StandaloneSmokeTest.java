package io.github.hassannawaz1210.ig;

import android.content.Context;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;

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
            scenario.recreate();
            awaitPlayer(scenario);
        }
    }
}

package io.github.hassannawaz1210.ig;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Message;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.JsPromptResult;
import android.webkit.JsResult;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Android owns the window; the existing Python app owns feeds and playback UI. */
public final class MainActivity extends Activity {
    private static final ExecutorService BACKEND = Executors.newSingleThreadExecutor();
    private static final Object PYTHON_LOCK = new Object();
    private static final Pattern POST_PATH = Pattern.compile("^/(?:reel|reels|p)/([A-Za-z0-9_-]{5,30})/?$");
    private static final Pattern SHARED_LINK = Pattern.compile("https?://(?:www\\.|m\\.)?instagram\\.com/(?:reel|reels|p)/[A-Za-z0-9_-]{5,30}/?(?:\\?[^\\s]*)?", Pattern.CASE_INSENSITIVE);
    private WebView web;
    private LinearLayout root;
    private String origin;
    private String token;
    private String pendingPath = "/";
    private boolean starting;
    private boolean paused;

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.BLACK);
        setContentView(root);
        // targetSdk 35 draws edge-to-edge: keep the WebView below system bars.
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else {
                view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                        insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            }
            return insets;
        });
        if (savedInstanceState != null) {
            String saved = savedInstanceState.getString("path", "/");
            if (saved.startsWith("/") && !saved.startsWith("//")) pendingPath = saved;
        }
        acceptSharedLink(getIntent());
        startBackend();
    }

    private void startBackend() {
        if (starting) return;
        starting = true;
        showLoading();
        BACKEND.execute(() -> {
            try {
                synchronized (PYTHON_LOCK) {
                    if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                }
                String result = Python.getInstance().getModule("ig_android")
                        .callAttr("start", getFilesDir().getAbsolutePath(), getCacheDir().getAbsolutePath()).toString();
                JSONObject data = new JSONObject(result);
                Uri address = Uri.parse(data.getString("url"));
                if (!"http".equals(address.getScheme()) || !"127.0.0.1".equals(address.getHost()) || address.getPort() < 1)
                    throw new IllegalStateException("Backend returned an invalid local address");
                String localOrigin = "http://127.0.0.1:" + address.getPort();
                String localToken = data.getString("token");
                if (!localToken.matches("[A-Za-z0-9_-]{20,}")) throw new IllegalStateException("Invalid local access token");
                runOnUiThread(() -> {
                    starting = false;
                    if (isFinishing() || isDestroyed()) return;
                    origin = localOrigin;
                    token = localToken;
                    createWebView();
                    CookieManager cookies = CookieManager.getInstance();
                    cookies.setAcceptCookie(true);
                    cookies.setAcceptThirdPartyCookies(web, false);
                    cookies.setCookie(origin, "ig_android=" + token + "; Path=/; HttpOnly; SameSite=Strict", accepted -> {
                        if (isFinishing() || isDestroyed() || web == null) return;
                        if (!accepted) { showFailure("Could not initialize local app access."); return; }
                        web.loadUrl(origin + pendingPath);
                    });
                });
            } catch (Exception error) {
                android.util.Log.e("ig", "Local backend startup failed", error);
                runOnUiThread(() -> {
                    starting = false;
                    if (!isFinishing() && !isDestroyed()) showFailure("Could not start ig on this phone. Your feeds are still saved.");
                });
            }
        });
    }

    private void showLoading() {
        root.removeAllViews();
        root.setGravity(Gravity.CENTER);
        root.addView(new ProgressBar(this));
        TextView label = label("Opening ig…");
        root.addView(label);
    }

    private TextView label(String text) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextColor(Color.WHITE);
        view.setTextSize(16);
        view.setGravity(Gravity.CENTER);
        view.setPadding(32, 24, 32, 24);
        return view;
    }

    private void showFailure(String text) {
        if (web != null) { web.destroy(); web = null; }
        root.removeAllViews();
        root.setGravity(Gravity.CENTER);
        root.addView(label(text));
        Button retry = new Button(this);
        retry.setText("Try again");
        retry.setOnClickListener(view -> startBackend());
        root.addView(retry);
    }

    private void createWebView() {
        if (web != null) web.destroy();
        root.removeAllViews();
        root.setGravity(Gravity.TOP);
        web = new WebView(this);
        web.setBackgroundColor(Color.BLACK);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return routeNavigation(request.getUrl());
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame() && isLocal(request.getUrl()))
                    showFailure("The local player stopped responding. Tap Try again to reopen it.");
            }
            @Override public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
                showFailure("The player closed. Tap Try again to reopen it.");
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                if (!isLocal(Uri.parse(url))) { result.cancel(); return true; }
                new AlertDialog.Builder(MainActivity.this).setMessage(message)
                        .setPositiveButton("OK", (dialog, which) -> result.confirm())
                        .setOnCancelListener(dialog -> result.cancel()).show();
                return true;
            }
            @Override public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
                if (!isLocal(Uri.parse(url))) { result.cancel(); return true; }
                new AlertDialog.Builder(MainActivity.this).setMessage(message)
                        .setPositiveButton("OK", (dialog, which) -> result.confirm())
                        .setNegativeButton("Cancel", (dialog, which) -> result.cancel())
                        .setOnCancelListener(dialog -> result.cancel()).show();
                return true;
            }
            @Override public boolean onJsPrompt(WebView view, String url, String message, String defaultValue, JsPromptResult result) {
                if (!isLocal(Uri.parse(url))) { result.cancel(); return true; }
                EditText input = new EditText(MainActivity.this);
                input.setSingleLine(true);
                input.setText(defaultValue);
                input.setSelectAllOnFocus(true);
                new AlertDialog.Builder(MainActivity.this).setTitle(message).setView(input)
                        .setPositiveButton("Open", (dialog, which) -> result.confirm(input.getText().toString()))
                        .setNegativeButton("Cancel", (dialog, which) -> result.cancel())
                        .setOnCancelListener(dialog -> result.cancel()).show();
                return true;
            }
            @Override public boolean onCreateWindow(WebView view, boolean dialog, boolean userGesture, Message message) {
                if (!userGesture) return false;
                WebView popup = new WebView(MainActivity.this);
                popup.setWebViewClient(new WebViewClient() {
                    @Override public boolean shouldOverrideUrlLoading(WebView target, WebResourceRequest request) {
                        Uri uri = request.getUrl();
                        if (isLocal(uri)) web.loadUrl(uri.toString()); else openExternal(uri);
                        target.destroy();
                        return true;
                    }
                });
                ((WebView.WebViewTransport) message.obj).setWebView(popup);
                message.sendToTarget();
                return true;
            }
        });
        web.setDownloadListener((url, userAgent, disposition, mimeType, length) -> saveDownload(Uri.parse(url)));
        root.addView(web, new LinearLayout.LayoutParams(-1, -1));
        if (paused) web.onPause();
    }

    private boolean isLocal(Uri uri) {
        if (origin == null) return false;
        Uri expected = Uri.parse(origin);
        return "http".equals(uri.getScheme()) && "127.0.0.1".equals(uri.getHost())
                && uri.getPort() == expected.getPort() && uri.getUserInfo() == null;
    }

    private boolean routeNavigation(Uri uri) {
        if (isLocal(uri)) {
            if (uri.getPath() != null && uri.getPath().matches("/download/[A-Za-z0-9_-]{5,30}")) {
                saveDownload(uri);
                return true;
            }
            return false;
        }
        openExternal(uri);
        return true;
    }

    private void openExternal(Uri uri) {
        if (!"https".equals(uri.getScheme()) && !"http".equals(uri.getScheme())) return;
        try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
        catch (ActivityNotFoundException error) { Toast.makeText(this, "No browser installed", Toast.LENGTH_SHORT).show(); }
    }

    private void saveDownload(Uri uri) {
        String path = uri.getPath();
        if (!isLocal(uri) || path == null || !path.matches("/download/[A-Za-z0-9_-]{5,30}")) return;
        String filename = path.substring("/download/".length()) + ".mp4";
        try {
            DownloadManager.Request request = new DownloadManager.Request(uri)
                    .addRequestHeader("Cookie", "ig_android=" + token)
                    .setTitle(filename)
                    .setMimeType("video/mp4")
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    .setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, filename);
            ((DownloadManager) getSystemService(DOWNLOAD_SERVICE)).enqueue(request);
            Toast.makeText(this, "Downloading reel", Toast.LENGTH_SHORT).show();
        } catch (RuntimeException error) {
            Toast.makeText(this, "Could not start download", Toast.LENGTH_LONG).show();
        }
    }

    private boolean acceptSharedLink(Intent intent) {
        if (!Intent.ACTION_SEND.equals(intent.getAction()) || !"text/plain".equals(intent.getType())) return false;
        String text = intent.getStringExtra(Intent.EXTRA_TEXT);
        if (text == null) return false;
        Matcher link = SHARED_LINK.matcher(text);
        if (!link.find()) { Toast.makeText(this, "Share an Instagram reel link", Toast.LENGTH_SHORT).show(); return false; }
        Uri uri = Uri.parse(link.group());
        Matcher post = POST_PATH.matcher(uri.getPath() == null ? "" : uri.getPath());
        if (!post.matches()) return false;
        pendingPath = "/reel/" + post.group(1);
        return true;
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (acceptSharedLink(intent) && web != null && origin != null) web.loadUrl(origin + pendingPath);
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        if (web != null && web.getUrl() != null && isLocal(Uri.parse(web.getUrl()))) {
            Uri current = Uri.parse(web.getUrl());
            pendingPath = current.getEncodedPath();
            if (current.getEncodedQuery() != null) pendingPath += "?" + current.getEncodedQuery();
        }
        state.putString("path", pendingPath);
        super.onSaveInstanceState(state);
    }

    @Override protected void onPause() {
        paused = true;
        if (web != null) {
            web.evaluateJavascript("document.querySelectorAll('video').forEach(v=>v.pause())", null);
            web.onPause();
        }
        super.onPause();
    }

    @Override protected void onResume() {
        super.onResume();
        paused = false;
        if (web != null) web.onResume();
    }

    @Override public void onBackPressed() {
        if (web == null) { super.onBackPressed(); return; }
        web.evaluateJavascript("(()=>{const d=document.querySelector('dialog[open]');if(d){d.close();return true;}const c=document.getElementById('comments-panel');if(c&&!c.hidden){closeComments();return true;}const f=document.getElementById('feed-menu');if(f&&f.open){f.open=false;return true;}return false;})()", handled -> {
            if ("true".equals(handled)) return;
            if (web != null && web.canGoBack()) web.goBack();
            else MainActivity.super.onBackPressed();
        });
    }

    @Override protected void onDestroy() {
        if (web != null) { web.destroy(); web = null; }
        super.onDestroy();
    }
}

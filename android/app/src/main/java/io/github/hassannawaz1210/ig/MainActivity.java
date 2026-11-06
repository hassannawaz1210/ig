package io.github.hassannawaz1210.ig;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.verify.domain.DomainVerificationManager;
import android.content.pm.verify.domain.DomainVerificationUserState;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Message;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.JsPromptResult;
import android.webkit.JsResult;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.ValueCallback;
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

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Android owns the window; the existing Python app owns feeds and playback UI. */
public final class MainActivity extends Activity implements AppUpdater.Listener {
    private static final ExecutorService BACKEND = Executors.newSingleThreadExecutor();
    private static final Object PYTHON_LOCK = new Object();
    private static final int CHOOSE_IMPORT_FILE = 41;
    private static final int SAVE_FEEDS_FILE = 42;
    private static final int ALLOW_APP_UPDATES = 44;
    private static final int INSTALL_APP_UPDATE = 45;
    private static final int MAX_BACKUP_BYTES = 5 * 1024 * 1024;
    private static final Pattern POST_PATH = Pattern.compile("^/(?:reel|reels|p)/([A-Za-z0-9_-]{5,30})/?$");
    private static final Pattern INSTAGRAM_HOST = Pattern.compile("^(?:www\\.|m\\.)?instagram\\.com$", Pattern.CASE_INSENSITIVE);
    private static final String LINK_PROMPT_SHOWN = "linkHandlingPromptShown";
    private static final Pattern SHARED_LINK = Pattern.compile("https?://(?:www\\.|m\\.)?instagram\\.com/(?:reel|reels|p)/[A-Za-z0-9_-]{5,30}/?(?:\\?[^\\s]*)?", Pattern.CASE_INSENSITIVE);
    private WebView web;
    private LinearLayout root;
    private String origin;
    private String token;
    private String pendingPath = "/";
    private boolean starting;
    private boolean paused;
    private ValueCallback<Uri[]> fileSelection;
    private boolean choosingBackup;
    private Uri pendingBackupDestination;
    private AppUpdater updater;
    // Latest update notice as JSON for the page; re-sent after each page load.
    private String updateNotice = "null";

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        updater = AppUpdater.get(this);
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
            choosingBackup = savedInstanceState.getBoolean("choosingBackup", false);
            String destination = savedInstanceState.getString("backupDestination");
            if (destination != null && "content".equals(Uri.parse(destination).getScheme()))
                pendingBackupDestination = Uri.parse(destination);
            String saved = savedInstanceState.getString("path", "/");
            if (saved.startsWith("/") && !saved.startsWith("//")) pendingPath = saved;
        }
        boolean openedFromLink = acceptInstagramLink(getIntent());
        startBackend();
        if (savedInstanceState == null && !openedFromLink) offerLinkHandling();
    }

    /** Shown under Settings → About. */
    private String appVersionName() {
        try { String name = getPackageManager().getPackageInfo(getPackageName(), 0).versionName; return name == null ? "" : name; }
        catch (Exception error) { android.util.Log.w("ig", "Could not read app version", error); return ""; }
    }

    private String appVersionCode() {
        try { return String.valueOf(UpdatePackage.code(getPackageManager().getPackageInfo(getPackageName(), 0))); }
        catch (Exception error) { android.util.Log.w("ig", "Could not read app build number", error); return ""; }
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
                        .callAttr("start", getFilesDir().getAbsolutePath(), getCacheDir().getAbsolutePath(),
                                appVersionName(), appVersionCode()).toString();
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
                    if (pendingBackupDestination != null) {
                        Uri destination = pendingBackupDestination;
                        pendingBackupDestination = null;
                        writeFeedsBackup(destination);
                    }
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
        finishFileSelection(null);
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
        finishFileSelection(null);
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
        // The document picker grants access only to the file the user selects.
        settings.setAllowContentAccess(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSupportMultipleWindows(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return routeNavigation(request.getUrl());
            }
            @Override public void onPageFinished(WebView view, String url) {
                // A new page has no update card yet; show the current notice again.
                if (url != null && isLocal(Uri.parse(url))) sendUpdateNotice();
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
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                                       FileChooserParams params) {
                if (view.getUrl() == null || !isLocal(Uri.parse(view.getUrl()))
                        || params.getMode() != FileChooserParams.MODE_OPEN || fileSelection != null) {
                    callback.onReceiveValue(null);
                    return true;
                }
                fileSelection = callback;
                Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT)
                        .addCategory(Intent.CATEGORY_OPENABLE)
                        .setType("*/*")
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                try { startActivityForResult(pick, CHOOSE_IMPORT_FILE); }
                catch (ActivityNotFoundException | SecurityException error) {
                    finishFileSelection(null);
                    Toast.makeText(MainActivity.this, "Could not open the file picker", Toast.LENGTH_SHORT).show();
                }
                return true;
            }
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
        root.addView(web, new LinearLayout.LayoutParams(-1, 0, 1));
        updater.render();
        if (paused) web.onPause();
    }

    private boolean isLocal(Uri uri) {
        if (origin == null) return false;
        Uri expected = Uri.parse(origin);
        return "http".equals(uri.getScheme()) && "127.0.0.1".equals(uri.getHost())
                && uri.getPort() == expected.getPort() && uri.getUserInfo() == null;
    }

    private void finishFileSelection(Uri[] files) {
        ValueCallback<Uri[]> callback = fileSelection;
        fileSelection = null;
        if (callback != null) callback.onReceiveValue(files);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == ALLOW_APP_UPDATES) {
            updater.permissionReturned();
            return;
        }
        if (requestCode == INSTALL_APP_UPDATE) {
            if (resultCode == RESULT_OK) updater.installSucceeded();
            else updater.installFinished("Update ready · tap Install to retry");
            return;
        }
        if (requestCode == SAVE_FEEDS_FILE) {
            if (!choosingBackup) return;
            choosingBackup = false;
            Uri destination = resultCode == RESULT_OK && data != null ? data.getData() : null;
            if (destination != null && "content".equals(destination.getScheme())) {
                if (origin == null || token == null) pendingBackupDestination = destination;
                else writeFeedsBackup(destination);
            }
            return;
        }
        if (requestCode != CHOOSE_IMPORT_FILE || fileSelection == null) return;
        Uri selected = resultCode == RESULT_OK && data != null ? data.getData() : null;
        // Only document URIs from the pending picker are accepted. Android
        // enforces access when WebView opens them; public providers need no
        // explicit URI grant, so checkUriPermission alone is too restrictive.
        if (selected != null && "content".equals(selected.getScheme())) {
            finishFileSelection(new Uri[] {selected});
        } else {
            finishFileSelection(null);
        }
    }

    private boolean routeNavigation(Uri uri) {
        if (isLocal(uri)) {
            if (uri.getPath() != null && uri.getPath().startsWith("/app/update/")
                    && uri.getQuery() == null && uri.getFragment() == null) {
                // Buttons in the page's update card; the page itself never loads these.
                switch (uri.getPath()) {
                    case "/app/update/check": updater.check(true); return true;
                    case "/app/update/action": updater.action(); return true;
                    case "/app/update/dismiss": updater.dismiss(); return true;
                    default: return true;
                }
            }
            if (uri.getPath() != null && uri.getPath().matches("/share/[A-Za-z0-9_-]{5,30}")) {
                String code = uri.getLastPathSegment();
                Intent share = new Intent(Intent.ACTION_SEND).setType("text/plain")
                        .putExtra(Intent.EXTRA_TEXT, "https://www.instagram.com/reel/" + code + "/");
                try { startActivity(Intent.createChooser(share, "Share reel")); }
                catch (ActivityNotFoundException | SecurityException error) {
                    Toast.makeText(this, "Could not open sharing", Toast.LENGTH_SHORT).show();
                }
                return true;
            }
            if ("/feeds/export".equals(uri.getPath())) {
                chooseBackupDestination();
                return true;
            }
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
        if (isLocal(uri) && "/feeds/export".equals(path)) {
            chooseBackupDestination();
            return;
        }
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

    private void chooseBackupDestination() {
        if (choosingBackup) return;
        choosingBackup = true;
        Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT)
                .addCategory(Intent.CATEGORY_OPENABLE)
                .setType("application/json")
                .putExtra(Intent.EXTRA_TITLE, "ig-feeds.json")
                .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        try { startActivityForResult(save, SAVE_FEEDS_FILE); }
        catch (ActivityNotFoundException | SecurityException error) {
            choosingBackup = false;
            Toast.makeText(this, "Could not open the file picker", Toast.LENGTH_SHORT).show();
        }
    }

    private void writeFeedsBackup(Uri destination) {
        final String address = origin + "/feeds/export";
        final String accessToken = token;
        BACKEND.execute(() -> {
            HttpURLConnection connection = null;
            String message;
            try {
                connection = (HttpURLConnection) new URL(address).openConnection();
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(10000);
                connection.setReadTimeout(20000);
                connection.setRequestProperty("Cookie", "ig_android=" + accessToken);
                if (connection.getResponseCode() != 200) throw new IOException("Backup request failed");
                // Read and validate before opening the chosen destination, so
                // a backend failure cannot overwrite it with an error page.
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                try (InputStream input = connection.getInputStream()) {
                    byte[] buffer = new byte[8192];
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        if (bytes.size() + count > MAX_BACKUP_BYTES) throw new IOException("Backup is too large");
                        bytes.write(buffer, 0, count);
                    }
                }
                new JSONObject(bytes.toString("UTF-8"));
                try (OutputStream output = getContentResolver().openOutputStream(destination, "wt")) {
                    if (output == null) throw new IOException("Could not open backup destination");
                    bytes.writeTo(output);
                }
                message = "Backup exported";
            } catch (Exception error) {
                android.util.Log.e("ig", "Feed export failed", error);
                message = "Could not export backup. Try again.";
            } finally {
                if (connection != null) connection.disconnect();
            }
            final String result = message;
            runOnUiThread(() -> {
                if (!isFinishing() && !isDestroyed()) Toast.makeText(this, result, Toast.LENGTH_LONG).show();
            });
        });
    }

    /** A shared (Share → ig) or tapped Instagram link becomes the page to open. */
    private boolean acceptInstagramLink(Intent intent) {
        if (intent == null) return false;
        if (Intent.ACTION_VIEW.equals(intent.getAction())) return acceptTappedLink(intent.getData());
        return acceptSharedLink(intent);
    }

    private boolean acceptTappedLink(Uri uri) {
        if (uri == null) return false;
        String host = uri.getHost() == null ? "" : uri.getHost();
        Matcher post = POST_PATH.matcher(uri.getPath() == null ? "" : uri.getPath());
        if (INSTAGRAM_HOST.matcher(host).matches() && post.matches()) {
            pendingPath = "/reel/" + post.group(1);
            return true;
        }
        // Matched our link filter but isn't a single reel/post (e.g. /reels/audio/…):
        // hand it to the web browser rather than dropping it.
        openInBrowser(uri);
        return false;
    }

    private void openInBrowser(Uri uri) {
        Intent view = new Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE);
        // The selector forces a browser, so the link cannot loop back to ig.
        view.setSelector(Intent.makeMainSelectorActivity(Intent.ACTION_MAIN, Intent.CATEGORY_APP_BROWSER));
        try { startActivity(view); }
        catch (ActivityNotFoundException error) {
            android.util.Log.w("ig", "No browser for " + uri, error);
            Toast.makeText(this, "ig can only open Instagram reels and posts", Toast.LENGTH_LONG).show();
        }
    }

    /**
     * Android 12+ sends Instagram links to Instagram's verified app (or the browser)
     * until the user chooses ig under Open by default. Offer that once.
     */
    private void offerLinkHandling() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return;  // older versions ask per link
        SharedPreferences preferences = getSharedPreferences("ig", MODE_PRIVATE);
        if (preferences.getBoolean(LINK_PROMPT_SHOWN, false) || handlesInstagramLinks()) return;
        preferences.edit().putBoolean(LINK_PROMPT_SHOWN, true).apply();
        new AlertDialog.Builder(this)
                .setTitle("Open Instagram links in ig?")
                .setMessage("To play reel links you tap in other apps here, open ig's \"Open by default\" settings, "
                        + "tap \"Add link\" and select instagram.com. If the Instagram app is installed, turn off "
                        + "\"Open supported links\" for it first.")
                .setPositiveButton("Open settings", (dialog, which) -> openLinkSettings())
                .setNegativeButton("Not now", null)
                .show();
    }

    @android.annotation.TargetApi(Build.VERSION_CODES.S)
    private boolean handlesInstagramLinks() {
        try {
            DomainVerificationManager manager = getSystemService(DomainVerificationManager.class);
            if (manager == null) return false;
            DomainVerificationUserState state = manager.getDomainVerificationUserState(getPackageName());
            if (state == null || !state.isLinkHandlingAllowed()) return false;
            for (java.util.Map.Entry<String, Integer> host : state.getHostToStateMap().entrySet())
                if (INSTAGRAM_HOST.matcher(host.getKey()).matches()
                        && host.getValue() != DomainVerificationUserState.DOMAIN_STATE_NONE) return true;
            return false;
        } catch (PackageManager.NameNotFoundException | RuntimeException error) {
            android.util.Log.w("ig", "Could not read link handling state", error);
            return false;
        }
    }

    @android.annotation.TargetApi(Build.VERSION_CODES.S)
    private void openLinkSettings() {
        Uri app = Uri.parse("package:" + getPackageName());
        try { startActivity(new Intent(Settings.ACTION_APP_OPEN_BY_DEFAULT_SETTINGS, app)); }
        catch (ActivityNotFoundException error) {
            // Some phones lack the direct screen; app details still leads to it.
            try { startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, app)); }
            catch (ActivityNotFoundException fallback) {
                Toast.makeText(this, "Open Settings → Apps → ig → Open by default", Toast.LENGTH_LONG).show();
            }
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
        if (acceptInstagramLink(intent) && web != null && origin != null) web.loadUrl(origin + pendingPath);
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        if (web != null && web.getUrl() != null && isLocal(Uri.parse(web.getUrl()))) {
            Uri current = Uri.parse(web.getUrl());
            pendingPath = current.getEncodedPath();
            if (current.getEncodedQuery() != null) pendingPath += "?" + current.getEncodedQuery();
        }
        state.putString("path", pendingPath);
        state.putBoolean("choosingBackup", choosingBackup);
        if (pendingBackupDestination != null) state.putString("backupDestination", pendingBackupDestination.toString());
        super.onSaveInstanceState(state);
    }

    @Override protected void onPause() {
        updater.detach(this);
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
        updater.attach(this);
        updater.check(false);
    }

    private static final Pattern PERCENT = Pattern.compile("(\\d{1,3})%");

    /** The page draws the update card (common.js); this only describes it. */
    @Override public void onUpdateStatus(String message, String action, boolean canDismiss) {
        if (isFinishing() || isDestroyed()) return;
        if (message.isEmpty()) {
            updateNotice = "null";
        } else {
            try {
                JSONObject notice = new JSONObject().put("message", message).put("action", action)
                        .put("dismissible", canDismiss);
                Matcher percent = PERCENT.matcher(message);
                if (percent.find()) notice.put("progress", Math.min(100, Integer.parseInt(percent.group(1))));
                updateNotice = notice.toString();
            } catch (org.json.JSONException error) {
                android.util.Log.e("ig", "Could not describe update status", error);
                return;
            }
        }
        sendUpdateNotice();
    }

    private void sendUpdateNotice() {
        if (web != null) web.evaluateJavascript("window.igShowAppUpdate&&igShowAppUpdate(" + updateNotice + ")", null);
    }

    @Override public void onUpdateInfo(String message) {
        if (isFinishing() || isDestroyed()) return;
        if (web == null) { Toast.makeText(this, message, Toast.LENGTH_SHORT).show(); return; }
        try {
            String notice = new JSONObject().put("message", message).put("action", "").put("dismissible", false)
                    .put("done", true).toString();
            web.evaluateJavascript("window.igShowAppUpdate&&igShowAppUpdate(" + notice + ")", null);
        } catch (org.json.JSONException error) {
            Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
        }
    }

    @Override public void onUpdateInstall(File apk) {
        if (isFinishing() || isDestroyed() || paused) return;
        try {
            if (!updater.canInstall() && Build.VERSION.SDK_INT >= 26) {
                updater.awaitingPermission();
                Toast.makeText(this, "Allow updates from ig, then return here", Toast.LENGTH_LONG).show();
                startActivityForResult(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:" + getPackageName())), ALLOW_APP_UPDATES);
            } else {
                startActivityForResult(AppUpdater.installIntent(this, apk), INSTALL_APP_UPDATE);
            }
        } catch (ActivityNotFoundException | SecurityException | IOException error) {
            updater.installFinished("Couldn’t open Android’s installer · try again");
        }
    }

    @Override public void onBackPressed() {
        if (web == null) { super.onBackPressed(); return; }
        web.evaluateJavascript("(()=>{if(typeof exitCleanView==='function'&&exitCleanView())return true;if(typeof handleLocalBack==='function'&&handleLocalBack())return true;const d=document.querySelector('dialog[open]');if(d){d.close();return true;}const c=document.getElementById('comments-panel');if(c&&!c.hidden){closeComments();return true;}const f=document.getElementById('feed-menu');if(f&&f.open){f.open=false;return true;}return false;})()", handled -> {
            if ("true".equals(handled)) return;
            if (web != null && web.canGoBack()) web.goBack();
            else MainActivity.super.onBackPressed();
        });
    }

    @Override protected void onDestroy() {
        updater.detach(this);
        finishFileSelection(null);
        if (web != null) { web.destroy(); web = null; }
        super.onDestroy();
    }
}

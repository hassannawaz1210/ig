package io.github.hassannawaz1210.ig;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.os.Build;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.function.BooleanSupplier;

/** Strict release metadata and APK verification, independent of the update UI. */
final class UpdatePackage {
    static final String MANIFEST_URL = "https://github.com/hassannawaz1210/ig/releases/download/android-updates/update.json";
    static final long MAX_BYTES = 100L * 1024 * 1024;
    static final int MAX_MANIFEST_BYTES = 16 * 1024;
    final long versionCode;
    final String versionName;
    final String apkUrl;
    final String sha256;
    final long size;

    UpdatePackage(long versionCode, String versionName, String apkUrl, String sha256, long size) {
        this.versionCode = versionCode;
        this.versionName = versionName;
        this.apkUrl = apkUrl;
        this.sha256 = sha256;
        this.size = size;
    }

    static UpdatePackage parse(String text) throws Exception {
        if (text.length() > MAX_MANIFEST_BYTES) throw new IOException("Update metadata is too large");
        JSONObject data = new JSONObject(text);
        if (data.length() != 5) throw new IOException("Invalid update metadata fields");
        long version = integer(data, "version_code");
        long size = integer(data, "size");
        Object name = data.get("version_name"), url = data.get("apk_url"), hash = data.get("sha256");
        if (version < 1 || version > Integer.MAX_VALUE || size < 1 || size > MAX_BYTES
                || !(name instanceof String) || !((String) name).matches("[A-Za-z0-9][A-Za-z0-9._+() -]{0,79}")
                || !(url instanceof String) || !allowedApkUrl((String) url)
                || !(hash instanceof String) || !((String) hash).matches("[a-fA-F0-9]{64}"))
            throw new IOException("Invalid update metadata");
        return new UpdatePackage(version, (String) name, (String) url, ((String) hash).toLowerCase(Locale.ROOT), size);
    }

    private static long integer(JSONObject data, String key) throws Exception {
        Object value = data.get(key);
        if (!(value instanceof Integer) && !(value instanceof Long)) throw new IOException("Invalid " + key);
        return ((Number) value).longValue();
    }

    String json() throws Exception {
        return new JSONObject().put("version_code", versionCode).put("version_name", versionName)
                .put("apk_url", apkUrl).put("sha256", sha256).put("size", size).toString();
    }

    static boolean allowedApkUrl(String url) {
        try {
            URI uri = new URI(url);
            return secure(uri) && "github.com".equals(uri.getHost()) && uri.getRawQuery() == null
                    && uri.getRawPath().matches("/hassannawaz1210/ig/releases/download/android-build-[0-9]+-[0-9]+/[A-Za-z0-9._-]+\\.apk");
        } catch (Exception error) { return false; }
    }

    static boolean allowedRedirect(String url) {
        try {
            URI uri = new URI(url);
            if (!secure(uri)) return false;
            if ("github.com".equals(uri.getHost())) return MANIFEST_URL.equals(url) || allowedApkUrl(url);
            return ("release-assets.githubusercontent.com".equals(uri.getHost())
                    || "objects.githubusercontent.com".equals(uri.getHost()))
                    && uri.getRawPath().startsWith("/") && !uri.getRawPath().contains("\\");
        } catch (Exception error) { return false; }
    }

    private static boolean secure(URI uri) {
        return "https".equals(uri.getScheme()) && (uri.getPort() == -1 || uri.getPort() == 443)
                && uri.getRawUserInfo() == null && uri.getRawFragment() == null && uri.getRawPath() != null;
    }

    static byte[] readManifest(InputStream input) throws IOException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int read;
        while ((read = input.read(buffer)) != -1) {
            if (bytes.size() + read > MAX_MANIFEST_BYTES) throw new IOException("Update metadata is too large");
            bytes.write(buffer, 0, read);
        }
        return bytes.toByteArray();
    }

    interface Progress { void copied(long bytes); }

    void copyVerified(InputStream input, OutputStream output, BooleanSupplier cancelled, Progress progress) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] buffer = new byte[64 * 1024];
        long total = 0;
        int read;
        while ((read = input.read(buffer)) != -1) {
            if (cancelled.getAsBoolean()) throw new IOException("Update cancelled");
            total += read;
            if (total > size || total > MAX_BYTES) throw new IOException("Update is too large");
            digest.update(buffer, 0, read);
            if (output != null) output.write(buffer, 0, read);
            progress.copied(total);
        }
        if (cancelled.getAsBoolean()) throw new IOException("Update cancelled");
        if (total != size || !MessageDigest.isEqual(hex(digest.digest()).getBytes(java.nio.charset.StandardCharsets.US_ASCII),
                sha256.getBytes(java.nio.charset.StandardCharsets.US_ASCII))) throw new IOException("Update verification failed");
    }

    void verifyFile(Context context, File file) throws Exception {
        if (!file.isFile() || file.length() != size) throw new IOException("Update file is missing or incomplete");
        try (InputStream input = new FileInputStream(file)) {
            copyVerified(input, null, () -> false, ignored -> {});
        }
        verifyArchive(context, file);
    }

    void verifyArchive(Context context, File file) throws Exception {
        int flags = Build.VERSION.SDK_INT >= 28 ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        PackageInfo installed = context.getPackageManager().getPackageInfo(context.getPackageName(), flags);
        PackageInfo archive = context.getPackageManager().getPackageArchiveInfo(file.getAbsolutePath(), flags);
        if (archive == null) throw new IOException("Invalid update APK");
        verifyIdentity(archive.packageName, code(archive), archive.versionName, signers(archive),
                installed.packageName, code(installed), signers(installed));
    }

    void verifyIdentity(String packageName, long code, String name, Set<String> signatures,
                        String installedPackage, long installedCode, Set<String> installedSignatures) throws IOException {
        if (!installedPackage.equals(packageName) || code != versionCode || code <= installedCode
                || !versionName.equals(name) || signatures.isEmpty() || installedSignatures.isEmpty()
                || !signatures.equals(installedSignatures)) throw new IOException("Update does not match this app");
    }

    static long code(PackageInfo info) {
        return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
    }

    static Set<String> signers(PackageInfo info) throws Exception {
        Signature[] signatures = Build.VERSION.SDK_INT >= 28
                ? (info.signingInfo == null ? null : info.signingInfo.getApkContentsSigners()) : info.signatures;
        Set<String> result = new HashSet<>();
        if (signatures != null) for (Signature signature : signatures)
            result.add(hex(MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())));
        return result;
    }

    static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) result.append(String.format(Locale.ROOT, "%02x", value & 255));
        return result.toString();
    }
}

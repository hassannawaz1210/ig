package io.github.hassannawaz1210.ig;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Intent;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.Binder;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;

/** Test APK only: fixed following fixture and an in-memory backup destination. */
public final class FollowingFixtureProvider extends ContentProvider {
    static final Uri FILE = Uri.parse("content://io.github.hassannawaz1210.ig.test.following/following.json");
    static final Uri BACKUP = Uri.parse("content://io.github.hassannawaz1210.ig.test.following/ig-feeds.json");
    private volatile String savedBackup;
    private static final byte[] CONTENT = ("{\"relationships_following\":[{\"string_list_data\":["
            + "{\"href\":\"https://www.instagram.com/fixture_creator/\",\"value\":\"fixture_creator\"}]}]}")
            .getBytes(StandardCharsets.UTF_8);

    @Override public boolean onCreate() { return true; }
    @Override public String getType(Uri uri) { return "application/json"; }
    @Override public Bundle call(String method, String arg, Bundle extras) {
        if ("readBackup".equals(method)) {
            Bundle result = new Bundle();
            result.putString("backup", savedBackup);
            return result;
        }
        if (!"grant".equals(method)) throw new IllegalArgumentException("Unknown fixture method");
        savedBackup = null;
        // Like DocumentsProvider, access needs an explicit URI grant rather
        // than being public (public providers can make granting a no-op).
        long identity = Binder.clearCallingIdentity();
        try {
            getContext().grantUriPermission("io.github.hassannawaz1210.ig", FILE, Intent.FLAG_GRANT_READ_URI_PERMISSION);
            getContext().grantUriPermission("io.github.hassannawaz1210.ig", BACKUP, Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        } finally {
            Binder.restoreCallingIdentity(identity);
        }
        return Bundle.EMPTY;
    }
    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String order) {
        String[] columns = projection == null ? new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE} : projection;
        MatrixCursor cursor = new MatrixCursor(columns);
        Object[] values = new Object[columns.length];
        for (int i = 0; i < columns.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(columns[i])) values[i] = "following.json";
            if (OpenableColumns.SIZE.equals(columns[i])) values[i] = CONTENT.length;
        }
        cursor.addRow(values);
        return cursor;
    }
    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (BACKUP.equals(uri) && ("w".equals(mode) || "wt".equals(mode))) {
            try {
                ParcelFileDescriptor[] pipe = ParcelFileDescriptor.createPipe();
                new Thread(() -> {
                    try (ParcelFileDescriptor.AutoCloseInputStream input = new ParcelFileDescriptor.AutoCloseInputStream(pipe[0])) {
                        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                        byte[] buffer = new byte[4096];
                        int count;
                        while ((count = input.read(buffer)) != -1) {
                            if (bytes.size() + count > 5 * 1024 * 1024) throw new IOException("Test backup too large");
                            bytes.write(buffer, 0, count);
                        }
                        savedBackup = bytes.toString("UTF-8");
                    } catch (IOException ignored) { /* Test assertion reports missing output. */ }
                }, "backup-fixture").start();
                return pipe[1];
            } catch (IOException error) {
                throw new FileNotFoundException(error.toString());
            }
        }
        if (!FILE.equals(uri) || !"r".equals(mode)) throw new FileNotFoundException();
        try {
            ParcelFileDescriptor[] pipe = ParcelFileDescriptor.createPipe();
            new Thread(() -> {
                try (ParcelFileDescriptor.AutoCloseOutputStream output = new ParcelFileDescriptor.AutoCloseOutputStream(pipe[1])) {
                    output.write(CONTENT);
                } catch (IOException ignored) { /* Reader may cancel. */ }
            }, "following-fixture").start();
            return pipe[0];
        } catch (IOException error) {
            throw new FileNotFoundException(error.toString());
        }
    }
    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
}

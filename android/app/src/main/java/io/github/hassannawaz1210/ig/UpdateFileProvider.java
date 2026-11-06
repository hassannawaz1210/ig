package io.github.hassannawaz1210.ig;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;

/** Private, read-only provider for verified update APKs, granted to the installer. */
public final class UpdateFileProvider extends ContentProvider {
    @Override public boolean onCreate() { return true; }
    @Override public String getType(Uri uri) { return "application/vnd.android.package-archive"; }

    private File file(Uri uri) throws FileNotFoundException {
        if (!"content".equals(uri.getScheme()) || !(getContext().getPackageName() + ".updates").equals(uri.getAuthority())
                || uri.getQuery() != null || uri.getFragment() != null || uri.getPathSegments().size() != 1
                || !uri.getLastPathSegment().matches("[a-f0-9]{64}\\.apk")) throw new FileNotFoundException("Invalid update URI");
        try {
            File directory = AppUpdater.directory(getContext()).getCanonicalFile();
            File file = new File(directory, uri.getLastPathSegment()).getCanonicalFile();
            if (!directory.equals(file.getParentFile()) || !file.isFile()) throw new FileNotFoundException("Update not found");
            return file;
        } catch (IOException error) { throw new FileNotFoundException("Update not found"); }
    }

    @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) throw new FileNotFoundException("Updates are read-only");
        return ParcelFileDescriptor.open(file(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override public Cursor query(Uri uri, String[] projection, String selection, String[] args, String order) {
        final File apk;
        try { apk = file(uri); } catch (FileNotFoundException error) { throw new IllegalArgumentException("Invalid update URI"); }
        String[] columns = projection == null ? new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE} : projection;
        MatrixCursor result = new MatrixCursor(columns);
        Object[] values = new Object[columns.length];
        for (int i = 0; i < columns.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(columns[i])) values[i] = "ig-update.apk";
            if (OpenableColumns.SIZE.equals(columns[i])) values[i] = apk.length();
        }
        result.addRow(values);
        return result;
    }

    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
}

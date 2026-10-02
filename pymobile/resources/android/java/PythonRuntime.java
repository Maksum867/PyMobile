package org.pymobile.app;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.res.AssetManager;
import android.util.Log;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/**
 * Extracts the bundled Python runtime and application code, then starts the
 * interpreter through JNI.
 *
 * Assets are unpacked into the app's private storage, because CPython needs a
 * real filesystem for its standard library. The stamp file records *what* was
 * unpacked — the installed versionCode plus a digest of the packaged payload —
 * so an upgrade (Play / ``adb install -r``) re-extracts instead of keeping
 * stale Python on disk, and so does a rebuild that keeps the same versionCode.
 */
public class PythonRuntime {

    private static final String TAG = "pymobile";

    /** Where the interpreter is in this process's life cycle. */
    private enum State {
        /** Nothing started yet (or a previous run finished). */
        IDLE,
        /** Assets are being extracted. */
        PREPARING,
        /** The interpreter is executing the application. */
        RUNNING,
        /** Preparation failed; the next run() may retry. */
        FAILED
    }

    private static State state = State.IDLE;

    static {
        System.loadLibrary("pymobile");
    }

    private native int startPython(String home, String appDir, String entrypoint);

    private static native boolean pythonIsInitialized();

    /** Whether the interpreter is already initialised inside this process. */
    private static boolean pythonAlreadyRunning() {
        try {
            return pythonIsInitialized();
        } catch (Throwable error) {  // an older prebuilt bridge lacks the probe
            return false;
        }
    }

    /** Extract assets if needed and run the entry point on the calling thread. */
    public static synchronized int run(Context context, String entrypoint) {
        if (state == State.RUNNING || pythonAlreadyRunning()) {
            Log.w(TAG, "Python runtime already running");
            return 0;
        }
        // PREPARING and FAILED both continue: the first is a concurrent call
        // (it waited for the lock and the state is re-checked above), the
        // second is a retry after a recoverable failure. A plain boolean
        // "started" was set *before* extraction, so an IOException left it
        // true: every later run() answered "already running", returned 0 and
        // never started Python.
        state = State.PREPARING;

        final File root = new File(context.getFilesDir(), "pymobile");
        try {
            File stamp = new File(root, ".extracted");
            String version = currentVersionStamp(context);
            if (!version.equals(readStamp(stamp))) {
                Log.i(TAG, "extracting runtime for " + version + "…");
                deleteRecursively(root);
                extractAssetDir(context.getAssets(), "python", root);
                extractAssetDir(context.getAssets(), "app", root);
                writeStamp(stamp, version);
                Log.i(TAG, "extraction finished");
            }
        } catch (IOException error) {
            Log.e(TAG, "failed to prepare the Python runtime", error);
            // FAILED, not RUNNING: the next attempt (after the user fixes the
            // storage, or on the next activity) starts from scratch.
            state = State.FAILED;
            return 1;
        }

        File home = new File(root, "python");
        File appDir = new File(root, "app");
        File cert = new File(home, "etc/ssl/cert.pem");
        if (cert.isFile()) {
            // Visible from Java; the JNI bootstrap also exports SSL_CERT_FILE
            // into the interpreter environment so urllib/OpenSSL find the bundle.
            System.setProperty("javax.net.ssl.trustStore", cert.getAbsolutePath());
        }
        state = State.RUNNING;
        try {
            return new PythonRuntime().startPython(
                    home.getAbsolutePath(), appDir.getAbsolutePath(), entrypoint);
        } finally {
            // An Activity recreation re-runs this method in the same process;
            // the interpreter is finished (or failed) by now, so the state
            // must not stay RUNNING — a relaunch would get no Python at all.
            state = State.IDLE;
        }
    }

    /**
     * Identity of the extracted payload.
     *
     * It used to be the versionCode alone. A normal ``edit → build → adb
     * install -r`` keeps versionCode at 1 (it only has to grow for a Play
     * upload), so the APK was replaced and the app kept running the Python
     * files it had extracted from the *previous* build — a silent stale-code
     * bug behind a successful reinstall. The build writes a digest of the
     * packaged payload into ``assets/pymobile.properties``; mixing it into the
     * stamp makes the same versionCode with different code re-extract, without
     * inventing version numbers.
     */
    private static String currentVersionStamp(Context context) {
        String code = versionCodeOrUnknown(context);
        String payload = readBuildProperties(context.getAssets()).get("payload");
        return payload == null || payload.isEmpty() ? code : code + "-" + payload;
    }

    /** versionCode of the installed APK (the Play-visible part of the stamp). */
    private static String versionCodeOrUnknown(Context context) {
        try {
            PackageInfo info = context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0);
            // getLongVersionCode() exists from API 28; the deprecated field
            // is the only option below that.
            long code = android.os.Build.VERSION.SDK_INT >= 28
                    ? info.getLongVersionCode()
                    : info.versionCode;
            return Long.toString(code);
        } catch (PackageManager.NameNotFoundException error) {
            return "unknown";
        }
    }

    /**
     * The ``key=value`` pairs of ``assets/pymobile.properties``.
     *
     * The build writes the file (name, package, version, entrypoint…). It is
     * read here rather than hard-coding anything in Java: the entry point and
     * the form it was packaged in are build-time facts. An APK from an older
     * release has no such asset, hence the empty map.
     */
    static Map<String, String> readBuildProperties(AssetManager assets) {
        Map<String, String> values = new HashMap<String, String>();
        InputStream stream = null;
        try {
            stream = assets.open("pymobile.properties");
            BufferedReader reader = new BufferedReader(
                    new InputStreamReader(stream, StandardCharsets.UTF_8));
            String line;
            while ((line = reader.readLine()) != null) {
                int split = line.indexOf('=');
                if (split <= 0) {
                    continue;
                }
                values.put(line.substring(0, split).trim(), line.substring(split + 1).trim());
            }
        } catch (IOException error) {
            Log.w(TAG, "assets/pymobile.properties is missing: " + error);
        } finally {
            if (stream != null) {
                try {
                    stream.close();
                } catch (IOException ignored) {
                    // nothing useful to do while closing a read-only stream
                }
            }
        }
        return values;
    }

    /**
     * The file the launcher must run, as packaged.
     *
     * ``entrypoint = "startup.py"`` in pymobile.toml used to be ignored: the
     * activity called ``run(…, "main.py")`` and the APK contained no such
     * file. ``optimize = true`` had the same effect with the correct name —
     * the packaged file was ``main.pyc``. Both are read from the build
     * metadata here; the JNI runner also falls back to ``.pyc`` when the
     * source is absent.
     */
    static String entrypointFromAssets(AssetManager assets) {
        Map<String, String> values = readBuildProperties(assets);
        String name = values.get("entrypoint");
        if (name == null || name.isEmpty()) {
            return "main.py";
        }
        String kind = values.get("entrypoint_type");
        if ("pyc".equals(kind) && !name.endsWith(".pyc")) {
            return name + "c";
        }
        return name;
    }

    private static String readStamp(File stamp) {
        if (!stamp.isFile()) {
            return "";
        }
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(new FileInputStream(stamp), StandardCharsets.UTF_8))) {
            String line = reader.readLine();
            return line == null ? "" : line.trim();
        } catch (IOException error) {
            return "";
        }
    }

    private static void writeStamp(File stamp, String version) throws IOException {
        File parent = stamp.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IOException("cannot create " + parent);
        }
        try (OutputStream output = new FileOutputStream(stamp)) {
            output.write(version.getBytes(StandardCharsets.UTF_8));
        }
    }

    /** Recursively copy an asset directory into the private filesystem. */
    private static void extractAssetDir(AssetManager assets, String path, File targetRoot)
            throws IOException {
        String[] entries = assets.list(path);
        File target = new File(targetRoot, path);
        if (entries == null || entries.length == 0) {
            copyAsset(assets, path, target);
            return;
        }
        if (!target.exists() && !target.mkdirs()) {
            throw new IOException("cannot create " + target);
        }
        for (String name : entries) {
            extractAssetDir(assets, path + "/" + name, targetRoot);
        }
    }

    /** Copy a single asset file. */
    private static void copyAsset(AssetManager assets, String path, File target)
            throws IOException {
        File parent = target.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) {
            throw new IOException("cannot create " + parent);
        }
        try (InputStream input = assets.open(path);
             OutputStream output = new FileOutputStream(target)) {
            byte[] buffer = new byte[16384];
            int count;
            while ((count = input.read(buffer)) != -1) {
                output.write(buffer, 0, count);
            }
        }
    }

    /** Remove a directory tree. */
    private static void deleteRecursively(File file) {
        File[] children = file.listFiles();
        if (children != null) {
            for (File child : children) {
                deleteRecursively(child);
            }
        }
        file.delete();
    }
}

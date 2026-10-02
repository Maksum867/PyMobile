package org.pymobile.app;

import android.app.Activity;
import android.content.res.Configuration;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * The launcher activity.
 *
 * Owns the view container and runs the Python interpreter on a background
 * thread. Python pushes widget trees in through {@link #renderTree}; the views
 * are always built and swapped on the UI thread.
 */
public class MainActivity extends Activity {

    private static final String TAG = "pymobile";
    private static MainActivity instance;

    private FrameLayout container;
    private ViewBuilder builder;
    private final Handler ui = new Handler(Looper.getMainLooper());

    /**
     * One entry per permission request, keyed by its request code.
     *
     * A single static latch/flag pair used to be shared by every request, so
     * two overlapping requests (a job and a button handler) overwrote each
     * other's latch and one caller waited the full timeout or read the other
     * permission's answer.
     */
    private static final class PendingPermission {
        final CountDownLatch latch = new CountDownLatch(1);
        volatile boolean granted;
    }

    private static final ConcurrentHashMap<Integer, PendingPermission> pendingPermissions =
            new ConcurrentHashMap<>();
    private static final AtomicInteger nextPermissionRequest = new AtomicInteger(1000);

    /** The last tree rendered, replayed after a density/font-scale change. */
    private String lastJson;
    /** Configuration seen last, to tell which parts changed. */
    private Configuration lastConfiguration;
    /** OnBackInvokedCallback on API 33+ (typed Object so older ART never loads it). */
    private Object backCallback;

    /**
     * Released once the activity reaches the resumed state.
     *
     * Python starts from onCreate and may ask for a permission immediately.
     * requestPermissions() issued before the window is ready is dropped by the
     * system without ever showing a dialog, which looks like an instant denial.
     */
    private static final CountDownLatch resumedLatch = new CountDownLatch(1);

    /** The running activity, or null while it is being recreated. */
    static MainActivity current() {
        return instance;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        instance = this;

        container = new FrameLayout(this);
        container.setBackgroundColor(Color.WHITE);
        setContentView(container);
        builder = new ViewBuilder(this);
        lastConfiguration = new Configuration(getResources().getConfiguration());
        registerBackCallback();

        showPlaceholder("Starting Python…");

        new Thread(new Runnable() {
            @Override
            public void run() {
                // The entry point comes from the build (assets/pymobile.properties):
                // pymobile.toml may name any module, and optimize = true ships
                // bytecode. Hard-coding "main.py" here made such an APK install
                // fine and then fail with FileNotFoundError on start.
                String entrypoint = PythonRuntime.entrypointFromAssets(getAssets());
                int status = PythonRuntime.run(getApplicationContext(), entrypoint);
                Log.i(TAG, "python exited with " + status);
                if (status != 0) {
                    // The runner reports non-zero for a missing entry point and
                    // for an exception during startup (it used to print the
                    // traceback and return 0, so this placeholder never
                    // appeared and the app looked frozen on "Starting Python…").
                    postPlaceholder("Python exited with code " + status
                            + "\n\nRun `adb logcat -s pymobile.stderr` for details.");
                }
            }
        }, "python-main").start();
    }


        /** Build the tree off the JSON Python sent us and swap it in. */
    void renderTree(final String json) {
        ui.post(new Runnable() {
            @Override
            public void run() {
                try {
                    JSONObject root = new JSONObject(json);
                    lastJson = json;

                    // Colours are baked into views when they are built, so a
                    // new palette means a rebuild rather than a patch.
                    boolean themeChanged = builder.setTheme(root.optJSONObject("theme"));
                    container.setBackgroundColor(builder.backgroundColor());

                    // The screen is child 0; a snackbar, when shown, floats
                    // above it as the last child and is not part of the tree.
                    View existing = container.getChildCount() >= 1
                            && !builder.isSnackbar(container.getChildAt(0))
                            ? container.getChildAt(0)
                            : null;

                    // Patch the live views when the structure is unchanged:
                    // this preserves scroll position and keyboard focus.
                    if (!themeChanged && existing != null && builder.update(existing, root)) {
                        builder.syncSnackbar(container, root.optJSONObject("snackbar"));
                        return;
                    }

                    View view = builder.build(root);
                    for (int i = container.getChildCount() - 1; i >= 0; i--) {
                        if (!builder.isSnackbar(container.getChildAt(i))) {
                            container.removeViewAt(i);
                        }
                    }
                    container.addView(view, 0, new FrameLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.MATCH_PARENT
                    ));
                    builder.syncSnackbar(container, root.optJSONObject("snackbar"));
                } catch (Exception error) {
                    Log.e(TAG, "render failed", error);
                    showPlaceholder("Render error:\n" + error);
                }
            }
        });
    }

    /** Show a toast on the UI thread. */
    void showToast(final String message, final boolean longer) {
        ui.post(new Runnable() {
            @Override
            public void run() {
                DeviceServices.toast(MainActivity.this, message, longer);
            }
        });
    }

    /** Replace the content with a plain message (startup and error states). */
    private void showPlaceholder(String message) {
        TextView text = new TextView(this);
        text.setText(message);
        text.setTextColor(Color.parseColor("#444444"));
        text.setPadding(48, 64, 48, 48);
        container.removeAllViews();
        container.addView(text);
    }

    private void postPlaceholder(final String message) {
        ui.post(new Runnable() {
            @Override
            public void run() {
                showPlaceholder(message);
            }
        });
    }

    /**
     * Show a permission dialog and block the calling thread until answered.
     *
     * Called from the Python thread, never from the UI thread.
     */
    static boolean requestPermissionBlocking(final Activity activity, final String permission) {
        // The window must be up before a dialog can be shown.
        try {
            if (!resumedLatch.await(10, TimeUnit.SECONDS)) {
                Log.w(TAG, "activity not resumed; permission dialog may be skipped");
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
        }

        // Request codes must fit in 16 bits; wrap around well below that.
        final int code = 1000 + (nextPermissionRequest.getAndIncrement() % 30000);
        final PendingPermission request = new PendingPermission();
        pendingPermissions.put(code, request);
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                activity.requestPermissions(new String[]{permission}, code);
            }
        });
        try {
            // A generous cap: if the user never answers we must not hang forever.
            request.latch.await(120, TimeUnit.SECONDS);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
        } finally {
            pendingPermissions.remove(code);
        }
        return request.granted || DeviceServices.hasPermission(activity, permission);
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        PendingPermission request = pendingPermissions.get(requestCode);
        if (request != null) {
            request.granted = results.length > 0
                    && results[0] == android.content.pm.PackageManager.PERMISSION_GRANTED;
            request.latch.countDown();
        }
    }

    /**
     * Density, font scale, locale and layout direction are now handled here
     * (see configChanges in the manifest) instead of letting Android destroy
     * and recreate the activity — which stopped the Python event loop and
     * started the interpreter again, losing the navigation stack and every
     * piece of state that was not saved.
     */
    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        int changes = lastConfiguration == null ? 0xFFFFFFFF : newConfig.diff(lastConfiguration);
        lastConfiguration = new Configuration(newConfig);

        // dp/sp sizes are computed when views are built: rebuild at the new
        // density / font scale (patching would keep the old sizes).
        if ((changes & (ActivityInfoCompat.CONFIG_DENSITY | ActivityInfoCompat.CONFIG_FONT_SCALE)) != 0) {
            builder = new ViewBuilder(this);
            if (lastJson != null) {
                container.removeAllViews();
                renderTree(lastJson);
            }
        }
        // Tell Python about a new system language; apps that follow the
        // device language switch their translations on "app:locale".
        if ((changes & ActivityInfoCompat.CONFIG_LOCALE) != 0) {
            Native.dispatchEvent("", "locale", currentLanguage(newConfig));
        }
    }

    @SuppressWarnings("deprecation")
    private static String currentLanguage(Configuration config) {
        java.util.Locale locale = Build.VERSION.SDK_INT >= 24
                ? config.getLocales().get(0)
                : config.locale;
        return locale == null ? "" : locale.toLanguageTag();
    }

    /** android.content.pm.ActivityInfo flags used with Configuration.diff(). */
    private static final class ActivityInfoCompat {
        static final int CONFIG_LOCALE = android.content.pm.ActivityInfo.CONFIG_LOCALE;
        static final int CONFIG_DENSITY = android.content.pm.ActivityInfo.CONFIG_DENSITY;
        static final int CONFIG_FONT_SCALE = android.content.pm.ActivityInfo.CONFIG_FONT_SCALE;
    }

    @Override
    protected void onResume() {
        super.onResume();
        resumedLatch.countDown();
    }

    /**
     * Route back gestures into Python.
     *
     * onBackPressed() is deprecated from API 33 and ignored once predictive
     * back is enabled (android:enableOnBackInvokedCallback="true", mandatory
     * with targetSdk 36), so an OnBackInvokedCallback is registered there;
     * older releases keep using onBackPressed().
     */
    private void registerBackCallback() {
        if (Build.VERSION.SDK_INT >= 33) {
            android.window.OnBackInvokedCallback callback = new android.window.OnBackInvokedCallback() {
                @Override
                public void onBackInvoked() {
                    Native.dispatchEvent("", "back", "");
                }
            };
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT, callback);
            backCallback = callback;
        }
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        Native.dispatchEvent("", "back", "");
    }

    /** Finish the activity from the Python thread (arrives via JNI). */
    static void finishApp() {
        final MainActivity activity = instance;
        if (activity != null) {
            activity.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    activity.finish();
                }
            });
        }
    }

    @Override
    protected void onNewIntent(android.content.Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        // The Python runtime is already running; a tap on a notification (or a
        // re-launch with SINGLE_TOP) must NOT re-run Python, which would crash.
        // Just ensure the activity is in the foreground.
    }

    @Override
    protected void onDestroy() {
        if (Build.VERSION.SDK_INT >= 33 && backCallback != null) {
            getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(
                    (android.window.OnBackInvokedCallback) backCallback);
            backCallback = null;
        }
        super.onDestroy();
        if (instance == this) {
            instance = null;
        }
        Native.stopEventLoop();
    }
}

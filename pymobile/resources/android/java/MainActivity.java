package org.pymobile.app;

import android.app.Activity;
import android.app.ActivityManager;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Bitmap;
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
    /** App.name received from Python; config.name remains the manifest fallback. */
    private String appTitle;
    /** Configuration seen last, to tell which parts changed. */
    private Configuration lastConfiguration;
    /** OnBackInvokedCallback on API 33+ (typed Object so older ART never loads it). */
    private Object backCallback;
    /** Whether Python reported a screen to go back to (frame ``can_go_back``). */
    private boolean canGoBack;
    /** Last system light/dark state, sent to Python as a "theme" event. */
    private boolean lastDark;
    /** Whether the keyboard is up (a WindowInsets event already reported). */
    private boolean keyboardVisible;

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

        container = new FrameLayout(this) {
            @Override
            public android.view.WindowInsets onApplyWindowInsets(android.view.WindowInsets insets) {
                // Applying the padding here (and not returning CONSUMED) keeps
                // the insets flowing to children, so SafeArea still sees them.
                applySystemInsets(insets);
                return super.onApplyWindowInsets(insets);
            }
        };
        container.setBackgroundColor(startupBackground());
        container.setFitsSystemWindows(true);
        setContentView(container);
        // Edge-to-edge is the default on Android 15+ (targetSdk 35 and up):
        // every frame is drawn behind the status and navigation bars, and the
        // keyboard is not accounted for either. The padding below is what keeps
        // content out of both. `adjustResize` makes the window shrink for the
        // IME; without it the keyboard simply covered the focused field.
        getWindow().setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        builder = new ViewBuilder(this);
        lastConfiguration = new Configuration(getResources().getConfiguration());

        showPlaceholder("Starting Python…");
        lastDark = systemIsDark();

        if (PythonRuntime.isRunning()) {
            // The process outlived its Activity (the usual "back, then open
            // again" and a configuration change that is not in configChanges).
            // Python is still alive with the whole application state in it, so
            // the only correct thing to do is hand it the new window — restart
            // it and every counter, screen and job is lost.
            Native.dispatchEvent("", "__lifecycle__", "resume");
            return;
        }

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
                    String requestedTitle = root.optString("app_title", "");
                    if (!requestedTitle.isEmpty() && !requestedTitle.equals(appTitle)) {
                        appTitle = requestedTitle;
                        setTitle(requestedTitle);
                        if (Build.VERSION.SDK_INT >= 21) {
                            setTaskDescription(new ActivityManager.TaskDescription(
                                    requestedTitle, (Bitmap) null));
                        }
                    }

                    JSONObject chrome = root.optJSONObject("chrome");
                    if (chrome != null) {
                        setCanGoBack(chrome.optBoolean("can_go_back", false));
                    }

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
                    JSONObject navigation = root.optJSONObject("navigation");
                    boolean screenChanged = navigation != null;
                    if (screenChanged) {
                        // Navigation metadata is a one-frame command; do not
                        // replay the transition after a configuration change.
                        root.remove("navigation");
                        lastJson = root.toString();
                    } else {
                        lastJson = json;
                    }

                    // A navigation frame is a different screen even when its
                    // root happens to reuse the same generated widget id. Never
                    // patch that tree into the outgoing screen: build it, then
                    // animate the swap. Ordinary state updates still patch in
                    // place and preserve focus/scroll.
                    if (!screenChanged && !themeChanged && existing != null
                            && builder.update(existing, root)) {
                        builder.syncSnackbar(container, root.optJSONObject("snackbar"));
                        return;
                    }

                    View view = builder.build(root);
                    // Cancel any earlier transition and discard its outgoing
                    // screen. The latest complete tree always wins.
                    for (int i = container.getChildCount() - 1; i >= 0; i--) {
                        View child = container.getChildAt(i);
                        if (builder.isSnackbar(child) || child == existing) {
                            continue;
                        }
                        child.animate().cancel();
                        container.removeViewAt(i);
                    }
                    FrameLayout.LayoutParams matchParent = new FrameLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.MATCH_PARENT
                    );
                    if (screenChanged && existing != null) {
                        // Attach first so ViewPropertyAnimator has a window token
                        // and can start on the next frame.
                        container.addView(view, 0, matchParent);
                        if (!animateScreenChange(existing, view, navigation)) {
                            container.removeView(existing);
                        }
                    } else {
                        if (existing != null) {
                            container.removeView(existing);
                        }
                        container.addView(view, 0, matchParent);
                    }
                    builder.syncSnackbar(container, root.optJSONObject("snackbar"));
                } catch (Exception error) {
                    Log.e(TAG, "render failed", error);
                    showPlaceholder("Render error:\n" + error);
                }
            }
        });
    }

    /**
     * Animate a screen swap requested by ``App(..., transition=...)``.
     * Returns false for disabled/zero-duration transitions so the caller can
     * replace the old view immediately.
     */
    private boolean animateScreenChange(
            final View outgoing, final View incoming, JSONObject navigation) {
        String kind = navigation.optString("type", "none");
        int duration = Math.max(0, Math.min(navigation.optInt("duration_ms", 220), 2000));
        if (duration == 0 || "none".equals(kind)) {
            return false;
        }
        final boolean reverse = navigation.optBoolean("reverse", false);
        outgoing.animate().cancel();
        incoming.animate().cancel();
        incoming.setVisibility(View.VISIBLE);

        if ("slide".equals(kind)) {
            float width = Math.max(1, container.getWidth());
            incoming.setTranslationX(reverse ? -width : width);
            incoming.setAlpha(1f);
            outgoing.setTranslationX(0f);
            incoming.animate().translationX(0f).setDuration(duration).start();
            outgoing.animate().translationX(reverse ? width / 4f : -width / 4f)
                    .setDuration(duration).withEndAction(new Runnable() {
                        @Override
                        public void run() {
                            container.removeView(outgoing);
                            incoming.setTranslationX(0f);
                        }
                    }).start();
            return true;
        }

        if ("fade_through".equals(kind)) {
            // Material's "fade through": the old screen fades out and shrinks
            // slightly, the new one fades in and grows. Two beats, not one
            // cross-fade, which is what makes it read as a change of context.
            int first = Math.max(1, duration / 2);
            incoming.setAlpha(0f);
            incoming.setScaleX(0.92f);
            incoming.setScaleY(0.92f);
            incoming.setVisibility(View.INVISIBLE);
            incoming.setPivotX(incoming.getWidth() / 2f);
            incoming.setPivotY(incoming.getHeight() / 2f);
            outgoing.animate().alpha(0f).scaleX(0.92f).scaleY(0.92f)
                    .setDuration(first).withEndAction(new Runnable() {
                        @Override
                        public void run() {
                            incoming.setVisibility(View.VISIBLE);
                            incoming.animate().alpha(1f).scaleX(1f).scaleY(1f)
                                    .setDuration(duration - first).start();
                            container.removeView(outgoing);
                            outgoing.setAlpha(1f);
                            outgoing.setScaleX(1f);
                            outgoing.setScaleY(1f);
                        }
                    }).start();
            return true;
        }

        if ("slide_up".equals(kind) || "slide_down".equals(kind)) {
            // A detail screen rises from the bottom; going back sends it down.
            float height = Math.max(1, container.getHeight());
            boolean down = "slide_down".equals(kind) || reverse;
            incoming.setTranslationY(down ? -height / 6f : height);
            incoming.setAlpha(1f);
            incoming.animate().translationY(0f).setDuration(duration).start();
            outgoing.animate().alpha(0f).setDuration(duration)
                    .withEndAction(new Runnable() {
                        @Override
                        public void run() {
                            container.removeView(outgoing);
                            incoming.setTranslationY(0f);
                            outgoing.setAlpha(1f);
                        }
                    }).start();
            return true;
        }

        if ("scale".equals(kind)) {
            incoming.setPivotX(incoming.getWidth() / 2f);
            incoming.setPivotY(incoming.getHeight() / 2f);
            incoming.setAlpha(0f);
            incoming.setScaleX(0.96f);
            incoming.setScaleY(0.96f);
            incoming.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(duration).start();
            outgoing.animate().alpha(0f).scaleX(1.02f).scaleY(1.02f)
                    .setDuration(duration).withEndAction(new Runnable() {
                        @Override
                        public void run() {
                            container.removeView(outgoing);
                            incoming.setAlpha(1f);
                            incoming.setScaleX(1f);
                            incoming.setScaleY(1f);
                        }
                    }).start();
            return true;
        }

        // Unknown transition names degrade to a fade, not a blank screen.
        incoming.setAlpha(0f);
        incoming.setTranslationX(0f);
        outgoing.animate().alpha(0f).setDuration(duration).withEndAction(new Runnable() {
            @Override
            public void run() {
                container.removeView(outgoing);
                incoming.setAlpha(1f);
            }
        }).start();
        incoming.animate().alpha(1f).setDuration(duration).start();
        return true;
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
    /**
     * Keep content inside the system bars and above the keyboard.
     *
     * On API 30+ ``getInsets`` covers status, navigation, cutout and IME in one
     * call; older releases fall back to the deprecated system-window insets,
     * which is why ``SafeArea`` alone used to be the only thing that helped.
     */
    private void applySystemInsets(android.view.WindowInsets insets) {
        int left;
        int top;
        int right;
        int bottom;
        if (Build.VERSION.SDK_INT >= 30) {
            android.graphics.Insets bars = insets.getInsets(
                    android.view.WindowInsets.Type.systemBars()
                            | android.view.WindowInsets.Type.displayCutout());
            android.graphics.Insets ime = insets.getInsets(android.view.WindowInsets.Type.ime());
            left = bars.left;
            top = bars.top;
            right = bars.right;
            bottom = Math.max(bars.bottom, ime.bottom);
        } else {
            left = insets.getSystemWindowInsetLeft();
            top = insets.getSystemWindowInsetTop();
            right = insets.getSystemWindowInsetRight();
            bottom = Math.max(insets.getSystemWindowInsetBottom(), 0);
        }
        if (container.getPaddingLeft() == left && container.getPaddingTop() == top
                && container.getPaddingRight() == right && container.getPaddingBottom() == bottom) {
            return;
        }
        container.setPadding(left, top, right, bottom);
        // The IME is a separate question from the bars: tell Python so a screen
        // can scroll the focused field into view without guessing the height.
        boolean keyboardUp = Build.VERSION.SDK_INT >= 30
                && insets.isVisible(android.view.WindowInsets.Type.ime());
        if (keyboardUp != keyboardVisible) {
            keyboardVisible = keyboardUp;
            Native.dispatchEvent("", "insets", String.valueOf(bottom));
        }
    }

    /** Light or dark, following the system: no white flash in dark mode. */
    private boolean systemIsDark() {
        int mode = getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK;
        return mode == Configuration.UI_MODE_NIGHT_YES;
    }

    /** The colour under "Starting Python…", taken from the current system theme. */
    private int startupBackground() {
        return systemIsDark() ? Color.parseColor("#121212") : Color.WHITE;
    }

    /** Placeholder text colour that stays readable in both themes. */
    private int placeholderTextColor() {
        return systemIsDark() ? Color.parseColor("#E0E0E0") : Color.parseColor("#444444");
    }

    private void showPlaceholder(String message) {
        TextView text = new TextView(this);
        text.setText(message);
        text.setTextColor(placeholderTextColor());
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
    /**
     * Picker answers arrive here: the file pickers used to have no path back
     * into Python at all, so every "choose a file" flow was a dead end.
     */
    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        MediaServices.onPickResult(requestCode, resultCode, data);
    }

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
        // uiMode is in configChanges, so the activity survives a light/dark
        // switch — which used to mean Python never heard about it at all and
        // the app stayed light inside a dark system.
        if ((changes & ActivityInfoCompat.CONFIG_UI_MODE) != 0) {
            container.setBackgroundColor(startupBackground());
            Native.dispatchEvent("", "theme", systemIsDark() ? "dark" : "light");
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
        static final int CONFIG_UI_MODE = android.content.pm.ActivityInfo.CONFIG_UI_MODE;
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
     *
     * The callback is registered only while the app has a screen to go back to.
     * A permanently registered callback swallows the gesture on the root
     * screen too, so the system's own "leave the app" animation never played
     * and back looked like a no-op. When Python reports ``can_go_back: false``
     * the callback is removed and the platform handles the gesture.
     */
    private void registerBackCallback() {
        if (Build.VERSION.SDK_INT < 33 || backCallback != null) {
            return;
        }
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

    /** Register or drop the back callback to match the reported depth. */
    private void setCanGoBack(boolean value) {
        canGoBack = value;
        if (value) {
            registerBackCallback();
        } else if (Build.VERSION.SDK_INT >= 33 && backCallback != null) {
            getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(
                    (android.window.OnBackInvokedCallback) backCallback);
            backCallback = null;
        }
    }

    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        // On the root screen the platform's default (finish the task) is the
        // right answer; intercepting it made back appear to do nothing.
        if (!canGoBack) {
            super.onBackPressed();
            return;
        }
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

    /**
     * Tear the window down without tearing the interpreter down.
     *
     * This used to stop Python (and the native side then called Py_Finalize),
     * which meant every Activity recreation — back on the root screen, a
     * process that survived in the background, "Don't keep activities", a
     * configuration change outside configChanges — discarded all application
     * state and re-ran the app from main.py.
     *
     * Now the interpreter is told to stop *handling input* for this window and
     * save what it needs (``on_stop`` → ``App.save_state``), and stays alive:
     * the next onCreate() resumes it. ``finishApp()`` (the app's own exit) and
     * a task that leaves the process with nothing to keep are the only cases
     * where Python is not needed any more.
     */
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
        // An activity that is finishing is not coming back to this process:
        // stop the interpreter, otherwise a background thread of a finished app
        // keeps it awake for no one. A pure configuration change is *not* a
        // finish, and the process stays warm on purpose.
        if (!isChangingConfigurations()) {
            PythonRuntime.stopForRecreate();
        }
    }
}

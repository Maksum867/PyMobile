package org.pymobile.app;

import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;
import android.util.Log;
import android.widget.Toast;

/**
 * Android platform features: vibration, notifications and runtime permissions.
 *
 * Every method tolerates a null activity, because Python may call in while the
 * activity is being recreated.
 */
final class DeviceServices {

    private static final String TAG = "pymobile";
    private static final String CHANNEL_ID = "pymobile.default";

    private DeviceServices() {
    }

    // -- vibration --------------------------------------------------------

    private static Vibrator vibrator(Context context) {
        if (context == null) {
            return null;
        }
        if (Build.VERSION.SDK_INT >= 31) {
            VibratorManager manager =
                    (VibratorManager) context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE);
            return manager == null ? null : manager.getDefaultVibrator();
        }
        return (Vibrator) context.getSystemService(Context.VIBRATOR_SERVICE);
    }

    /**
     * Vibrate once.
     *
     * Implemented as a one-entry waveform rather than createOneShot: on several
     * devices (Pixel among them) createOneShot with DEFAULT_AMPLITUDE is
     * silently ignored, while waveforms are honoured. Very short pulses are
     * also stretched, because anything under ~50 ms is imperceptible on most
     * hardware and looks like a bug to the user.
     */
    static void vibrate(Context context, long milliseconds, int amplitude) {
        long duration = Math.max(milliseconds, MIN_PERCEPTIBLE_MS);
        vibratePattern(context, new long[]{0, duration}, -1, amplitude);
    }

    /** Below this, a pulse is not reliably felt on typical hardware. */
    private static final long MIN_PERCEPTIBLE_MS = 50;

    static void vibratePattern(Context context, long[] pattern, int repeat) {
        vibratePattern(context, pattern, repeat, 255);
    }

    static void vibratePattern(Context context, long[] pattern, int repeat, int amplitude) {
        Vibrator vibrator = vibrator(context);
        if (vibrator == null || pattern == null || pattern.length == 0) {
            return;
        }
        // -1 means "the device default"; everything else is an explicit
        // 1..255 strength that must be honoured.
        int buzz = (amplitude == -1) ? 255 : amplitude;
        if (Build.VERSION.SDK_INT >= 26) {
            // Explicit amplitudes: some devices treat the default as "off".
            int[] amplitudes = new int[pattern.length];
            for (int i = 0; i < pattern.length; i++) {
                amplitudes[i] = (i % 2 == 0) ? 0 : buzz;
            }
            try {
                vibrator.vibrate(VibrationEffect.createWaveform(pattern, amplitudes, repeat));
            } catch (IllegalArgumentException error) {
                // Devices without amplitude control reject the array form.
                vibrator.vibrate(VibrationEffect.createWaveform(pattern, repeat));
            }
        } else {
            vibrator.vibrate(pattern, repeat);
        }
    }

    static void cancelVibration(Context context) {
        Vibrator vibrator = vibrator(context);
        if (vibrator != null) {
            vibrator.cancel();
        }
    }

    // -- notifications ----------------------------------------------------

    private static NotificationManager notificationManager(Context context) {
        if (context == null) {
            return null;
        }
        return (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
    }

    /** Create (or refresh) the notification channel with the configured identity. */
    static void ensureChannel(Context context, String channelId, String channelName,
            int importance) {
        if (Build.VERSION.SDK_INT < 26) {
            return;  // channels do not exist below Android 8
        }
        NotificationManager manager = notificationManager(context);
        if (manager == null) {
            return;
        }
        String id = (channelId == null || channelId.isEmpty()) ? CHANNEL_ID : channelId;
        String name = (channelName == null || channelName.isEmpty()) ? "General" : channelName;
        int level = (importance <= 0) ? NotificationManager.IMPORTANCE_DEFAULT : importance;
        manager.createNotificationChannel(new NotificationChannel(id, name, level));
    }

    static void notify(Context context, String title, String body, int id, boolean ongoing,
            String channelId, String channelName, String smallIcon) {
        Log.i(TAG, "notify() called: title=" + title + " id=" + id + " context=" + (context != null));
        NotificationManager manager = notificationManager(context);
        if (manager == null) {
            Log.w(TAG, "notify() abort: NotificationManager is null");
            return;
        }
        String channel = (channelId == null || channelId.isEmpty()) ? CHANNEL_ID : channelId;
        Log.i(TAG, "notify() channel=" + channel + " channelName=" + channelName);
        ensureChannel(context, channel, channelName, NotificationManager.IMPORTANCE_DEFAULT);

        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            builder = new Notification.Builder(context, channel);
        } else {
            builder = new Notification.Builder(context);
        }
        int iconRes = iconResource(context, smallIcon);
        Log.i(TAG, "notify() iconRes=" + iconRes + " smallIcon=" + smallIcon);
        builder.setContentTitle(title)
                .setContentText(body)
                .setOngoing(ongoing)
                .setAutoCancel(!ongoing)
                .setSmallIcon(iconRes);
        // Tapping the notification should bring the running app to the front
        // (not restart it, which would re-init Python and crash). Using
        // SINGLE_TOP + SINGLE_TASK + CURRENT_TASK reuses the existing activity.
        android.app.PendingIntent contentIntent =
                android.app.PendingIntent.getActivity(
                        context,
                        0,
                        new android.content.Intent(context, MainActivity.class)
                                .setFlags(android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP
                                        | android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP
                                        | android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
                        android.app.PendingIntent.FLAG_UPDATE_CURRENT
                                | android.app.PendingIntent.FLAG_IMMUTABLE);
        builder.setContentIntent(contentIntent);
        try {
            Notification notification = builder.build();
            Log.i(TAG, "notify() posting notification id=" + id);
            manager.notify(id, notification);
            Log.i(TAG, "notify() SUCCESS id=" + id);
        } catch (SecurityException error) {
            Log.e(TAG, "notify() FAILED: POST_NOTIFICATIONS not granted", error);
        } catch (Exception error) {
            Log.e(TAG, "notify() FAILED with exception", error);
        }
    }

    static void cancelNotification(Context context, int id) {
        NotificationManager manager = notificationManager(context);
        if (manager != null) {
            manager.cancel(id);
        }
    }

    /**
     * Resolve the notification small icon: a custom one passed from Python if
     * provided, otherwise the launcher icon, falling back to a platform
     * drawable.
     */
    private static int iconResource(Context context, String smallIcon) {
        String name = (smallIcon == null || smallIcon.isEmpty()) ? "icon" : smallIcon;
        int found = context.getResources().getIdentifier(
                name, "mipmap", context.getPackageName());
        if (found == 0) {
            found = context.getResources().getIdentifier(
                    name, "drawable", context.getPackageName());
        }
        return found != 0 ? found : android.R.drawable.ic_dialog_info;
    }

    // -- permissions ------------------------------------------------------

    static boolean hasPermission(Context context, String permission) {
        if (context == null) {
            return false;
        }
        if (Build.VERSION.SDK_INT < 23) {
            return true;
        }
        return context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    /**
     * Show the system dialog and wait for the user's answer.
     *
     * requestPermissions() is asynchronous. Returning immediately meant Python
     * re-checked the permission before the user had tapped anything and always
     * saw "denied", so this blocks the calling (Python) thread until
     * MainActivity reports the result.
     */
    static boolean requestPermission(Activity activity, String permission) {
        if (activity == null) {
            return false;
        }
        if (Build.VERSION.SDK_INT < 23 || hasPermission(activity, permission)) {
            return true;
        }
        return MainActivity.requestPermissionBlocking(activity, permission);
    }

    // -- misc -------------------------------------------------------------

    /** Open a URL in the system browser. */
    static boolean openUrl(Context context, String url) {
        if (context == null || url == null || url.isEmpty()) {
            return false;
        }
        try {
            android.content.Intent intent = new android.content.Intent(
                    android.content.Intent.ACTION_VIEW,
                    android.net.Uri.parse(url));
            intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(intent);
            return true;
        } catch (Exception error) {
            Log.w(TAG, "could not open url: " + url, error);
            return false;
        }
    }

    static void toast(Activity activity, String message, boolean longer) {
        if (activity == null) {
            return;
        }
        Toast.makeText(activity, message,
                longer ? Toast.LENGTH_LONG : Toast.LENGTH_SHORT).show();
    }

    /**
     * The language the device is set to, as a BCP-47 tag such as "uk-UA".
     *
     * Read from the configuration rather than Locale.getDefault() so that a
     * per-app language override (Android 13's per-app languages) is honoured.
     * Returns an empty string when it cannot be determined, which tells the
     * Python side to fall back to its own detection.
     */
    static String deviceLanguage(Context context) {
        java.util.Locale locale = null;
        if (context != null) {
            android.content.res.Configuration config =
                    context.getResources().getConfiguration();
            if (Build.VERSION.SDK_INT >= 24) {
                android.os.LocaleList locales = config.getLocales();
                if (!locales.isEmpty()) {
                    locale = locales.get(0);
                }
            } else {
                locale = config.locale;
            }
        }
        if (locale == null) {
            locale = java.util.Locale.getDefault();
        }
        if (locale == null) {
            return "";
        }
        String language = locale.getLanguage();
        if (language == null || language.isEmpty()) {
            return "";
        }
        String country = locale.getCountry();
        return (country == null || country.isEmpty()) ? language : language + "-" + country;
    }


    // -- clipboard ---------------------------------------------------------

    /**
     * Put text on the system clipboard.
     *
     * Android 13 shows its own "copied" confirmation when the app writes to the
     * clipboard, so the framework does not toast on top of it (an app can still
     * say something itself). Returns false when there is nothing to copy or the
     * clipboard service is unavailable, which a caller can report honestly
     * instead of claiming a copy that never happened.
     */
    static boolean copyToClipboard(Context context, String text) {
        if (context == null || text == null || text.isEmpty()) {
            return false;
        }
        try {
            ClipboardManager manager =
                    (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
            if (manager == null) {
                return false;
            }
            manager.setPrimaryClip(ClipData.newPlainText("text", text));
            return true;
        } catch (Exception error) {
            Log.w(TAG, "could not copy to the clipboard", error);
            return false;
        }
    }

    /** Whatever the clipboard holds, or an empty string. */
    static String clipboardText(Context context) {
        if (context == null) {
            return "";
        }
        try {
            ClipboardManager manager =
                    (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
            if (manager == null || !manager.hasPrimaryClip()) {
                return "";
            }
            ClipData clip = manager.getPrimaryClip();
            if (clip == null || clip.getItemCount() == 0) {
                return "";
            }
            CharSequence text = clip.getItemAt(0).coerceToText(context);
            return text == null ? "" : text.toString();
        } catch (Exception error) {
            // Android 10+ refuses clipboard reads to apps that are not in the
            // foreground; that is a policy, not a bug, so it stays quiet.
            Log.i(TAG, "clipboard is not readable right now");
            return "";
        }
    }

    // -- share sheet -------------------------------------------------------

    /**
     * Open the system share sheet.
     *
     * ``url`` travels both as the body text and as an ``EXTRA_TEXT`` link, so a
     * target that only reads the body (SMS, a QR scanner) still gets the link,
     * while a browser or a messenger shows a preview. The chooser is created
     * with FLAG_ACTIVITY_NEW_TASK because it may be started from the Python
     * thread, which has no activity context of its own.
     */
    static boolean share(Context context, String text, String url, String subject) {
        if (context == null) {
            return false;
        }
        String body = text == null ? "" : text;
        String link = url == null ? "" : url;
        if (body.isEmpty() && link.isEmpty()) {
            return false;
        }
        try {
            android.content.Intent send = new android.content.Intent(
                    android.content.Intent.ACTION_SEND);
            send.setType("text/plain");
            send.putExtra(android.content.Intent.EXTRA_TEXT,
                    body.isEmpty() ? link : body);
            if (!link.isEmpty()) {
                // Some targets read only the link stream.
                send.putExtra(android.content.Intent.EXTRA_STREAM, link);
            }
            if (subject != null && !subject.isEmpty()) {
                send.putExtra(android.content.Intent.EXTRA_SUBJECT, subject);
            }
            android.content.Intent chooser =
                    android.content.Intent.createChooser(send, subject);
            chooser.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(chooser);
            return true;
        } catch (Exception error) {
            Log.w(TAG, "could not open the share sheet", error);
            return false;
        }
    }
}

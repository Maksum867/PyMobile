package org.pymobile.app;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.media.MediaPlayer;
import android.media.MediaRecorder;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Media and file features: the system pickers, the camera, playback, recording.
 *
 * Like {@link DeviceServices}, every method tolerates a null activity — Python
 * may call in while the activity is being recreated — and returns "nothing
 * happened" rather than throwing across the JNI boundary, where an exception
 * would arrive as an opaque error.
 *
 * The two pickers block the calling (Python) thread until the user answers.
 * That is deliberate: it matches how the framework's permission dialog already
 * behaves, and it lets app code be written top to bottom
 * (``file = app.pick_file()``) instead of as a continuation.
 */
final class MediaServices {

    private static final String TAG = "pymobile";

    /** Request codes; distinct from the permission codes (1000+). */
    private static final int REQUEST_FILE = 4000;
    private static final int REQUEST_PHOTO = 4001;

    private MediaServices() {
    }

    /** One answer to a picker: the result, or null when the user cancelled. */
    private static final class PendingPick {
        final CountDownLatch latch = new CountDownLatch(1);
        volatile List<String> paths = new ArrayList<>();
    }

    private static final ConcurrentHashMap<Integer, PendingPick> pendingPicks =
            new ConcurrentHashMap<>();
    private static final AtomicInteger nextPickRequest = new AtomicInteger(REQUEST_FILE);

    // -- the pickers -------------------------------------------------------

    /**
     * Ask the user for files and copy the selection into the app's cache.
     *
     * A content:// URI stops being readable once the activity is gone, and the
     * framework hands Python plain paths, so every picked file is copied (its
     * display name preserved) into cacheDir/picked. That makes a picked file
     * behave exactly like a packaged asset: a path, readable whenever.
     */
    static String[] pickFile(Activity activity, String mimeTypes, boolean multiple) {
        if (activity == null) {
            return new String[0];
        }
        Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        String type = mimeTypes == null ? "" : mimeTypes.trim();
        if (type.isEmpty()) {
            type = "*/*";
        }
        // GET_CONTENT takes one type; several are expressed through
        // EXTRA_MIME_TYPES, which every file provider understands.
        if (type.contains(",")) {
            String[] parts = type.split(",");
            intent.setType("*/*");
            intent.putExtra(Intent.EXTRA_MIME_TYPES, parts);
        } else {
            intent.setType(type);
        }
        if (multiple) {
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        }
        return runPicker(activity, intent, REQUEST_FILE);
    }

    /** Take a photo with the camera app, or pick one from the gallery. */
    static String[] pickImage(Activity activity) {
        if (activity == null) {
            return new String[0];
        }
        File target = new File(pickDirectory(activity), "photo-" + System.currentTimeMillis() + ".jpg");
        Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("image/*");
        // No camera intent: it needs a FileProvider and a manifest entry the
        // scaffold does not ship, and GET_CONTENT already offers the camera on
        // most launchers. The target file is kept for a future camera path.
        target.delete();
        return runPicker(activity, intent, REQUEST_PHOTO);
    }

    private static String[] runPicker(final Activity activity, final Intent intent, int code) {
        final int request = code + (nextPickRequest.getAndIncrement() % 1000);
        final PendingPick pending = new PendingPick();
        pendingPicks.put(request, pending);
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    activity.startActivityForResult(intent, request);
                } catch (Exception error) {
                    Log.w(TAG, "could not open the picker", error);
                    pending.latch.countDown();
                }
            }
        });
        try {
            // A picker the user leaves open is not a hang: ten minutes is long
            // enough for a real choice and short enough to fail visibly.
            pending.latch.await(600, TimeUnit.SECONDS);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
        } finally {
            pendingPicks.remove(request);
        }
        return pending.paths.toArray(new String[0]);
    }

    /** Called from {@code MainActivity.onActivityResult} for a picker answer. */
    static void onPickResult(int requestCode, int resultCode, Intent data) {
        PendingPick pending = pendingPicks.get(requestCode);
        if (pending == null) {
            return;
        }
        try {
            MainActivity activity = MainActivity.current();
            if (resultCode == Activity.RESULT_OK && data != null && activity != null) {
                pending.paths = copySelection(activity, data);
            }
        } catch (Exception error) {
            Log.w(TAG, "could not read the picker result", error);
        } finally {
            pending.latch.countDown();
        }
    }

    private static List<String> copySelection(Context context, Intent data) {
        List<Uri> uris = new ArrayList<>();
        if (data.getClipData() != null) {
            for (int i = 0; i < data.getClipData().getItemCount(); i++) {
                Uri uri = data.getClipData().getItemAt(i).getUri();
                if (uri != null) {
                    uris.add(uri);
                }
            }
        } else if (data.getData() != null) {
            uris.add(data.getData());
        }
        List<String> paths = new ArrayList<>();
        for (Uri uri : uris) {
            try {
                paths.add(copyToCache(context, uri));
            } catch (Exception error) {
                Log.w(TAG, "could not copy a picked file", error);
            }
        }
        return paths;
    }

    private static File pickDirectory(Context context) {
        File directory = new File(context.getCacheDir(), "picked");
        if (!directory.isDirectory() && !directory.mkdirs()) {
            Log.w(TAG, "could not create the picker cache directory");
        }
        return directory;
    }

    /**
     * Copy a picked URI into the cache and return the absolute path.
     *
     * The display name is kept (sanitised, de-duplicated) because an app that
     * shows the file it got — "voice note.m4a" — reads better than one that
     * shows a random number.
     */
    private static String copyToCache(Context context, Uri uri) throws Exception {
        String name = displayName(context, uri);
        File directory = pickDirectory(context);
        File target = new File(directory, name);
        int copy = 1;
        String stem = name;
        int dot = name.lastIndexOf('.');
        if (dot > 0) {
            stem = name.substring(0, dot);
        }
        String extension = dot > 0 ? name.substring(dot) : "";
        while (target.exists() && copy < 100) {
            target = new File(directory, stem + "-" + copy + extension);
            copy++;
        }
        InputStream input = context.getContentResolver().openInputStream(uri);
        if (input == null) {
            return "";
        }
        try {
            FileOutputStream output = new FileOutputStream(target);
            try {
                byte[] buffer = new byte[8192];
                int read;
                while ((read = input.read(buffer)) > 0) {
                    output.write(buffer, 0, read);
                }
            } finally {
                output.close();
            }
        } finally {
            input.close();
        }
        return target.getAbsolutePath();
    }

    private static String displayName(Context context, Uri uri) {
        String fallback = "picked-" + System.currentTimeMillis();
        String name = fallback;
        Cursor cursor = null;
        try {
            cursor = context.getContentResolver().query(uri, null, null, null, null);
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0) {
                    String value = cursor.getString(index);
                    if (value != null && !value.isEmpty()) {
                        name = value;
                    }
                }
            }
        } catch (Exception error) {
            Log.i(TAG, "the provider did not offer a display name");
        } finally {
            if (cursor != null) {
                cursor.close();
            }
        }
        // A path separator in a name would escape the cache directory.
        return name.replace('/', '_').replace('\\', '_');
    }

    // -- playback ----------------------------------------------------------

    /**
     * One prepared {@link MediaPlayer} per Python AudioPlayer.
     *
     * Prepared asynchronously so a ``play()`` never blocks the UI thread on a
     * network source, and polled by Python for position/duration rather than
     * pushed as callbacks (which arrive on a Java thread Python would have to
     * re-enter the interpreter from).
     */
    static final class Player {
        private final MediaPlayer player = new MediaPlayer();
        private volatile boolean prepared = false;
        private float volume = 1.0f;

        Player(Context context, String source, boolean looping) {
            try {
                player.setAudioStreamType(android.media.AudioManager.STREAM_MUSIC);
                if (source.startsWith("http://") || source.startsWith("https://")) {
                    player.setDataSource(source);
                } else {
                    String path = resolvePath(context, source);
                    player.setDataSource(path);
                }
                player.setLooping(looping);
                player.setVolume(volume, volume);
                player.setOnPreparedListener(new MediaPlayer.OnPreparedListener() {
                    @Override
                    public void onPrepared(MediaPlayer mp) {
                        prepared = true;
                    }
                });
                player.prepareAsync();
            } catch (Exception error) {
                Log.w(TAG, "could not open audio " + source, error);
            }
        }

        /**
         * Turn an app-relative source into an absolute path.
         *
         * ``assets/beep.wav`` is how a project names a file it packaged, and
         * the build puts project assets next to the code inside the APK's
         * files directory, so a relative path is resolved against it.
         */
        private static String resolvePath(Context context, String source) {
            if (source.startsWith("file://")) {
                return Uri.parse(source).getPath();
            }
            File file = new File(source);
            if (file.isAbsolute()) {
                return source;
            }
            File base = context.getFilesDir();
            File candidate = new File(base, source);
            return candidate.exists() ? candidate.getAbsolutePath() : file.getAbsolutePath();
        }

        void play() {
            try {
                player.start();
            } catch (IllegalStateException error) {
                // Not prepared yet: retry once it is, instead of dropping the tap.
                player.setOnPreparedListener(new MediaPlayer.OnPreparedListener() {
                    @Override
                    public void onPrepared(MediaPlayer mp) {
                        prepared = true;
                        mp.start();
                    }
                });
            }
        }

        void pause() {
            try {
                player.pause();
            } catch (IllegalStateException error) {
                Log.i(TAG, "pause before prepare is ignored");
            }
        }

        void stop() {
            try {
                player.stop();
            } catch (IllegalStateException error) {
                Log.i(TAG, "stop before start is ignored");
            }
        }

        void seek(int positionMs) {
            try {
                player.seekTo(positionMs);
            } catch (IllegalStateException error) {
                Log.i(TAG, "seek before prepare is ignored");
            }
        }

        int position() {
            try {
                return player.getCurrentPosition();
            } catch (IllegalStateException error) {
                return 0;
            }
        }

        int duration() {
            try {
                return player.getDuration();
            } catch (IllegalStateException error) {
                return 0;
            }
        }

        boolean isPlaying() {
            try {
                return prepared && player.isPlaying();
            } catch (IllegalStateException error) {
                return false;
            }
        }

        void setVolume(float level) {
            volume = Math.max(0f, Math.min(1f, level));
            try {
                player.setVolume(volume, volume);
            } catch (IllegalStateException error) {
                Log.i(TAG, "volume before prepare is kept for later");
            }
        }

        void setLooping(boolean looping) {
            try {
                player.setLooping(looping);
            } catch (IllegalStateException error) {
                Log.i(TAG, "looping before prepare is ignored");
            }
        }

        void release() {
            try {
                player.release();
            } catch (Exception error) {
                Log.i(TAG, "releasing the player failed", error);
            }
        }
    }

    // -- player handles ----------------------------------------------------

    /**
     * Players and recorders live in this class and Python holds an int handle.
     *
     * A released handle is removed from the map, so a stale Python object can
     * only ever get "nothing happened" instead of touching a freed player —
     * the same reason the permission path keeps its futures here.
     */
    private static final ConcurrentHashMap<Integer, Player> players = new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<Integer, Recorder> recorders = new ConcurrentHashMap<>();
    private static final AtomicInteger nextHandle = new AtomicInteger(1);

    static int open(Context context, String source, boolean looping) {
        try {
            int handle = nextHandle.getAndIncrement();
            players.put(handle, new Player(context, source, looping));
            return handle;
        } catch (Exception error) {
            Log.w(TAG, "could not open audio " + source, error);
            return -1;
        }
    }

    static Player get(int handle) {
        return players.get(handle);
    }

    static void release(int handle) {
        Player player = players.remove(handle);
        if (player != null) {
            player.release();
        }
    }

    static int startRecording(Context context, String directory) {
        int handle = nextHandle.getAndIncrement();
        Recorder recorder = new Recorder();
        recorders.put(handle, recorder);
        if (recorder.start(context, directory).isEmpty()) {
            recorders.remove(handle);
            return -1;
        }
        return handle;
    }

    static String stopRecording(int handle) {
        Recorder recorder = recorders.remove(handle);
        return recorder == null ? "" : recorder.stop();
    }

    static void cancelRecording(int handle) {
        Recorder recorder = recorders.remove(handle);
        if (recorder != null) {
            recorder.cancel();
        }
    }

    static boolean isRecording(int handle) {
        Recorder recorder = recorders.get(handle);
        return recorder != null && recorder.isRecording();
    }

    static int elapsed(int handle) {
        Recorder recorder = recorders.get(handle);
        return recorder == null ? 0 : recorder.elapsedMs();
    }

    // -- recording ---------------------------------------------------------

    /** A microphone recorder that always yields a file the app can keep. */
    static final class Recorder {
        private MediaRecorder recorder;
        private File target;
        private long startedAt;
        private boolean recording;

        String start(Context context, String directory) {
            if (recording) {
                return target == null ? "" : target.getAbsolutePath();
            }
            File base = directory == null || directory.isEmpty()
                    ? new File(context.getFilesDir(), "recordings")
                    : new File(directory);
            if (!base.isDirectory() && !base.mkdirs()) {
                Log.w(TAG, "could not create the recording directory");
                return "";
            }
            target = new File(base, "recording-" + System.currentTimeMillis() + ".m4a");
            recorder = new MediaRecorder();
            try {
                recorder.setAudioSource(MediaRecorder.AudioSource.MIC);
                recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
                recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
                recorder.setAudioEncodingBitRate(64000);
                recorder.setAudioSamplingRate(44100);
                recorder.setOutputFile(target.getAbsolutePath());
                recorder.prepare();
                recorder.start();
            } catch (Exception error) {
                Log.w(TAG, "could not start recording", error);
                releaseQuietly();
                return "";
            }
            startedAt = System.currentTimeMillis();
            recording = true;
            return target.getAbsolutePath();
        }

        String stop() {
            if (!recording) {
                return "";
            }
            recording = false;
            try {
                recorder.stop();
            } catch (RuntimeException error) {
                // stop() throws when nothing was captured; the file is then
                // useless, so it is removed rather than handed to the app.
                Log.w(TAG, "recording was too short to keep", error);
                releaseQuietly();
                if (target != null) {
                    target.delete();
                }
                return "";
            }
            releaseQuietly();
            return target == null ? "" : target.getAbsolutePath();
        }

        void cancel() {
            recording = false;
            releaseQuietly();
            if (target != null) {
                target.delete();
            }
            target = null;
        }

        boolean isRecording() {
            return recording;
        }

        int elapsedMs() {
            return recording ? (int) (System.currentTimeMillis() - startedAt) : 0;
        }

        private void releaseQuietly() {
            if (recorder == null) {
                return;
            }
            try {
                recorder.reset();
                recorder.release();
            } catch (Exception error) {
                Log.i(TAG, "releasing the recorder failed", error);
            }
            recorder = null;
        }
    }
}

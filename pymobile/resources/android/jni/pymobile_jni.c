/* PyMobile JNI bridge.
 *
 * Two directions of traffic:
 *
 *   Python → Java   through the built-in module `_pymobile_android`
 *                   (render, toast, vibrate, notify, permissions);
 *   Java   → Python through `Native.dispatchEvent`, which pushes UI events
 *                   into a queue the Python thread blocks on.
 *
 * The Python interpreter runs on its own thread, so every JNI call attaches
 * that thread to the JVM, and `next_event` releases the GIL while waiting.
 */

#include <android/log.h>
#include <errno.h>
#include <jni.h>
#include <pthread.h>
#include <Python.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/time.h>
#include <unistd.h>

#define TAG "pymobile"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

static JavaVM *g_vm = NULL;
static jclass g_native_class = NULL;

/* ------------------------------------------------------------------ */
/* Text conversion: UTF-16 (JVM) ↔ UTF-8 (CPython)                     */
/* ------------------------------------------------------------------ */

/* Do NOT use GetStringUTFChars/NewStringUTF for application text.
 *
 * Those speak *Modified* UTF-8: a character outside the BMP (any emoji, most
 * historic scripts) travels as a CESU-8 surrogate pair (ED A0 BD ED B8 80 for
 * U+1F600). Python's strict UTF-8 decoder rejects those bytes, so an emoji
 * typed into a TextInput raised UnicodeDecodeError inside next_event() and the
 * event loop died; the same bytes sent the other way rendered as mojibake.
 * Both directions are converted here by hand, and every queued string carries
 * an explicit length: a decoded event may legitimately contain U+0000, which
 * no NUL-terminated representation can carry. */

typedef struct {
    char *data;   /* UTF-8 bytes, NUL-terminated *and* length-delimited */
    size_t len;
} Utf8Text;

static Utf8Text text_empty(void) {
    Utf8Text text = {NULL, 0};
    return text;
}

static void text_free(Utf8Text *text) {
    free(text->data);
    text->data = NULL;
    text->len = 0;
}

static int text_copy(Utf8Text *target, const char *data, size_t len) {
    target->data = (char *)malloc(len + 1);
    if (!target->data) {
        target->len = 0;
        return 0;
    }
    if (len) {
        memcpy(target->data, data, len);
    }
    target->data[len] = '\0';
    target->len = len;
    return 1;
}

/* Append one code point (0..0x10FFFF) as UTF-8; returns the byte count. */
static size_t utf8_encode(uint32_t code, char *out) {
    if (code <= 0x7F) {
        out[0] = (char)code;
        return 1;
    }
    if (code <= 0x7FF) {
        out[0] = (char)(0xC0 | (code >> 6));
        out[1] = (char)(0x80 | (code & 0x3F));
        return 2;
    }
    if (code <= 0xFFFF) {
        out[0] = (char)(0xE0 | (code >> 12));
        out[1] = (char)(0x80 | ((code >> 6) & 0x3F));
        out[2] = (char)(0x80 | (code & 0x3F));
        return 3;
    }
    out[0] = (char)(0xF0 | (code >> 18));
    out[1] = (char)(0x80 | ((code >> 12) & 0x3F));
    out[2] = (char)(0x80 | ((code >> 6) & 0x3F));
    out[3] = (char)(0x80 | (code & 0x3F));
    return 4;
}

/* Java string → fresh standard UTF-8 with an explicit length. */
static Utf8Text jstring_to_utf8(JNIEnv *env, jstring value) {
    Utf8Text text = text_empty();
    if (!value) {
        return text;
    }
    jsize units = (*env)->GetStringLength(env, value);
    if (units <= 0) {
        return text;
    }
    const jchar *chars = (*env)->GetStringChars(env, value, NULL);
    if (!chars) {
        return text;
    }
    /* Worst case four bytes per unit; a surrogate pair collapses to one. */
    char *buffer = (char *)malloc((size_t)units * 4 + 1);
    if (buffer) {
        size_t used = 0;
        for (jsize i = 0; i < units; i++) {
            uint32_t code = chars[i];
            if (code >= 0xD800 && code <= 0xDBFF && i + 1 < units
                    && chars[i + 1] >= 0xDC00 && chars[i + 1] <= 0xDFFF) {
                code = 0x10000 + ((code - 0xD800) << 10) + (chars[i + 1] - 0xDC00);
                i++;
            } else if (code >= 0xD800 && code <= 0xDFFF) {
                /* Lone surrogate: no standard UTF-8 form exists. */
                code = 0xFFFD;
            }
            used += utf8_encode(code, buffer + used);
        }
        buffer[used] = '\0';
        text.data = buffer;
        text.len = used;
    }
    (*env)->ReleaseStringChars(env, value, chars);
    return text;
}

/* Python UTF-8 (length-delimited) → Java string; invalid bytes → U+FFFD. */
static jstring utf8_to_jstring(JNIEnv *env, const char *data, size_t len) {
    jchar *units = (jchar *)malloc(len * sizeof(jchar) + sizeof(jchar));
    if (!units) {
        return NULL;
    }
    size_t used = 0;
    size_t i = 0;
    while (i < len) {
        unsigned char byte = (unsigned char)data[i];
        uint32_t code = 0xFFFD;
        size_t width = 1;
        if (byte < 0x80) {
            code = byte;
        } else if ((byte & 0xE0) == 0xC0) {
            width = 2;
            code = byte & 0x1F;
        } else if ((byte & 0xF0) == 0xE0) {
            width = 3;
            code = byte & 0x0F;
        } else if ((byte & 0xF8) == 0xF0) {
            width = 4;
            code = byte & 0x07;
        }
        if (width > 1) {
            if (i + width > len) {
                code = 0xFFFD;
            } else {
                for (size_t k = 1; k < width; k++) {
                    unsigned char next = (unsigned char)data[i + k];
                    if ((next & 0xC0) != 0x80) {
                        code = 0xFFFD;
                        width = 1;
                        break;
                    }
                    code = (code << 6) | (next & 0x3F);
                }
                if (width > 1 && (code > 0x10FFFF
                        || (code >= 0xD800 && code <= 0xDFFF))) {
                    code = 0xFFFD;
                }
            }
        }
        if (code <= 0xFFFF) {
            units[used++] = (jchar)code;
        } else {
            code -= 0x10000;
            units[used++] = (jchar)(0xD800 + (code >> 10));
            units[used++] = (jchar)(0xDC00 + (code & 0x3FF));
        }
        i += width;
    }
    jstring result = (*env)->NewString(env, units, (jsize)used);
    free(units);
    return result;
}

/* ------------------------------------------------------------------ */
/* Event queue: Java UI thread → Python thread                         */
/* ------------------------------------------------------------------ */

typedef struct Event {
    Utf8Text widget_id;
    Utf8Text type;
    Utf8Text value;
    struct Event *next;
} Event;

static Event *q_head = NULL;
static Event *q_tail = NULL;
static int q_count = 0;
static pthread_mutex_t q_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t q_cond = PTHREAD_COND_INITIALIZER;
static int q_stopped = 0;

/* The UI thread must never block on the interpreter, and a burst of events
 * (fast scrolling, a slow handler) must not grow memory without bound. Past
 * this many pending events the *oldest* one is dropped: input belonging to a
 * screen the app has already left is the least valuable item in the queue.
 * Dropping the newest would lose the tap the user just made; blocking the UI
 * thread would freeze the app. */
#define MAX_PENDING_EVENTS 1024

static void event_free(Event *event) {
    text_free(&event->widget_id);
    text_free(&event->type);
    text_free(&event->value);
    free(event);
}

static void queue_push(const char *widget_id, size_t widget_id_len, const char *type,
                       size_t type_len, const char *value, size_t value_len) {
    Event *event = (Event *)calloc(1, sizeof(Event));
    if (!event) {
        return;
    }
    if (!text_copy(&event->widget_id, widget_id, widget_id_len)
            || !text_copy(&event->type, type, type_len)
            || !text_copy(&event->value, value, value_len)) {
        /* Out of memory: drop the event instead of dereferencing NULL later. */
        LOGE("dropping event: out of memory");
        event_free(event);
        return;
    }

    pthread_mutex_lock(&q_mutex);
    if (q_count >= MAX_PENDING_EVENTS && q_head) {
        Event *oldest = q_head;
        q_head = oldest->next;
        if (!q_head) {
            q_tail = NULL;
        }
        q_count--;
        event_free(oldest);
        LOGE("event queue is full (%d pending): dropping the oldest event", MAX_PENDING_EVENTS);
    }

    if (q_tail) {
        q_tail->next = event;
    } else {
        q_head = event;
    }
    q_tail = event;
    q_count++;
    pthread_cond_signal(&q_cond);
    pthread_mutex_unlock(&q_mutex);
}


/* ------------------------------------------------------------------ */
/* Stdio → logcat                                                      */
/* ------------------------------------------------------------------ */

static const int MAX_BYTES_PER_WRITE = 4000;

typedef struct {
    int fd;
    android_LogPriority priority;
    const char *tag;
    int pipe[2];
} StreamInfo;

static StreamInfo STREAMS[] = {
    {STDOUT_FILENO, ANDROID_LOG_INFO, "pymobile.stdout", {-1, -1}},
    {STDERR_FILENO, ANDROID_LOG_WARN, "pymobile.stderr", {-1, -1}},
    {-1, ANDROID_LOG_UNKNOWN, NULL, {-1, -1}},
};

static void *stream_reader(void *arg) {
    StreamInfo *si = (StreamInfo *)arg;
    char buf[MAX_BYTES_PER_WRITE + 1];
    ssize_t count;
    while ((count = read(si->pipe[0], buf, MAX_BYTES_PER_WRITE)) > 0) {
        buf[count] = '\0';
        __android_log_write(si->priority, si->tag, buf);
    }
    return NULL;
}

static void redirect_stdio_to_logcat(void) {
    for (StreamInfo *si = STREAMS; si->tag; si++) {
        FILE *file = (si->fd == STDOUT_FILENO) ? stdout : stderr;
        setvbuf(file, NULL, _IOLBF, 0);
        if (pipe(si->pipe) != 0) {
            LOGE("stdio redirect failed (pipe): %s", strerror(errno));
            return;
        }
        if (dup2(si->pipe[1], si->fd) == -1) {
            LOGE("stdio redirect failed (dup2): %s", strerror(errno));
            close(si->pipe[0]);
            close(si->pipe[1]);
            return;
        }
        close(si->pipe[1]);  /* the writer end now lives at si->fd */
        pthread_t thread;
        if (pthread_create(&thread, NULL, stream_reader, si) == 0) {
            pthread_detach(thread);
        }
    }
}

/* ------------------------------------------------------------------ */
/* Helpers for calling static Java methods                             */
/* ------------------------------------------------------------------ */

/* Look up a static method of Native. A missing method leaves a pending
 * NoSuchMethodError, and calling any further JNI function with a pending
 * exception is undefined behaviour (CheckJNI aborts the process) — so it is
 * logged and cleared here, once, for every caller. */
static jmethodID static_method(JNIEnv *env, const char *name, const char *signature) {
    jmethodID method = (*env)->GetStaticMethodID(env, g_native_class, name, signature);
    if (!method) {
        LOGE("method not found: %s%s", name, signature);
        (*env)->ExceptionClear(env);
    }
    return method;
}

/* Attach the calling thread and return its JNIEnv. */
static JNIEnv *jni_env(int *attached) {
    JNIEnv *env = NULL;
    *attached = 0;
    if (!g_vm) {
        return NULL;
    }
    if ((*g_vm)->GetEnv(g_vm, (void **)&env, JNI_VERSION_1_6) == JNI_EDETACHED) {
        if ((*g_vm)->AttachCurrentThread(g_vm, &env, NULL) != JNI_OK) {
            LOGE("AttachCurrentThread failed");
            return NULL;
        }
        *attached = 1;
    }
    return env;
}

static void call_void_method(const char *name, const char *signature, ...) {
    int attached = 0;
    JNIEnv *env = jni_env(&attached);
    if (!env || !g_native_class) {
        return;
    }
    jmethodID method = static_method(env, name, signature);
    if (method) {
        va_list args;
        va_start(args, signature);
        (*env)->CallStaticVoidMethodV(env, g_native_class, method, args);
        va_end(args);
        if ((*env)->ExceptionCheck(env)) {
            (*env)->ExceptionDescribe(env);
            (*env)->ExceptionClear(env);
        }
    }
    if (attached) {
        (*g_vm)->DetachCurrentThread(g_vm);
    }
}

/* ------------------------------------------------------------------ */
/* Built-in module `_pymobile_android`                                 */
/* ------------------------------------------------------------------ */

static PyObject *py_render(PyObject *self, PyObject *args) {
    const char *json;
    (void)self;
    if (!PyArg_ParseTuple(args, "s", &json)) {
        return NULL;
    }
    int attached = 0;
    JNIEnv *env = jni_env(&attached);
    if (env && g_native_class) {
        jstring jjson = utf8_to_jstring(env, json, strlen(json));
        jmethodID method =
            static_method(env, "render", "(Ljava/lang/String;)V");
        if (method) {
            (*env)->CallStaticVoidMethod(env, g_native_class, method, jjson);
            if ((*env)->ExceptionCheck(env)) {
                (*env)->ExceptionDescribe(env);
                (*env)->ExceptionClear(env);
            }
        }
        (*env)->DeleteLocalRef(env, jjson);
        if (attached) {
            (*g_vm)->DetachCurrentThread(g_vm);
        }
    }
    Py_RETURN_NONE;
}

/* Generic helper: call a static Java method taking one string. */
static PyObject *call_with_string(const char *name, const char *signature, const char *value,
                                  int extra_bool, int has_bool) {
    int attached = 0;
    JNIEnv *env = jni_env(&attached);
    if (env && g_native_class) {
        jstring jvalue = utf8_to_jstring(env, value, strlen(value));
        jmethodID method = static_method(env, name, signature);
        if (method) {
            if (has_bool) {
                (*env)->CallStaticVoidMethod(env, g_native_class, method, jvalue,
                                             (jboolean)extra_bool);
            } else {
                (*env)->CallStaticVoidMethod(env, g_native_class, method, jvalue);
            }
            if ((*env)->ExceptionCheck(env)) {
                (*env)->ExceptionDescribe(env);
                (*env)->ExceptionClear(env);
            }
        }
        (*env)->DeleteLocalRef(env, jvalue);
        if (attached) {
            (*g_vm)->DetachCurrentThread(g_vm);
        }
    }
    Py_RETURN_NONE;
}

static PyObject *py_toast(PyObject *self, PyObject *args) {
    const char *message;
    int longer = 0;
    (void)self;
    if (!PyArg_ParseTuple(args, "s|p", &message, &longer)) {
        return NULL;
    }
    return call_with_string("toast", "(Ljava/lang/String;Z)V", message, longer, 1);
}

static PyObject *py_vibrate(PyObject *self, PyObject *args) {
    long milliseconds = 0;
    long amplitude = -1;
    (void)self;
    if (!PyArg_ParseTuple(args, "l|l", &milliseconds, &amplitude)) {
        return NULL;
    }
    call_void_method("vibrate", "(JI)V", (jlong)milliseconds, (jint)amplitude);
    Py_RETURN_NONE;
}

static PyObject *py_vibrate_pattern(PyObject *self, PyObject *args) {
    PyObject *sequence;
    int repeat = -1;
    (void)self;
    if (!PyArg_ParseTuple(args, "O|i", &sequence, &repeat)) {
        return NULL;
    }
    Py_ssize_t length = PySequence_Size(sequence);
    if (length < 0) {
        return NULL;
    }
    int attached = 0;
    JNIEnv *env = jni_env(&attached);
    if (env && g_native_class) {
        jlongArray array = (*env)->NewLongArray(env, (jsize)length);
        if (!array) {  /* OOM — nothing else to do */
            if (attached) {
                (*g_vm)->DetachCurrentThread(g_vm);
            }
            Py_RETURN_NONE;
        }
        jlong *values = NULL;
        if (length > 0) {
            values = (jlong *)malloc(sizeof(jlong) * (size_t)length);
            if (!values) {
                (*env)->DeleteLocalRef(env, array);
                if (attached) {
                    (*g_vm)->DetachCurrentThread(g_vm);
                }
                Py_RETURN_NONE;
            }
        }
        int failed = 0;
        for (Py_ssize_t i = 0; i < length && !failed; i++) {
            PyObject *item = PySequence_GetItem(sequence, i);
            if (item == NULL) {
                failed = 1;
                break;
            }
            values[i] = (jlong)PyLong_AsLong(item);
            Py_DECREF(item);
            if (PyErr_Occurred()) {  /* non-integer element */
                failed = 1;
                break;
            }
        }
        if (!failed) {
            (*env)->SetLongArrayRegion(env, array, 0, (jsize)length, values);
            jmethodID method =
                static_method(env, "vibratePattern", "([JI)V");
            if (method) {
                (*env)->CallStaticVoidMethod(env, g_native_class, method, array, (jint)repeat);
                if ((*env)->ExceptionCheck(env)) {
                    (*env)->ExceptionClear(env);
                }
            }
        }
        free(values);
        (*env)->DeleteLocalRef(env, array);
        if (attached) {
            (*g_vm)->DetachCurrentThread(g_vm);
        }
        if (failed) {
            return NULL;  /* a Python exception is already set */
        }
    }
    Py_RETURN_NONE;
}

static PyObject *py_cancel_vibration(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    call_void_method("cancelVibration", "()V");
    Py_RETURN_NONE;
}

static PyObject *py_notify(PyObject *self, PyObject *args) {
    const char *title;
    const char *body;
    const char *channel_id = "";
    const char *channel_name = "";
    const char *small_icon = "";
    int identifier = 1;
    int ongoing = 0;
    (void)self;
    if (!PyArg_ParseTuple(args, "ssi|psss", &title, &body, &identifier, &ongoing,
                          &channel_id, &channel_name, &small_icon)) {
        return NULL;
    }
    int attached = 0;
    JNIEnv *env = jni_env(&attached);
    if (env && g_native_class) {
        jstring jtitle = utf8_to_jstring(env, title, strlen(title));
        jstring jbody = utf8_to_jstring(env, body, strlen(body));
        jstring jchannel = utf8_to_jstring(env, channel_id, strlen(channel_id));
        jstring jchannelname = utf8_to_jstring(env, channel_name, strlen(channel_name));
        jstring jicon = utf8_to_jstring(env, small_icon, strlen(small_icon));
        jmethodID method = static_method(env, "notify",
            "(Ljava/lang/String;Ljava/lang/String;IZLjava/lang/String;Ljava/lang/String;Ljava/lang/String;)V");
        if (method) {
            (*env)->CallStaticVoidMethod(env, g_native_class, method, jtitle, jbody,
                                         (jint)identifier, (jboolean)ongoing,
                                         jchannel, jchannelname, jicon);
            if ((*env)->ExceptionCheck(env)) {
                (*env)->ExceptionClear(env);
            }
        }
        (*env)->DeleteLocalRef(env, jtitle);
        (*env)->DeleteLocalRef(env, jbody);
        (*env)->DeleteLocalRef(env, jchannel);
        (*env)->DeleteLocalRef(env, jchannelname);
        (*env)->DeleteLocalRef(env, jicon);
        if (attached) {
            (*g_vm)->DetachCurrentThread(g_vm);
        }
    }
    Py_RETURN_NONE;
}

static PyObject *py_ensure_channel(PyObject *self, PyObject *args) {
    const char *channel_id;
    const char *channel_name;
    int importance = 3;
    (void)self;
    if (!PyArg_ParseTuple(args, "ss|i", &channel_id, &channel_name, &importance)) {
        return NULL;
    }
    int attached = 0;
    JNIEnv *env = jni_env(&attached);
    if (env && g_native_class) {
        jstring jid = utf8_to_jstring(env, channel_id, strlen(channel_id));
        jstring jname = utf8_to_jstring(env, channel_name, strlen(channel_name));
        jmethodID method = static_method(env, "ensureChannel", "(Ljava/lang/String;Ljava/lang/String;I)V");
        if (method) {
            (*env)->CallStaticVoidMethod(env, g_native_class, method, jid, jname,
                                         (jint)importance);
            if ((*env)->ExceptionCheck(env)) {
                (*env)->ExceptionClear(env);
            }
        }
        (*env)->DeleteLocalRef(env, jid);
        (*env)->DeleteLocalRef(env, jname);
        if (attached) {
            (*g_vm)->DetachCurrentThread(g_vm);
        }
    }
    Py_RETURN_NONE;
}

static PyObject *py_cancel_notification(PyObject *self, PyObject *args) {
    int identifier;
    (void)self;
    if (!PyArg_ParseTuple(args, "i", &identifier)) {
        return NULL;
    }
    call_void_method("cancelNotification", "(I)V", (jint)identifier);
    Py_RETURN_NONE;
}

static PyObject *py_has_permission(PyObject *self, PyObject *args) {
    const char *permission;
    (void)self;
    if (!PyArg_ParseTuple(args, "s", &permission)) {
        return NULL;
    }
    int granted = 0;
    int attached = 0;
    JNIEnv *env = jni_env(&attached);
    if (env && g_native_class) {
        jstring jperm = utf8_to_jstring(env, permission, strlen(permission));
        jmethodID method = static_method(env, "hasPermission",
                                                     "(Ljava/lang/String;)Z");
        if (method) {
            granted = (*env)->CallStaticBooleanMethod(env, g_native_class, method, jperm);
            if ((*env)->ExceptionCheck(env)) {
                (*env)->ExceptionClear(env);
                granted = 0;
            }
        }
        (*env)->DeleteLocalRef(env, jperm);
        if (attached) {
            (*g_vm)->DetachCurrentThread(g_vm);
        }
    }
    return PyBool_FromLong(granted);
}

static PyObject *py_device_language(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    PyObject *result = NULL;
    int attached = 0;
    JNIEnv *env = jni_env(&attached);
    if (env && g_native_class) {
        jmethodID method = static_method(env, "deviceLanguage",
                                                     "()Ljava/lang/String;");
        if (method) {
            jstring value = (jstring)(*env)->CallStaticObjectMethod(env, g_native_class, method);
            if ((*env)->ExceptionCheck(env)) {
                (*env)->ExceptionClear(env);
            } else if (value) {
                Utf8Text text = jstring_to_utf8(env, value);
                if (text.data) {
                    result = PyUnicode_FromStringAndSize(text.data, (Py_ssize_t)text.len);
                }
                text_free(&text);
                (*env)->DeleteLocalRef(env, value);
            }
        }
        if (attached) {
            (*g_vm)->DetachCurrentThread(g_vm);
        }
    }
    if (!result) {
        result = PyUnicode_FromString("");
    }
    return result;
}

static PyObject *py_request_permission(PyObject *self, PyObject *args) {
    const char *permission;
    (void)self;
    if (!PyArg_ParseTuple(args, "s", &permission)) {
        return NULL;
    }
    int granted = 0;
    int attached = 0;
    JNIEnv *env = jni_env(&attached);
    if (env && g_native_class) {
        jstring jperm = utf8_to_jstring(env, permission, strlen(permission));
        jmethodID method = static_method(env, "requestPermission",
                                                     "(Ljava/lang/String;)Z");
        if (method) {
            /* This blocks until the user answers, so release the GIL to keep
               the rest of the interpreter responsive. */
            Py_BEGIN_ALLOW_THREADS
            granted = (*env)->CallStaticBooleanMethod(env, g_native_class, method, jperm);
            Py_END_ALLOW_THREADS
            if ((*env)->ExceptionCheck(env)) {
                (*env)->ExceptionClear(env);
                granted = 0;
            }
        }
        (*env)->DeleteLocalRef(env, jperm);
        if (attached) {
            (*g_vm)->DetachCurrentThread(g_vm);
        }
    }
    return PyBool_FromLong(granted);
}

static PyObject *py_open_url(PyObject *self, PyObject *args) {
    const char *url;
    int opened = 0;
    (void)self;
    if (!PyArg_ParseTuple(args, "s", &url)) {
        return NULL;
    }
    int attached = 0;
    JNIEnv *env = jni_env(&attached);
    if (env && g_native_class) {
        jstring jurl = utf8_to_jstring(env, url, strlen(url));
        jmethodID method = static_method(env, "openUrl", "(Ljava/lang/String;)Z");
        if (method) {
            opened = (*env)->CallStaticBooleanMethod(env, g_native_class, method, jurl);
            if ((*env)->ExceptionCheck(env)) {
                (*env)->ExceptionClear(env);
            }
        }
        (*env)->DeleteLocalRef(env, jurl);
        if (attached) {
            (*g_vm)->DetachCurrentThread(g_vm);
        }
    }
    return PyBool_FromLong(opened);
}

/* Block until a UI event arrives. Returns (widget_id, type, value) or None. */
static PyObject *py_next_event(PyObject *self, PyObject *args) {
    int timeout_ms = -1;
    (void)self;
    if (!PyArg_ParseTuple(args, "|i", &timeout_ms)) {
        return NULL;
    }

    Event *event = NULL;
    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&q_mutex);
    /* A condition variable may wake spuriously, so its predicate must be
     * re-checked in a loop: the old ``if`` could return None from an empty
     * queue, and Python reads None as "the platform asked the loop to end". */
    while (!q_head && !q_stopped) {
        if (timeout_ms < 0) {
            pthread_cond_wait(&q_cond, &q_mutex);
        } else {
            struct timeval now;
            struct timespec deadline;
            gettimeofday(&now, NULL);
            deadline.tv_sec = now.tv_sec + timeout_ms / 1000;
            deadline.tv_nsec = (now.tv_usec + (timeout_ms % 1000) * 1000) * 1000;
            if (deadline.tv_nsec >= 1000000000L) {
                deadline.tv_sec += 1;
                deadline.tv_nsec -= 1000000000L;
            }
            if (pthread_cond_timedwait(&q_cond, &q_mutex, &deadline) == ETIMEDOUT) {
                break;  /* deadline reached: give the interpreter its timers back */
            }
        }
    }
    if (q_head) {
        event = q_head;
        q_head = event->next;
        if (!q_head) {
            q_tail = NULL;
        }
        q_count--;
    }
    pthread_mutex_unlock(&q_mutex);
    Py_END_ALLOW_THREADS

    if (!event) {
        Py_RETURN_NONE;
    }
    /* ``s#`` carries the byte length: the payload may contain U+0000. */
    PyObject *result = Py_BuildValue("(s#s#s#)",
                                     event->widget_id.data, (Py_ssize_t)event->widget_id.len,
                                     event->type.data, (Py_ssize_t)event->type.len,
                                     event->value.data, (Py_ssize_t)event->value.len);
    event_free(event);
    return result;

}

/* Wake a next_event() that is blocked waiting, from any Python thread: the
 * loop then runs the callbacks queued with App.dispatch() and timers. */
static PyObject *py_wake(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    queue_push("", 0, "__wake__", 7, "", 0);
    Py_RETURN_NONE;
}

static PyObject *py_finish_app(PyObject *self, PyObject *args) {
    (void)self;
    (void)args;
    call_void_method("finishApp", "()V");
    Py_RETURN_NONE;
}

static PyMethodDef module_methods[] = {
    {"render", py_render, METH_VARARGS, "Send a serialised widget tree to the UI thread."},
    {"toast", py_toast, METH_VARARGS, "Show a toast."},
    {"vibrate", py_vibrate, METH_VARARGS, "Vibrate once."},
    {"vibrate_pattern", py_vibrate_pattern, METH_VARARGS, "Play a vibration pattern."},
    {"cancel_vibration", py_cancel_vibration, METH_NOARGS, "Stop vibrating."},
    {"notify", py_notify, METH_VARARGS, "Post a notification."},
    {"ensure_channel", py_ensure_channel, METH_VARARGS, "Create a notification channel."},
    {"cancel_notification", py_cancel_notification, METH_VARARGS, "Cancel a notification."},
    {"has_permission", py_has_permission, METH_VARARGS, "Check a permission."},
    {"request_permission", py_request_permission, METH_VARARGS, "Request a permission."},
    {"device_language", py_device_language, METH_NOARGS, "The device's language tag."},
    {"open_url", py_open_url, METH_VARARGS, "Open a URL in the browser."},
    {"next_event", py_next_event, METH_VARARGS, "Block until the next UI event."},
    {"wake", py_wake, METH_NOARGS, "Wake the thread blocked in next_event()."},
    {"finish_app", py_finish_app, METH_NOARGS, "Ask the Activity to finish."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef android_module = {
    PyModuleDef_HEAD_INIT, "_pymobile_android", "PyMobile Android platform hooks.", -1,
    module_methods, NULL, NULL, NULL, NULL,
};

static PyObject *init_android_module(void) {
    return PyModule_Create(&android_module);
}

/* ------------------------------------------------------------------ */
/* JNI entry points                                                    */
/* ------------------------------------------------------------------ */

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM *vm, void *reserved) {
    (void)reserved;
    g_vm = vm;
    return JNI_VERSION_1_6;
}

/* Java → Python: queue a UI event. */
JNIEXPORT void JNICALL Java_org_pymobile_app_Native_dispatchEvent(
        JNIEnv *env, jclass clazz, jstring widgetIdJ, jstring typeJ, jstring valueJ) {
    (void)clazz;
    /* ``valueJ`` may legitimately be null (e.g. a bare "press" event carries
     * no payload); jstring_to_utf8 maps both null and "" to empty text. */
    Utf8Text widget_id = jstring_to_utf8(env, widgetIdJ);
    Utf8Text type = jstring_to_utf8(env, typeJ);
    Utf8Text value = jstring_to_utf8(env, valueJ);

    queue_push(widget_id.data ? widget_id.data : "", widget_id.len,
               type.data ? type.data : "", type.len,
               value.data ? value.data : "", value.len);

    text_free(&widget_id);
    text_free(&type);
    text_free(&value);
}

/* Wake the Python thread so it can shut down. */
JNIEXPORT void JNICALL Java_org_pymobile_app_Native_stopEventLoop(JNIEnv *env, jclass clazz) {
    (void)env;
    (void)clazz;
    pthread_mutex_lock(&q_mutex);
    q_stopped = 1;
    pthread_cond_broadcast(&q_cond);
    pthread_mutex_unlock(&q_mutex);
}

JNIEXPORT jboolean JNICALL
Java_org_pymobile_app_PythonRuntime_pythonIsInitialized(JNIEnv *env, jclass clazz) {
    (void)env;
    (void)clazz;
    return Py_IsInitialized() ? JNI_TRUE : JNI_FALSE;
}

/* The packaged entry point, resolved to an existing file.
 *
 * ``pymobile.toml`` may name any module, and ``optimize = true`` ships
 * bytecode only, so the build records the packaged name (and form) in
 * ``assets/pymobile.properties``; Java passes it here. Older APKs name
 * ``main.py``. Whatever the name, the file that actually exists wins: the
 * runner used to receive a hard-coded ``main.py`` and failed with
 * FileNotFoundError on a perfectly good APK.
 */
static int resolve_entry(const char *app_dir, const char *entry, char *out, size_t out_size) {
    if (!entry || !*entry) {
        entry = "main.py";
    }
    if (entry[0] == '/') {
        snprintf(out, out_size, "%s", entry);
    } else {
        snprintf(out, out_size, "%s/%s", app_dir, entry);
    }
    if (access(out, F_OK) == 0) {
        return 0;
    }
    /* optimize = true packages ``x.pyc`` without ``x.py`` (and vice versa). */
    if (strlen(entry) > 3 && strcmp(entry + strlen(entry) - 3, ".py") == 0) {
        snprintf(out, out_size, "%s/%sc", app_dir, entry);
    } else if (strlen(entry) > 4 && strcmp(entry + strlen(entry) - 4, ".pyc") == 0) {
        snprintf(out, out_size, "%s/%.*s", app_dir, (int)(strlen(entry) - 1), entry);
    } else {
        return -1;
    }
    return access(out, F_OK) == 0 ? 1 : -1;
}

/* ``globals[name] = value`` without leaking the value's reference. */
static int set_global_string(PyObject *globals, const char *name, const char *value) {
    PyObject *text = PyUnicode_FromString(value);
    if (!text) {
        return 0;
    }
    int ok = PyDict_SetItemString(globals, name, text) == 0;
    Py_DECREF(text);
    return ok;
}

/* Run the packaged entry point and report what happened.
 *
 * The old runner printed a traceback and still returned 0, and swallowed
 * SystemExit entirely: MainActivity only shows its error screen on a non-zero
 * status, so a failed startup looked like a clean exit and the user stared at
 * the "Starting Python…" placeholder. Now SystemExit keeps its exit code
 * (``None`` counts as 0) and any other exception returns 1 after printing.
 */
static int run_entry_point(const char *entry_path) {
    PyObject *main_module = PyImport_AddModule("__main__");
    if (!main_module) {
        PyErr_Print();
        return 1;
    }
    PyObject *globals = PyModule_GetDict(main_module);
    PyObject *code = Py_CompileString(
        "import runpy\n"
        "runpy.run_path(_PYMOBILE_ENTRY, run_name='__main__')\n",
        "<pymobile-startup>", Py_file_input);
    if (!code) {
        PyErr_Print();
        return 1;
    }
    if (!set_global_string(globals, "_PYMOBILE_ENTRY", entry_path)) {
        Py_DECREF(code);
        PyErr_Print();
        return 1;
    }
    PyObject *result = PyEval_EvalCode(code, globals, globals);
    Py_DECREF(code);
    if (result) {
        Py_DECREF(result);
        return 0;
    }
    if (PyErr_ExceptionMatches(PyExc_SystemExit)) {
        PyObject *type = NULL, *value = NULL, *traceback = NULL;
        long status = 0;
        PyErr_Fetch(&type, &value, &traceback);
        PyErr_NormalizeException(&type, &value, &traceback);
        if (value && value != Py_None) {
            PyObject *exit_code = PyObject_GetAttrString(value, "code");
            if (exit_code) {
                if (exit_code != Py_None) {
                    status = PyLong_AsLong(exit_code);
                    if (PyErr_Occurred()) {
                        /* e.g. SystemExit("message") — print and use 1. */
                        PyErr_Clear();
                        status = 1;
                    }
                }
                Py_DECREF(exit_code);
            } else {
                PyErr_Clear();
                status = 1;
            }
        }
        if (value && status != 0) {
            PyErr_Display(type, value, traceback);
        }
        Py_XDECREF(type);
        Py_XDECREF(value);
        Py_XDECREF(traceback);
        return (int)status;
    }
    PyErr_Print();
    return 1;
}

JNIEXPORT jint JNICALL
Java_org_pymobile_app_PythonRuntime_startPython(
        JNIEnv *env, jobject obj, jstring homeJ, jstring appDirJ, jstring entryJ) {
    (void)obj;

    /* Paths may contain anything the user typed; they travel as UTF-8 with a
     * length and are never pasted into a Python string literal (the old
     * snprintf('%s') broke on a quote in the path). */
    Utf8Text home_text = jstring_to_utf8(env, homeJ);
    Utf8Text app_text = jstring_to_utf8(env, appDirJ);
    Utf8Text entry_text = jstring_to_utf8(env, entryJ);
    const char *home = home_text.data ? home_text.data : "";
    const char *app_dir = app_text.data ? app_text.data : "";
    const char *entry = entry_text.data ? entry_text.data : "";

    redirect_stdio_to_logcat();
    LOGI("starting python: home=%s app=%s entry=%s", home, app_dir, entry);

    /* Cache the Native class so background threads can find it: JNI class
     * lookup from a non-Java thread only sees the system class loader. */
    jclass local = (*env)->FindClass(env, "org/pymobile/app/Native");
    if (local) {
        g_native_class = (jclass)(*env)->NewGlobalRef(env, local);
        (*env)->DeleteLocalRef(env, local);
    } else {
        LOGE("org.pymobile.app.Native not found");
        (*env)->ExceptionClear(env);
    }

    if (PyImport_AppendInittab("_pymobile_android", init_android_module) != 0) {
        LOGE("could not register _pymobile_android");
    }

    PyStatus status;
    PyPreConfig preconfig;
    PyPreConfig_InitIsolatedConfig(&preconfig);
    preconfig.utf8_mode = 1;
    status = Py_PreInitialize(&preconfig);
    if (PyStatus_Exception(status)) {
        LOGE("Py_PreInitialize failed");
        return 1;
    }

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.write_bytecode = 0;
    config.install_signal_handlers = 0;

    wchar_t *whome = Py_DecodeLocale(home, NULL);
    PyConfig_SetString(&config, &config.home, whome);
    wchar_t *argv[] = {L"pymobile", NULL};
    PyConfig_SetArgv(&config, 1, argv);

    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    PyMem_RawFree(whome);
    if (PyStatus_Exception(status)) {
        LOGE("Py_InitializeFromConfig failed: %s", status.err_msg ? status.err_msg : "?");
        return 1;
    }

    /* A fresh interpreter means a fresh event loop: clear the stop flag a
       previous Activity's onDestroy() may have left set, otherwise
       next_event() stops blocking and busy-spins forever. */
    pthread_mutex_lock(&q_mutex);
    q_stopped = 0;
    pthread_mutex_unlock(&q_mutex);

    /* Bootstrap with the values injected as objects, never as source text. */
    int rc = 1;
    PyObject *main_module = PyImport_AddModule("__main__");
    PyObject *globals = main_module ? PyModule_GetDict(main_module) : NULL;
    if (!set_global_string(globals, "_PYMOBILE_APP", app_dir)
            || !set_global_string(globals, "_PYMOBILE_HOME", home)) {
        LOGE("failed to prepare the bootstrap namespace");
        PyErr_Print();
        goto cleanup;
    }
    PyObject *bootstrap = Py_CompileString(
        "import os, sys\n"
        "sys.path.insert(0, _PYMOBILE_APP)\n"
        "os.chdir(_PYMOBILE_APP)\n"
        "os.environ['ANDROID_APP_PATH'] = _PYMOBILE_APP\n"
        "_ca = os.path.join(_PYMOBILE_HOME, 'etc', 'ssl', 'cert.pem')\n"
        "if os.path.exists(_ca):\n"
        "    os.environ['SSL_CERT_FILE'] = _ca\n"
        "    os.environ['REQUESTS_CA_BUNDLE'] = _ca\n",
        "<pymobile-config>", Py_file_input);
    if (!bootstrap) {
        PyErr_Print();
        goto cleanup;
    }
    PyObject *prepared = PyEval_EvalCode(bootstrap, globals, globals);
    Py_DECREF(bootstrap);
    if (!prepared) {
        PyErr_Print();
        goto cleanup;
    }
    Py_DECREF(prepared);

    char entry_path[4096];
    int resolved = resolve_entry(app_dir, entry, entry_path, sizeof(entry_path));
    if (resolved < 0) {
        LOGE("entry point not found: %s/%s", app_dir, entry);
        rc = 1;
        goto cleanup;
    }
    if (resolved > 0) {
        LOGI("entry point %s does not exist; running %s instead", entry, entry_path);
    }

    rc = run_entry_point(entry_path);
    LOGI("python finished with rc=%d", rc);

cleanup:
    text_free(&home_text);
    text_free(&app_text);
    text_free(&entry_text);

    Py_Finalize();
    return rc;
}

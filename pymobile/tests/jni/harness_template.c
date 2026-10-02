/* Host-side harness for the UTF-8 ↔ UTF-16 helpers in ``pymobile_jni.c``.
 *
 * The conversion section is extracted verbatim from the real bridge source by
 * ``test_jni_utf8.py`` and compiled here against a miniature stand-in for the
 * JNI function table, so the round-trip can be checked on a laptop without an
 * Android device or the NDK. Everything else about the bridge needs a device;
 * this part does not, and it is exactly the part that used to corrupt emoji.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned short jchar;
typedef int jsize;
typedef unsigned char jboolean;

struct JStringStub;
typedef struct JStringStub *jstring;

struct JStringStub {
    jchar *units;
    jsize len;
};

/* Mirrors the real jni.h shape: ``JNIEnv`` is a pointer to a table of
 * function pointers, so the extracted code's ``(*env)->Method(env, …)``
 * call convention compiles unchanged. */
struct FakeEnv;
typedef struct FakeEnv *JNIEnv;
typedef struct FakeEnv {
    jsize (*GetStringLength)(struct FakeEnv **, jstring);
    const jchar *(*GetStringChars)(struct FakeEnv **, jstring, jboolean *);
    void (*ReleaseStringChars)(struct FakeEnv **, jstring, const jchar *);
    jstring (*NewString)(struct FakeEnv **, const jchar *, jsize);
} FakeEnv;

static jsize stub_length(struct FakeEnv **env, jstring value) {
    (void)env;
    return value->len;
}

static const jchar *stub_chars(struct FakeEnv **env, jstring value, jboolean *copied) {
    (void)env;
    if (copied) {
        *copied = 0;
    }
    return value->units;
}

static void stub_release(struct FakeEnv **env, jstring value, const jchar *chars) {
    (void)env;
    (void)value;
    (void)chars;
}

static jstring stub_new(struct FakeEnv **env, const jchar *units, jsize len) {
    (void)env;
    struct JStringStub *made = (struct JStringStub *)calloc(1, sizeof(struct JStringStub));
    if (!made) {
        return NULL;
    }
    made->units = (jchar *)malloc(sizeof(jchar) * (size_t)(len ? len : 1));
    if (!made->units) {
        free(made);
        return NULL;
    }
    if (len) {
        memcpy(made->units, units, sizeof(jchar) * (size_t)len);
    }
    made->len = len;
    return made;
}

/* =================== extracted from pymobile_jni.c =================== */


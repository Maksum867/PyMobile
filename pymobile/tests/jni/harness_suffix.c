
/* ============================== driver =============================== */

static int hex_value(int c) {
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

static size_t parse_hex(const char *line, unsigned char *out) {
    size_t used = 0;
    for (size_t i = 0; line[i] && line[i] != '\n'; i += 2) {
        int high = hex_value(line[i]);
        int low = line[i + 1] ? hex_value(line[i + 1]) : 0;
        if (high < 0 || low < 0) {
            break;
        }
        out[used++] = (unsigned char)((high << 4) | low);
    }
    return used;
}

int main(void) {
    static struct FakeEnv table = {stub_length, stub_chars, stub_release, stub_new};
    JNIEnv env = &table;
    char line[65536];
    unsigned char bytes[32768];
    while (fgets(line, sizeof(line), stdin)) {
        if (line[0] == 'q') {
            break;
        }
        size_t used = parse_hex(line + 1, bytes);
        if (line[0] == 'j') {
            /* Java → Python: hex UTF-16 code units, print hex UTF-8 bytes. */
            struct JStringStub text = {(jchar *)bytes, (jsize)(used / 2)};
            Utf8Text converted = jstring_to_utf8(&env, &text);
            for (size_t i = 0; i < converted.len; i++) {
                printf("%02x", (unsigned char)converted.data[i]);
            }
            text_free(&converted);
            printf("\n");
        } else if (line[0] == 'u') {
            /* Python → Java: hex UTF-8 bytes, print hex UTF-16 code units. */
            jstring converted = utf8_to_jstring(&env, (const char *)bytes, used);
            if (!converted) {
                printf("ERROR\n");
                continue;
            }
            for (jsize i = 0; i < converted->len; i++) {
                /* Little-endian byte order, matching UTF-16LE hex vectors
                 * with the input side of the "j" requests. */
                int unit = converted->units[i];
                printf("%02x%02x", unit & 0xFF, (unit >> 8) & 0xFF);
            }
            free(converted->units);
            free(converted);
            printf("\n");
        } else {
            printf("ERROR\n");
        }
        fflush(stdout);
    }
    return 0;
}

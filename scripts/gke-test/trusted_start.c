/* Prototype only: a trusted, statically linked entrypoint for the suite.
 * The controller must create a fresh empty gate, mount this binary and the
 * gate read-only, validate their identities, monitor the network continuously,
 * and write the marker in place only after every start barrier passes.
 * This program does not authenticate the test image or produce evidence. */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef AW_START_GATE
#define AW_START_GATE "/run/aw-start/gate"
#endif
#ifndef AW_TEST_BINARY
#define AW_TEST_BINARY "/usr/local/bin/lumen-it"
#endif

int main(int argc, char **argv) {
    const char marker[] = "aw-start-v1\n";
    struct timespec begin, now, pause = {0, 100000000};
    struct stat info;
    char value[sizeof(marker)];
    int fd;
    if (argc != 4 || strcmp(argv[1], "--ignored") ||
        strcmp(argv[2], "--test-threads=1") || argv[3][0] == '\0' || argv[3][0] == '-') return 2;
    size_t suite_length = strlen(argv[3]);
    if (suite_length > 256) return 2;
    for (size_t i = 0; i < suite_length; ++i) {
        unsigned char c = (unsigned char)argv[3][i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_' || c == ':' || c == '.' || c == '-')) return 2;
    }
    fd = open(AW_START_GATE, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0 || fstat(fd, &info) || !S_ISREG(info.st_mode) ||
        clock_gettime(CLOCK_MONOTONIC, &begin)) return 3;
    for (;;) {
        ssize_t n = pread(fd, value, sizeof(value), 0);
        if (n < 0) { if (errno == EINTR) continue; return 4; }
        if (n == (ssize_t)(sizeof(marker) - 1) && !memcmp(value, marker, sizeof(marker) - 1)) break;
        if (n != 0) return 5;
        if (clock_gettime(CLOCK_MONOTONIC, &now) || now.tv_sec - begin.tv_sec >= 120) return 6;
        if (nanosleep(&pause, NULL) && errno != EINTR) return 7;
    }
    close(fd);
    argv[0] = (char *)AW_TEST_BINARY;
    execv(AW_TEST_BINARY, argv);
    return 8;
}

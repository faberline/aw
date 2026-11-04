/* Synthetic Docker executor fixture. This is never product test evidence. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    const char *suite = "e2e::operator::";
    const char *name = "e2e::operator::journey";
    if ((argc == 3 && strcmp(argv[1], suite) == 0 && strcmp(argv[2], "--list") == 0) ||
        (argc == 4 && strcmp(argv[1], suite) == 0 && strcmp(argv[2], "--ignored") == 0 &&
         strcmp(argv[3], "--list") == 0)) {
        printf("%s: test\n\n1 test, 0 benchmarks\n", name);
        return 0;
    }
    if (argc == 4 && strcmp(argv[1], "--ignored") == 0 &&
        strcmp(argv[2], "--test-threads=1") == 0 && strcmp(argv[3], suite) == 0) {
        if (getenv("AW_FIXTURE_SENTINEL")) {
            char gate[32] = {0};
            FILE *input = fopen("/run/aw-start/gate", "r");
            if (!input) return 3;
            size_t n = fread(gate, 1, sizeof(gate), input);
            fclose(input);
            if (n != strlen("aw-start-v1\n") || memcmp(gate, "aw-start-v1\n", n)) return 4;
            printf("AW_SYNTHETIC_SUITE_ENTERED\n");
            fflush(stdout);
        }
        printf("running 1 test\ntest %s ... ok\n\n", name);
        printf("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n");
        return 0;
    }
    return 2;
}

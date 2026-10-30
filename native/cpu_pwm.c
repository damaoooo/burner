/*
 * cpu_pwm: CPU load generator with a shared, wall-clock aligned duty cycle.
 *
 * One worker thread is pinned to each CPU. Time is cut into fixed intervals
 * aligned to CLOCK_REALTIME (so NTP-synced machines share the same grid).
 * At the start of every interval each worker is busy for util * interval and
 * then sleeps until the next boundary. Because every CPU is busy and idle at
 * the same time, the package can drop into deep idle during the idle part,
 * which keeps power at a given utilization low-floored and repeatable.
 *
 * Utilization (0-100, percent) is read from --util-file once per interval by
 * a reader thread just before the boundary. A failed or partial read keeps
 * the previous value.
 *
 * Usage: cpu_pwm --util-file PATH [-n NCPUS] [--interval-ms MS] [--kernel int|fma]
 */
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define NSEC 1000000000LL
#define READ_AHEAD_NS 1000000LL /* read the file 1 ms before each boundary */

static const char *util_file;
static long long interval_ns = 10 * 1000000LL;
static int use_fma = 1;
static _Atomic long long util_ppm = 0; /* parts per million of the interval */
static volatile sig_atomic_t stop_requested = 0;
static volatile double sink;

static long long now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (long long)ts.tv_sec * NSEC + ts.tv_nsec;
}

static void sleep_until(long long deadline)
{
    struct timespec ts = {deadline / NSEC, deadline % NSEC};
    while (clock_nanosleep(CLOCK_REALTIME, TIMER_ABSTIME, &ts, NULL) == EINTR) {
        if (stop_requested)
            return;
    }
}

static long long next_boundary(long long t)
{
    return (t / interval_ns + 1) * interval_ns;
}

static int read_util_ppm(long long *out)
{
    FILE *file = fopen(util_file, "r");
    double value;
    int ok;

    if (file == NULL)
        return 0;
    ok = fscanf(file, "%lf", &value) == 1;
    fclose(file);
    if (!ok)
        return 0;
    if (value < 0)
        value = 0;
    if (value > 100)
        value = 100;
    *out = (long long)(value * 10000.0 + 0.5);
    return 1;
}

typedef float v16f __attribute__((vector_size(64)));

/* Independent multiply-add chains keep the FMA units busy; the values
 * converge (a < 1) so they never overflow. */
static void burn_fma(long long until)
{
    v16f a = {0}, b = {0}, acc[8];
    int i, k;

    for (i = 0; i < 16; i++) {
        a[i] = 0.9999f;
        b[i] = 0.0001f * (float)(i + 1);
    }
    for (k = 0; k < 8; k++)
        acc[k] = b * (float)(k + 1);
    while (now_ns() < until) {
        for (i = 0; i < 256; i++)
            for (k = 0; k < 8; k++)
                acc[k] = acc[k] * a + b;
    }
    for (k = 1; k < 8; k++)
        acc[0] += acc[k];
    sink = acc[0][0];
}

static void burn_int(long long until)
{
    unsigned long long x = (unsigned long long)until;
    int i;

    while (now_ns() < until) {
        for (i = 0; i < 1024; i++)
            x = x * 6364136223846793005ULL + 1442695040888963407ULL;
    }
    sink = (double)(x & 0xff);
}

static void *reader_main(void *arg)
{
    long long next = next_boundary(now_ns());
    long long value;

    (void)arg;
    while (!stop_requested) {
        sleep_until(next - READ_AHEAD_NS);
        if (read_util_ppm(&value))
            atomic_store(&util_ppm, value);
        next = next_boundary(now_ns() + READ_AHEAD_NS);
    }
    return NULL;
}

static void *worker_main(void *arg)
{
    int cpu = (int)(long)arg;
    cpu_set_t set;
    long long next;

    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    pthread_setaffinity_np(pthread_self(), sizeof(set), &set);

    next = next_boundary(now_ns());
    while (!stop_requested) {
        long long busy_ns;

        sleep_until(next);
        busy_ns = interval_ns * atomic_load(&util_ppm) / 1000000LL;
        if (busy_ns > 0) {
            if (use_fma)
                burn_fma(next + busy_ns);
            else
                burn_int(next + busy_ns);
        }
        next += interval_ns;
        /* At 100% the burn ends right at the next boundary; only a whole
         * interval of lag (descheduled, overloaded) means rejoining the grid. */
        if (now_ns() - next >= interval_ns)
            next = next_boundary(now_ns());
    }
    return NULL;
}

static void on_signal(int sig)
{
    (void)sig;
    stop_requested = 1;
}

static void usage(const char *prog)
{
    fprintf(stderr, "usage: %s --util-file PATH [-n NCPUS] [--interval-ms MS] [--kernel int|fma]\n", prog);
}

int main(int argc, char **argv)
{
    long ncpus = sysconf(_SC_NPROCESSORS_ONLN);
    pthread_t *threads;
    pthread_t reader;
    struct sigaction sa;
    long long initial;
    int i;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--util-file") && i + 1 < argc) {
            util_file = argv[++i];
        } else if (!strcmp(argv[i], "-n") && i + 1 < argc) {
            ncpus = strtol(argv[++i], NULL, 10);
        } else if (!strcmp(argv[i], "--interval-ms") && i + 1 < argc) {
            interval_ns = strtoll(argv[++i], NULL, 10) * 1000000LL;
        } else if (!strcmp(argv[i], "--kernel") && i + 1 < argc) {
            const char *kernel = argv[++i];
            if (!strcmp(kernel, "fma"))
                use_fma = 1;
            else if (!strcmp(kernel, "int"))
                use_fma = 0;
            else {
                fprintf(stderr, "cpu_pwm: unknown kernel '%s'\n", kernel);
                return 2;
            }
        } else {
            usage(argv[0]);
            return 2;
        }
    }
    if (util_file == NULL) {
        usage(argv[0]);
        return 2;
    }
    if (ncpus < 1 || ncpus > 4096) {
        fprintf(stderr, "cpu_pwm: -n must be between 1 and 4096\n");
        return 2;
    }
    if (interval_ns < 1000000LL || interval_ns > 1000LL * 1000000LL) {
        fprintf(stderr, "cpu_pwm: --interval-ms must be between 1 and 1000\n");
        return 2;
    }

    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);

    initial = 0;
    read_util_ppm(&initial);
    atomic_store(&util_ppm, initial);

    threads = calloc((size_t)ncpus, sizeof(*threads));
    if (threads == NULL)
        return 1;
    if (pthread_create(&reader, NULL, reader_main, NULL) != 0)
        return 1;
    for (i = 0; i < ncpus; i++) {
        if (pthread_create(&threads[i], NULL, worker_main, (void *)(long)i) != 0) {
            fprintf(stderr, "cpu_pwm: failed to start worker %d\n", i);
            stop_requested = 1;
            ncpus = i;
            break;
        }
    }
    for (i = 0; i < ncpus; i++)
        pthread_join(threads[i], NULL);
    stop_requested = 1;
    pthread_join(reader, NULL);
    free(threads);
    return 0;
}

// Linux build (run in scripts/):
//   gcc -O2 -fPIC -shared -o libcpusample.so body-runner-cpu-sample.c -lpthread
// Pass an executable wrapper as --runner /absolute/path/to/sampled-runner (or set
// DUELS_RUNNER=/absolute/path/to/sampled-runner). For example, sampled-runner:
//   #!/bin/sh
//   export LD_PRELOAD=/absolute/path/to/libcpusample.so
//   export CPUSAMPLE_OUT=/absolute/path/to/samples/cpu
//   # Required for an AVX runner built with OPENGOTHIC_JOLT_X86=x86-64-v3:
//   export CPUSAMPLE_FLAT=1
//   exec /absolute/path/to/headless-runner "$@"
// Create the output directory first. Omit CPUSAMPLE_FLAT for full stacks on a
// compatible runner; backtrace() in the signal handler crashes the AVX build.
// CPUSAMPLE_PERIOD_US optionally changes the default 1000 us sampling period.
//
// CPU-time sampling profiler for the headless runner where perf is locked: LD_PRELOAD it.
// ITIMER_PROF fires only while the process burns CPU, so sleeps between ticks are not sampled.
// Each SIGPROF writes one record (magic, frame count, CLOCK_MONOTONIC ns, return addresses) to
// $CPUSAMPLE_OUT.<pid>.bin; /proc/self/maps goes next to it for symbolization.
// CPUSAMPLE_FLAT=1 records only the interrupted pc from the signal context, without unwinding:
// glibc backtrace() from the handler crashes the runner built with AVX (x86-64-v3 Jolt).
#define _GNU_SOURCE
#include <errno.h>
#include <execinfo.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>

enum { MAX_FRAMES = 96 };
static int fd = -1;
static const char* base;
static long period_us = 1000;
static int flat = 0;

static void dump_maps(void) {
  char path[1024];
  snprintf(path, sizeof path, "%s.%d.maps", base, (int)getpid());
  int in = open("/proc/self/maps", O_RDONLY), out = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
  char buf[65536];
  ssize_t n;
  while (in >= 0 && out >= 0 && (n = read(in, buf, sizeof buf)) > 0)
    if (write(out, buf, (size_t)n) != n) break;
  if (in >= 0) close(in);
  if (out >= 0) close(out);
}

static void handler(int sig, siginfo_t* info, void* context) {
  (void)sig; (void)info;
  int saved = errno;
  struct { uint32_t magic, n; uint64_t ns; void* frames[MAX_FRAMES]; } rec;
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  rec.magic = 0x50524f46u;
  if (flat) {  // same layout as an unwound record: handler, trampoline slot, interrupted pc
    rec.frames[0] = (void*)handler;
    rec.frames[1] = 0;
    rec.frames[2] = (void*)((ucontext_t*)context)->uc_mcontext.gregs[REG_RIP];
    rec.n = 3;
  } else {
    rec.n = (uint32_t)backtrace(rec.frames, MAX_FRAMES);
  }
  rec.ns = (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
  if (fd >= 0 && write(fd, &rec, 16 + rec.n * sizeof(void*)) < 0) {}
  errno = saved;
}

static void arm(void) {
  char path[1024];
  snprintf(path, sizeof path, "%s.%d.bin", base, (int)getpid());
  if (fd >= 0) close(fd);
  fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_APPEND, 0644);
  dump_maps();
  struct itimerval timer = {{0, period_us}, {0, period_us}};
  setitimer(ITIMER_PROF, &timer, NULL);
}

static void child(void) { arm(); }

__attribute__((constructor)) static void start(void) {
  base = getenv("CPUSAMPLE_OUT");
  if (!base || !*base) return;
  const char* period = getenv("CPUSAMPLE_PERIOD_US");
  if (period && atol(period) > 0) period_us = atol(period);
  const char* flat_env = getenv("CPUSAMPLE_FLAT");
  flat = flat_env && flat_env[0] == '1';
  void* warm[4];
  backtrace(warm, 4);  // loads libgcc_s outside the signal handler
  struct sigaction action;
  memset(&action, 0, sizeof action);
  action.sa_sigaction = handler;
  action.sa_flags = SA_SIGINFO | SA_RESTART;
  sigemptyset(&action.sa_mask);
  sigaction(SIGPROF, &action, NULL);
  pthread_atfork(NULL, NULL, child);
  arm();
}

__attribute__((destructor)) static void stop(void) {
  if (fd < 0) return;
  struct itimerval off = {{0, 0}, {0, 0}};
  setitimer(ITIMER_PROF, &off, NULL);
  dump_maps();
  close(fd);
  fd = -1;
}

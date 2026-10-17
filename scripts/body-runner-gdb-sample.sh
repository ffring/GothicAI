#!/usr/bin/env bash
# Sampling profiler for the headless runner where perf is locked (perf_event_paranoid=4) and gdb
# may only trace its own children: pass this script as --runner. It runs the real runner under gdb,
# a background loop sends SIGUSR2 every SAMPLE_INTERVAL s, and gdb logs one backtrace per stop to
# SAMPLE_LOG. The runner keeps the caller's stdin/stdout, so the JSONL protocol is untouched.
real="${SAMPLE_RUNNER:-$HOME/gothic-ai/body-api/build/opengothic/gothic-body-runner}"
log="${SAMPLE_LOG:-$HOME/gothic-ai/body/rl/profile/gdb-samples.log}"
interval="${SAMPLE_INTERVAL:-0.01}"
exec 3<&0 4>&1
args=$(printf '%q ' "$@")
cat > "$log.gdb" <<GDB
file $real
set pagination off
set confirm off
set print thread-events off
set width 0
set print frame-arguments none
set print address off
set print frame-info short-location
set logging file $log
set logging overwrite on
set logging redirect on
set logging enabled on
handle SIGPIPE nostop noprint pass
handle SIGUSR2 nostop noprint nopass
catch signal SIGUSR2
commands
silent
echo ==SAMPLE==\n
bt 60
continue
end
run $args < /dev/fd/3 > /dev/fd/4
GDB
( sleep 4; while :; do p=$(pgrep -P $$ -x gothic-body-run | head -1); [ -z "$p" ] && { sleep 0.5; p=$(pgrep -P $$ -x gothic-body-run | head -1); [ -z "$p" ] && exit 0; }; kill -USR2 "$p" 2>/dev/null; sleep "$interval"; done ) &
exec gdb -q -batch -nx -x "$log.gdb" < /dev/null > "$log.out" 2>&1

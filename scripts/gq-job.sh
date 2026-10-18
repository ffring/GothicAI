#!/usr/bin/env bash
# Source this file on the execution host. Keep submit separate from wait: the
# caller may have EOF on stdin (an SSH heredoc, nohup, or </dev/null).

gq_available() {
  [[ -x "${GQ_BIN:-$HOME/gothic-ai/bin/gq}" ]]
}

# Bash dynamically scopes these locals from gq_job. Keep the cancellation in
# the launcher's shell: a signal directed only at its PID must reach this trap.
_gq_job_cancel() {
  [[ -n "$_gq_id" && "$_gq_cancelled" == 0 ]] || return 0
  _gq_cancel_pending=1
  [[ "$_gq_cancelling" == 0 ]] || return 0
  _gq_cancelling=1
  while [[ "$_gq_cancel_pending" == 1 && "$_gq_cancelled" == 0 ]]; do
    _gq_cancel_pending=0
    # Terminal/group signals belong to the launcher. Let an in-flight cancel
    # save its result, and serialize any further requests from nested traps.
    if GQ_OWNER="$_gq_owner" bash -c 'trap "" INT TERM HUP; exec "$@"' \
        gq-cancel "$_gq_bin" cancel "$_gq_id" >&2; then
      _gq_cancelled=1
    else
      printf 'gq: could not cancel %s\n' "$_gq_id" >&2
    fi
  done
  _gq_cancelling=0
}

gq_job() {
  local _gq_bin="${GQ_BIN:-$HOME/gothic-ai/bin/gq}"
  local _gq_root="${HOME%/}/gothic-ai" _gq_cwd _gq_owner
  local _gq_id='' _gq_wait_pid _gq_result _gq_saved_traps
  local _gq_interrupted=0 _gq_cancelled=0 _gq_cancelling=0 _gq_cancel_pending=0

  if ! gq_available; then
    printf 'gq: executable not found: %s\n' "$_gq_bin" >&2
    return 127
  fi
  _gq_owner="${GQ_OWNER:-}"
  if [[ -z "$_gq_owner" ]]; then
    _gq_cwd=$(pwd -P)
    case "$_gq_cwd" in
      "$_gq_root"/*)
        _gq_owner="${_gq_cwd#"$_gq_root"/}"
        _gq_owner="${_gq_owner%%/*}"
        case "$_gq_owner" in bin|queue) _gq_owner='' ;; esac
        ;;
    esac
  fi
  if [[ -z "$_gq_owner" ]]; then
    printf 'gq: set GQ_OWNER or submit from a task folder under %s\n' "$_gq_root" >&2
    return 2
  fi
  _gq_saved_traps=$(trap -p INT TERM HUP)
  trap '_gq_interrupted=1; _gq_job_cancel' INT TERM HUP

  if _gq_id=$(GQ_OWNER="$_gq_owner" "$_gq_bin" submit "$@"); then
    _gq_result=0
  else
    _gq_result=$?
  fi
  if [[ "$_gq_result" != 0 ]]; then
    : # gq submit cancels its own saved request if interrupted before printing ID.
  elif [[ ! "$_gq_id" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
    printf 'gq: submit returned an invalid job id: %s\n' "$_gq_id" >&2
    _gq_result=1
  else
    printf 'gq: submitted %s (%s)\n' "$_gq_id" "$_gq_owner" >&2
    if [[ -n "${GQ_JOB_ID_FILE:-}" ]]; then
      if ! printf '%s\n' "$_gq_id" > "$GQ_JOB_ID_FILE"; then
        _gq_job_cancel
      fi
    fi
    # A PID-only signal during submit is handled when its ID becomes available.
    if [[ "$_gq_interrupted" == 1 ]]; then
      _gq_job_cancel
    fi

    # The launcher handles terminal/process-group signals and cancels the job.
    # Let wait observe that terminal state instead of dying with the same signal.
    GQ_OWNER="$_gq_owner" bash -c 'trap "" INT TERM HUP; exec "$@"' \
      gq-wait "$_gq_bin" wait "$_gq_id" &
    _gq_wait_pid=$!
    while :; do
      _gq_interrupted=0
      if wait "$_gq_wait_pid"; then
        _gq_result=0
      else
        _gq_result=$?
      fi
      # A trapped signal interrupts Bash's wait before the child exits. Reap it
      # after cancellation so the returned status still comes from gq wait.
      [[ "$_gq_interrupted" == 1 ]] || break
    done
  fi
  trap - INT TERM HUP
  eval "$_gq_saved_traps"
  return "$_gq_result"
}

#!/usr/bin/env bash
# Source this file on the execution host. Keep submit separate from wait: the
# caller may have EOF on stdin (an SSH heredoc, nohup, or </dev/null).

gq_available() {
  [[ -x "${GQ_BIN:-$HOME/gothic-ai/bin/gq}" ]]
}

gq_job() (
  local _gq_bin="${GQ_BIN:-$HOME/gothic-ai/bin/gq}"
  local _gq_root="${HOME%/}/gothic-ai" _gq_cwd _gq_owner
  local _gq_id='' _gq_wait_pid _gq_result
  local _gq_interrupted=0 _gq_cancelled=0

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
  export GQ_OWNER="$_gq_owner"

  _gq_cancel() {
    if [[ -n "$_gq_id" && "$_gq_cancelled" == 0 ]]; then
      _gq_cancelled=1
      if ! "$_gq_bin" cancel "$_gq_id" >&2; then
        printf 'gq: could not cancel %s\n' "$_gq_id" >&2
      fi
    fi
  }
  _gq_interrupt() {
    _gq_interrupted=1
    _gq_cancel
  }
  trap '_gq_interrupt' INT TERM HUP

  if _gq_id=$("$_gq_bin" submit "$@"); then
    :
  else
    return "$?"
  fi
  if [[ ! "$_gq_id" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
    printf 'gq: submit returned an invalid job id: %s\n' "$_gq_id" >&2
    return 1
  fi
  printf 'gq: submitted %s (%s)\n' "$_gq_id" "$GQ_OWNER" >&2
  if [[ -n "${GQ_JOB_ID_FILE:-}" ]]; then
    if ! printf '%s\n' "$_gq_id" > "$GQ_JOB_ID_FILE"; then
      _gq_cancel
    fi
  fi
  # A signal during submit is remembered until its ID is available.
  if [[ "$_gq_interrupted" == 1 ]]; then
    _gq_cancel
  fi

  "$_gq_bin" wait "$_gq_id" &
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
    if [[ "$_gq_interrupted" == 0 ]]; then
      return "$_gq_result"
    fi
  done
)

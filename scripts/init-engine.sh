#!/usr/bin/env bash
# Prepares the engine in OpenGothic/: fetches its libraries (git submodules) and builds the game.
# In a git worktree the libraries are copied from the main checkout when it has them, so nothing
# is downloaded; the copies are independent of it (--dissociate).
#
#   scripts/init-engine.sh              # libraries + RelWithDebInfo build of Gothic2Notr
#   scripts/init-engine.sh --no-build   # libraries only
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="$(git -C "$ROOT" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0

# init <repo> <path>: one submodule, borrowing objects from the same path in the main checkout
init() {
  local repo="$1" path="$2" ref
  ref="$MAIN/${repo#"$ROOT"}/$path"
  ref="${ref//\/\//\/}"
  if [ "$MAIN" != "$ROOT" ] && git -C "$ref" rev-parse --git-dir >/dev/null 2>&1; then
    git -C "$repo" submodule update --init --reference "$ref" --dissociate -- "$path"
  else
    git -C "$repo" submodule update --init -- "$path"
  fi
  # nested submodules (ZenKit's vendor/, rapidjson's thirdparty/)
  local p
  for p in $(gitlinks "$repo/$path"); do
    init "$repo/$path" "$p"
  done
}

# submodules actually recorded in a repository; .gitmodules may list stale ones (ZenKit's vendor/mio)
gitlinks() {
  git -C "$1" ls-files --stage | awk '$1=="160000" {print $4}'
}

for p in $(gitlinks "$ROOT"); do
  init "$ROOT" "$p"
done

if [ "$BUILD" = 1 ]; then
  cmake -S "$ROOT/OpenGothic" -B "$ROOT/OpenGothic/build" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
  cmake --build "$ROOT/OpenGothic/build" --target Gothic2Notr
fi

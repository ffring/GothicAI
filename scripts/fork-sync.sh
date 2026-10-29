#!/usr/bin/env bash
# Merges the author's main into this fork's main: one ordinary merge commit, pushed without force, so the
# fork's own commits stay exactly as they are. Run by .github/workflows/fork-sync.yml; what to do when it
# stops is in docs/fork-sync.md.
#
#   scripts/fork-sync.sh FORK UPSTREAM [--dry-run]
#
# FORK and UPSTREAM are anything git fetches from (a URL, a remote name, a path); both are taken at main.
# The merge is git's own (merge-tree, the same ort merge as `git merge`) and needs no working tree: the
# script only adds objects and FETCH_HEAD to the current repository, its working tree, index, HEAD,
# branches and tags stay as they are. It pushes FORK's main only when the merge is clean and the author
# changed no workflow files: the Actions token may not push those, and a new workflow would run in the fork,
# so a person reads them first. When the fork's main moves meanwhile, the merge is redone on top of it.
#
# Exit status, nothing is pushed unless it is 0: 0 up to date, merged or, with --dry-run, merges cleanly;
# 1 conflicts; 2 usage or a failed merge; 3 the author changed workflow files; 4 the push was refused.
set -euo pipefail

case "$#:${3:-}" in
  2: | 3:--dry-run) ;;
  *) echo "usage: $0 FORK UPSTREAM [--dry-run]" >&2; exit 2 ;;
esac
fork=$1 upstream=$2 dry_run=${3:-}

note() {  # to the log and, in Actions, to the run's summary
  printf '%s\n' "$@"
  if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then printf '%s\n' "$@" >>"$GITHUB_STEP_SUMMARY"; fi
}

head_of() {  # the commit of main in $1; a failed fetch fails the caller rather than leave an old FETCH_HEAD
  git fetch -q --no-tags "$1" main && git rev-parse FETCH_HEAD
}

theirs=$(head_of "$upstream")
refused=
for attempt in 1 2 3; do
  ours=$(head_of "$fork")
  if git merge-base --is-ancestor "$theirs" "$ours"; then
    note "Up to date: main ${ours:0:12} already contains the author's main ${theirs:0:12}, nothing pushed."
    exit 0
  fi
  if [[ $ours == "$refused" ]]; then
    note "The push was refused although main did not move, nothing pushed: check the rules on main and the token's rights."
    exit 4
  fi
  if (( attempt == 1 )); then
    note "The author's main ${theirs:0:12} has $(git rev-list --count "$ours..$theirs") commits that main ${ours:0:12} lacks." \
      "Files the author changed, by top-level entry:" '```' \
      "$(git diff --name-only "$ours...$theirs" | cut -d/ -f1 | sort | uniq -c | sort -rn)" '```'
  fi

  status=0
  merged=$(git merge-tree --write-tree --name-only --no-messages "$ours" "$theirs") || status=$?
  if (( status > 1 )); then
    note "git merge-tree failed with status $status, nothing pushed."
    exit 2
  fi
  workflows=$(git log --full-history --format= --name-only "$ours..$theirs" -- .github/workflows | sed '/^$/d' | sort -u)
  if (( status == 1 )); then
    note "Conflicts, nothing pushed. Merge by hand as docs/fork-sync.md says; the conflicted files:" '```' \
      "$(tail -n +2 <<<"$merged" | sed '/^$/d')" '```'
  fi
  if [[ -n $workflows ]]; then
    note "The author changed workflow files, nothing pushed: the Actions token may not push them, and they would run in this fork." \
      "Read them, then merge by hand as docs/fork-sync.md says:" '```' "$workflows" '```'
  fi
  if (( status == 1 )); then exit 1; fi
  if [[ -n $workflows ]]; then exit 3; fi
  if [[ -n $dry_run ]]; then
    note "Dry run: the author's main merges cleanly into main, nothing pushed."
    exit 0
  fi

  commit=$(git commit-tree "$(head -n 1 <<<"$merged")" -p "$ours" -p "$theirs" -m "Merge branch 'main' of ${upstream%.git}")
  if git push -q "$fork" "$commit:refs/heads/main"; then
    note "Merged: main ${ours:0:12} -> ${commit:0:12}, an ordinary merge commit with the author's main ${theirs:0:12}; the fork's commits are kept as they were."
    exit 0
  fi
  refused=$ours
  note "The push was refused; main may have moved meanwhile, merging again."
done
note "The push was refused three times as main kept moving, nothing pushed: run the workflow again."
exit 4

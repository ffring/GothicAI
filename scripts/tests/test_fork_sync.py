"""scripts/fork-sync.sh on throwaway local repositories: what it merges, what it refuses, and that the fork's
main only ever moves forward. No network: the author's repository and the fork are bare repositories here."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "fork-sync.sh"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


class Repos:
    def __init__(self, root: Path):
        self.root = root
        self.upstream = root / "upstream.git"
        self.fork = root / "fork.git"
        self.scratch = root / "scratch"  # the clone the script runs in, like the workflow's checkout
        self.clones = 0
        seed = self.clone_dir()
        git("init", "-q", "-b", "main", str(seed))
        (seed / "shared.txt").write_text("one\n")
        git("-C", str(seed), "add", "-A")
        git("-C", str(seed), "commit", "-q", "-m", "seed")
        git("clone", "-q", "--bare", str(seed), str(self.upstream))
        git("clone", "-q", "--bare", str(seed), str(self.fork))
        git("init", "-q", str(self.scratch))

    def clone_dir(self) -> Path:
        self.clones += 1
        return self.root / f"clone-{self.clones}"

    def commit(self, repo: Path, path: str, text: str, push: bool = True) -> Path:
        """A commit on main of `repo` from a fresh clone; returns the clone."""
        work = self.clone_dir()
        git("clone", "-q", str(repo), str(work))
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text(text)
        git("-C", str(work), "add", "-A")
        git("-C", str(work), "commit", "-q", "-m", f"{path} in {repo.name}")
        if push:
            git("-C", str(work), "push", "-q", "origin", "HEAD:main")
        return work

    def main(self, repo: Path) -> str:
        return git("--git-dir", str(repo), "rev-parse", "main")

    def sync(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SCRIPT), str(self.fork), str(self.upstream), *args],
                              cwd=self.scratch, env=env, capture_output=True, text=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))  # no signing or hooks of the developer's own config
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return Repos(tmp_path)


def test_a_fork_that_already_has_the_authors_main_is_left_alone(repos):
    repos.commit(repos.fork, "windows.txt", "fork work\n")
    before = repos.main(repos.fork)
    result = repos.sync()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Up to date" in result.stdout
    assert repos.main(repos.fork) == before


def test_a_clean_merge_keeps_the_forks_commits_and_a_second_run_does_nothing(repos):
    repos.commit(repos.upstream, "engine.txt", "author\n")
    repos.commit(repos.fork, "windows.txt", "fork work\n")
    fork_before, upstream = repos.main(repos.fork), repos.main(repos.upstream)
    result = repos.sync()
    assert result.returncode == 0, result.stdout + result.stderr
    merged = repos.main(repos.fork)
    assert git("--git-dir", str(repos.fork), "rev-list", "--parents", "-n", "1", merged).split()[1:] == [fork_before, upstream]
    files = git("--git-dir", str(repos.fork), "ls-tree", "--name-only", merged).split()
    assert {"engine.txt", "windows.txt", "shared.txt"} <= set(files)
    again = repos.sync()
    assert again.returncode == 0 and "Up to date" in again.stdout
    assert repos.main(repos.fork) == merged


def test_a_conflict_pushes_nothing_and_names_the_file(repos):
    repos.commit(repos.upstream, "shared.txt", "author\n")
    repos.commit(repos.fork, "shared.txt", "fork\n")
    before = repos.main(repos.fork)
    result = repos.sync()
    assert result.returncode == 1
    assert "shared.txt" in result.stdout
    assert repos.main(repos.fork) == before


def test_workflow_files_from_the_author_wait_for_a_person(repos):
    repos.commit(repos.upstream, ".github/workflows/new.yml", "on: push\n")
    before = repos.main(repos.fork)
    result = repos.sync()
    assert result.returncode == 3
    assert ".github/workflows/new.yml" in result.stdout
    assert repos.main(repos.fork) == before


def test_a_dry_run_pushes_nothing(repos):
    repos.commit(repos.upstream, "engine.txt", "author\n")
    before = repos.main(repos.fork)
    result = repos.sync("--dry-run")
    assert result.returncode == 0 and "Dry run" in result.stdout
    assert repos.main(repos.fork) == before


def test_main_moving_on_another_pc_is_merged_on_top_not_overwritten(repos, tmp_path):
    repos.commit(repos.upstream, "engine.txt", "author\n")
    other_pc = repos.commit(repos.fork, "launcher.txt", "other pc\n", push=False)
    pushed_by_other_pc = git("-C", str(other_pc), "rev-parse", "HEAD")
    # A git in front of the real one: the other PC pushes just before the sync's first push.
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "git").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = push ] && [ ! -e "$RACE_DONE" ]; then\n'
        '  : > "$RACE_DONE"\n'
        '  "$REAL_GIT" -C "$OTHER_PC" push -q origin HEAD:main || exit 1\n'
        "fi\n"
        'exec "$REAL_GIT" "$@"\n')
    (shim / "git").chmod((shim / "git").stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{shim}{os.pathsep}{os.environ['PATH']}", REAL_GIT=shutil.which("git"),
               OTHER_PC=str(other_pc), RACE_DONE=str(tmp_path / "raced"))
    result = repos.sync(env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "refused" in result.stdout
    merged = repos.main(repos.fork)
    assert git("--git-dir", str(repos.fork), "rev-list", "--parents", "-n", "1", merged).split()[1:] == [
        pushed_by_other_pc, repos.main(repos.upstream)]


def test_a_refused_push_stops_without_forcing(repos):
    repos.commit(repos.upstream, "engine.txt", "author\n")
    hook = repos.fork / "hooks" / "pre-receive"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'main is protected' >&2\nexit 1\n")
    hook.chmod(0o755)
    before = repos.main(repos.fork)
    result = repos.sync()
    assert result.returncode == 4
    assert "did not move" in result.stdout
    assert repos.main(repos.fork) == before

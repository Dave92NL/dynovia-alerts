"""The git handling inside the scrape workflow.

Two runs writing data/dynovia.db is the one thing this loop cannot talk its
way out of: a SQLite file has no merge. It has already taken the job down
once, so the recovery is exercised here against real repositories rather than
trusted to read correctly.

The function under test is read out of the workflow itself, not copied, so
editing the workflow is what this test is measuring.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest
import yaml

WORKFLOW = ".github/workflows/scrape.yml"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="brak basha")


def take_origin_source() -> str:
    """The recovery function, lifted verbatim from the workflow."""
    workflow = yaml.safe_load(open(WORKFLOW, encoding="utf-8"))
    script = next(
        step["run"]
        for step in workflow["jobs"]["run"]["steps"]
        if "take_origin()" in (step.get("run") or "")
    )
    found = re.search(r"^ *take_origin\(\) \{.*?^ *\}", script, re.S | re.M)
    assert found, "take_origin zniknelo z workflow"
    return found.group(0)


def git(*args: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


@pytest.fixture
def two_runs(tmp_path):
    """A shared origin and two clones of it, as two overlapping jobs would be."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git("init", "--bare", "--initial-branch=main", ".", cwd=origin)

    first = tmp_path / "first"
    git("clone", str(origin), str(first), cwd=tmp_path)
    (first / "data").mkdir()
    # A NUL byte, so git treats it as the binary file it stands in for.
    (first / "data" / "db.bin").write_bytes(b"v1\x00")
    for args in (
        ("config", "user.email", "t@t"),
        ("config", "user.name", "t"),
        ("add", "-A"),
        ("commit", "-m", "v1"),
        ("push", "-u", "origin", "main"),
    ):
        git(*args, cwd=first)

    second = tmp_path / "second"
    git("clone", str(origin), str(second), cwd=tmp_path)
    git("config", "user.email", "t@t", cwd=second)
    git("config", "user.name", "t", cwd=second)
    return first, second


def run_commit_and_push(repo) -> subprocess.CompletedProcess:
    """The workflow's own recovery, wrapped in the same push it guards."""
    script = f"""
set -eo pipefail
{take_origin_source()}
if git pull --rebase --autostash && git push --quiet; then
  echo WYPCHNIETE
else
  take_origin
  echo WZIETO_ORIGIN
fi
"""
    return subprocess.run(
        [BASH, "-c", script], cwd=repo, capture_output=True, text=True
    )


def test_a_clean_tick_just_pushes(two_runs):
    _, second = two_runs
    (second / "data" / "db.bin").write_bytes(b"v2\x00")
    git("commit", "-am", "dane", cwd=second)

    done = run_commit_and_push(second)
    assert done.returncode == 0
    assert "WYPCHNIETE" in done.stdout


def test_the_loser_of_a_race_takes_origin_instead_of_dying(two_runs):
    first, second = two_runs
    # The other run got there first.
    (first / "data" / "db.bin").write_bytes(b"pierwszy\x00")
    git("commit", "-am", "dane", cwd=first)
    git("push", cwd=first)

    # And this one wrote its own copy of the same unmergeable file.
    (second / "data" / "db.bin").write_bytes(b"drugi\x00")
    git("commit", "-am", "dane", cwd=second)

    done = run_commit_and_push(second)
    # The job survives. Before the fix this was exit 1 and a "workflow padł".
    assert done.returncode == 0, done.stderr
    assert "WZIETO_ORIGIN" in done.stdout

    # It ends up exactly at the winner's state, with nothing half-rebased
    # left behind to poison the next tick.
    assert (second / "data" / "db.bin").read_bytes() == b"pierwszy\x00"
    assert git("status", "--porcelain", cwd=second).stdout == ""
    assert not (second / ".git" / "rebase-merge").exists()
    assert (
        git("rev-parse", "HEAD", cwd=second).stdout
        == git("rev-parse", "origin/main", cwd=second).stdout
    )

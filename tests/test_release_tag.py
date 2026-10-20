"""The release workflow refuses a tag that does not match the package version,
and does so before the full CI run (issue #1)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_release_tag.py"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"


def _project(tmp_path: Path, pyproject_version: str, init_version: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "pipeprof"\nversion = "{pyproject_version}"\n'
    )
    pkg = tmp_path / "src" / "pipeprof"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'"""Doc."""\n\n__version__ = "{init_version}"\n')
    return tmp_path


def _run(root: Path, tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), tag, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_matching_tag_passes(tmp_path: Path) -> None:
    result = _run(_project(tmp_path, "1.2.0", "1.2.0"), "v1.2.0")
    assert result.returncode == 0, result.stderr
    assert "v1.2.0" in result.stdout


@pytest.mark.parametrize("tag", ["v1.2.1", "1.2.0", "v1.2.0rc1", "v1.2"])
def test_wrong_tag_fails(tmp_path: Path, tag: str) -> None:
    result = _run(_project(tmp_path, "1.2.0", "1.2.0"), tag)
    assert result.returncode == 1
    assert "1.2.0" in result.stderr


def test_package_and_pyproject_must_agree(tmp_path: Path) -> None:
    result = _run(_project(tmp_path, "1.2.0", "1.1.0"), "v1.2.0")
    assert result.returncode == 1
    assert "__version__" in result.stderr


def test_this_repository_versions_agree() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--versions-only", "--root", str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _job(text: str, name: str) -> str:
    """The lines of one top-level job in a workflow file."""
    match = re.search(rf"^  {name}:\n((?:    .*\n|\s*\n)*)", text, re.MULTILINE)
    assert match, f"job {name} not found"
    return match.group(1)


def test_tag_check_runs_before_ci() -> None:
    text = RELEASE.read_text()
    tag_job = _job(text, "tag")
    assert "scripts/check_release_tag.py" in tag_job
    assert "needs:" not in tag_job
    assert re.search(r"^    needs: \[?tag\]?$", _job(text, "ci"), re.MULTILINE)
    # the check runs once, in its own job, not again after CI
    assert text.count("check_release_tag.py") == 1

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeprof.logkit import RunLogger, new_run_id, read_jsonl, step_durations
from pipeprof.provenance import (
    capture,
    git_state,
    hash_inputs,
    package_versions,
    safe_environment,
    sha256_file,
    tool_version,
)


def test_run_logger_writes_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "run.jsonl"
    console = io.StringIO()
    with RunLogger("align", jsonl_path=path, run_id="r1", console=console) as log:
        log.info("hello", sample="NA12878")
        log.warning("careful")
        with log.step("bwa", sample="NA12878") as fields:
            fields["reads"] = 10
        with pytest.raises(RuntimeError), log.step("broken"):
            raise RuntimeError("boom")
        log.error("bad", code=3)
    records = read_jsonl(path)
    assert {r["run_id"] for r in records} == {"r1"}
    end = [r for r in records if r["event"] == "step_end"]
    assert end[0]["status"] == "ok" and end[0]["reads"] == 10 and end[0]["sample"] == "NA12878"
    assert end[1]["status"] == "error" and end[1]["exc_type"] == "RuntimeError"
    assert "boom" in end[1]["error"]
    assert step_durations(records).keys() == {"bwa", "broken"}
    assert "hello sample=NA12878" in console.getvalue()


def test_add_stream_and_defaults(tmp_path: Path) -> None:
    log = RunLogger(console=None)
    stream = io.StringIO()
    log.add_stream(stream)
    log.event("custom", path=tmp_path, items={1, 2})
    log.add_stream(io.StringIO(), json_lines=False)
    log.close()
    payload = json.loads(stream.getvalue())
    assert payload["event"] == "custom" and payload["path"] == str(tmp_path)


def test_run_id_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPEPROF_RUN_ID", "shared-7")
    assert new_run_id() == "shared-7"
    monkeypatch.delenv("PIPEPROF_RUN_ID")
    assert len(new_run_id().split("-")[-1]) == 6


def test_read_jsonl_reports_bad_line(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": 1}\n\n[1, 2]\nnot json\n')
    with pytest.raises(ValueError, match=":4:"):
        read_jsonl(path)


def test_sha256_and_inputs(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("abc")
    assert sha256_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    hashes = hash_inputs([f, tmp_path / "missing.txt"])
    assert len(hashes) == 1 and hashes[0].size_bytes == 3


def test_environment_allow_list_drops_secrets() -> None:
    env = {
        "SLURM_JOB_ID": "77",
        "SLURM_AUTH_TOKEN": "x",
        "AWS_SECRET_ACCESS_KEY": "y",
        "HOME": "/home/a",
        "CONDA_DEFAULT_ENV": "bio",
    }
    assert safe_environment(env) == {"CONDA_DEFAULT_ENV": "bio", "SLURM_JOB_ID": "77"}


HAVE_GIT = shutil.which("git") is not None


def test_git_state_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with caplog.at_level(logging.DEBUG, logger="pipeprof.provenance"):
        assert git_state(tmp_path) == {"commit": None, "branch": None, "dirty": None}
    assert "git not found" in caplog.text
    prov = capture(inputs=[], tools=["git"], git_path=tmp_path).to_dict()
    assert prov["git"]["commit"] is None and prov["tool_versions"]["git"] is None


def test_git_state_with_broken_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A PATH entry that names git but cannot be executed.
    monkeypatch.setattr(shutil, "which", lambda name: str(tmp_path / "no-such-git"))
    assert git_state(tmp_path)["commit"] is None


@pytest.mark.skipif(not HAVE_GIT, reason="git is not installed on this host")
def test_git_state_in_and_outside_a_repository(tmp_path: Path) -> None:
    assert git_state(tmp_path)["commit"] is None  # not a repository
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "f").write_text("1")
    subprocess.run(["git", "-C", str(tmp_path), "add", "f"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.org",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
    )
    state = git_state(tmp_path)
    assert isinstance(state["commit"], str) and len(state["commit"]) == 40
    assert state["dirty"] is False


def test_tool_version_with_a_fake_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = tmp_path / "faketool"
    tool.write_text("#!/bin/sh\necho 'faketool 9.9.1'\necho 'second line'\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert tool_version("faketool") == "faketool 9.9.1"
    assert tool_version("surely-not-a-tool-pipeprof") is None
    assert package_versions(["psutil", "not-a-package-pipeprof"])["not-a-package-pipeprof"] is None
    prov = capture(inputs=[], tools=["faketool"], git_path=tmp_path).to_dict()
    assert prov["tool_versions"]["faketool"] == "faketool 9.9.1"
    assert prov["memory_total_bytes"] > 0

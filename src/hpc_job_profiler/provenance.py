"""Capture provenance: environment, tool versions, git commit and input hashes.

Only an allow list of environment variables is recorded, so tokens and
passwords that happen to sit in the environment never reach a log file.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

import psutil

from hpc_job_profiler import __version__
from hpc_job_profiler.logkit import utc_now_iso

#: Environment variables that are safe and useful to record.
ENV_ALLOW_PREFIXES = ("SLURM_", "SBATCH_", "PBS_", "NXF_", "SNAKEMAKE_", "HJP_")
ENV_ALLOW_NAMES = (
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "VIRTUAL_ENV",
    "APPTAINER_NAME",
    "SINGULARITY_NAME",
    "SINGULARITY_CONTAINER",
    "OMP_NUM_THREADS",
    "TMPDIR",
    "LANG",
)
#: Substrings that mark a variable as secret even when its prefix is allowed.
ENV_DENY_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "KEY", "CREDENTIAL", "AUTH")

DEFAULT_TOOLS = ("bcftools", "samtools", "nextflow", "snakemake", "sbatch", "sacct")


@dataclass
class InputHash:
    """SHA-256 and size of one input file."""

    path: str
    size_bytes: int
    sha256: str


@dataclass
class Provenance:
    """Everything needed to say where and how a result was produced."""

    captured_at: str
    hjp_version: str
    hostname: str
    platform: dict[str, str]
    python: str
    cpu_count: int | None
    memory_total_bytes: int
    environment: dict[str, str]
    tool_versions: dict[str, str | None]
    git: dict[str, str | bool | None]
    inputs: list[InputHash] = field(default_factory=list)
    packages: dict[str, str | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON ready dictionary."""
        return asdict(self)


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    """Return the hex SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def safe_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Return the allow listed environment variables, without secrets."""
    source = dict(os.environ) if environ is None else environ
    kept: dict[str, str] = {}
    for name, value in sorted(source.items()):
        allowed = name in ENV_ALLOW_NAMES or name.startswith(ENV_ALLOW_PREFIXES)
        if not allowed:
            continue
        if any(marker in name.upper() for marker in ENV_DENY_MARKERS):
            continue
        kept[name] = value
    return kept


def tool_version(tool: str, timeout: float = 10.0) -> str | None:
    """Return the first line of ``tool --version``, or ``None`` when it is not installed."""
    exe = shutil.which(tool)
    if exe is None:
        return None
    try:
        # errors="replace": samtools 1.21 on Debian prints a non UTF-8 byte in --version.
        result = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0].strip() if text else None


def git_state(path: str | Path = ".") -> dict[str, str | bool | None]:
    """Return the commit, branch and dirty flag of the git work tree at ``path``."""
    state: dict[str, str | bool | None] = {"commit": None, "branch": None, "dirty": None}
    git = shutil.which("git")
    if git is None:
        return state

    def run(args: Sequence[str]) -> str | None:
        try:
            result = subprocess.run(
                [git, "-C", str(path), *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    state["commit"] = run(["rev-parse", "HEAD"])
    state["branch"] = run(["rev-parse", "--abbrev-ref", "HEAD"])
    status = run(["status", "--porcelain", "--untracked-files=no"])
    state["dirty"] = None if status is None else bool(status)
    return state


def hash_inputs(paths: Iterable[str | Path]) -> list[InputHash]:
    """Hash each existing input file."""
    hashes: list[InputHash] = []
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            hashes.append(
                InputHash(path=str(path), size_bytes=path.stat().st_size, sha256=sha256_file(path))
            )
    return hashes


def package_versions(names: Iterable[str] = ("psutil", "typer", "rich")) -> dict[str, str | None]:
    """Return installed versions of Python packages."""
    versions: dict[str, str | None] = {}
    for name in names:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def capture(
    inputs: Iterable[str | Path] = (),
    tools: Iterable[str] = DEFAULT_TOOLS,
    git_path: str | Path = ".",
) -> Provenance:
    """Capture provenance for the current process."""
    uname = platform.uname()
    return Provenance(
        captured_at=utc_now_iso(),
        hjp_version=__version__,
        hostname=socket.gethostname(),
        platform={
            "system": uname.system,
            "release": uname.release,
            "machine": uname.machine,
        },
        python=sys.version.split()[0],
        cpu_count=os.cpu_count(),
        memory_total_bytes=int(psutil.virtual_memory().total),
        environment=safe_environment(),
        tool_versions={tool: tool_version(tool) for tool in tools},
        git=git_state(git_path),
        inputs=hash_inputs(inputs),
        packages=package_versions(),
    )

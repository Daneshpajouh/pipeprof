"""A small structured logging library: JSON lines, run IDs and step timing.

It sits on top of the standard :mod:`logging` module, so any pipeline that
already uses ``logging`` can add a JSON lines file without changing its calls.

Example::

    from hpc_job_profiler.logkit import RunLogger

    log = RunLogger("align", jsonl_path="logs/align.jsonl")
    with log.step("bwa_mem", sample="NA12878") as fields:
        run_bwa()
        fields["reads"] = 1_204_311
    log.close()

Each line in ``logs/align.jsonl`` is one JSON object with at least ``ts``
(UTC, ISO 8601), ``level``, ``logger``, ``run_id``, ``event`` and ``msg``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import secrets
import sys
import time
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, TextIO

_FIELDS_ATTR = "hjp_fields"
_EVENT_ATTR = "hjp_event"


def utc_now_iso() -> str:
    """Return the current UTC time as ISO 8601 with milliseconds and a ``Z`` suffix."""
    now = dt.datetime.now(dt.UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def new_run_id() -> str:
    """Return a sortable, unique run ID such as ``20261019T180102Z-3f2a9c``.

    The ``HJP_RUN_ID`` environment variable, when set, wins, so a pipeline and
    every tool it calls can share one ID.
    """
    env_id = os.environ.get("HJP_RUN_ID")
    if env_id:
        return env_id
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    return repr(value)


class JsonLinesFormatter(logging.Formatter):
    """Format a log record as one JSON object per line."""

    def __init__(self, run_id: str, name: str | None = None) -> None:
        super().__init__()
        self.run_id = run_id
        self.name = name

    def format(self, record: logging.LogRecord) -> str:
        created = dt.datetime.fromtimestamp(record.created, dt.UTC)
        payload: dict[str, Any] = {
            "ts": created.strftime("%Y-%m-%dT%H:%M:%S.") + f"{created.microsecond // 1000:03d}Z",
            "level": record.levelname,
            "logger": self.name or record.name,
            "run_id": self.run_id,
            "event": getattr(record, _EVENT_ATTR, "message"),
            "msg": record.getMessage(),
        }
        fields = getattr(record, _FIELDS_ATTR, None)
        if isinstance(fields, Mapping):
            for key, value in fields.items():
                if key not in payload:
                    payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
            payload["exc_text"] = "".join(traceback.format_exception(*record.exc_info)).strip()
        return json.dumps(payload, default=_json_default, sort_keys=False)


class ConsoleFormatter(logging.Formatter):
    """Short human readable lines for the terminal."""

    def format(self, record: logging.LogRecord) -> str:
        created = dt.datetime.fromtimestamp(record.created, dt.UTC).strftime("%H:%M:%SZ")
        fields = getattr(record, _FIELDS_ATTR, None)
        extra = ""
        if isinstance(fields, Mapping) and fields:
            extra = " " + " ".join(f"{k}={v}" for k, v in fields.items())
        return f"{created} {record.levelname:<7} {record.getMessage()}{extra}"


class RunLogger:
    """A logger bound to one run ID that writes JSON lines and optional console text.

    Parameters
    ----------
    name:
        Logger name, usually the tool or pipeline step.
    jsonl_path:
        File that receives one JSON object per line. Parent folders are created.
    run_id:
        Shared run ID. Defaults to :func:`new_run_id`.
    console:
        Stream for human readable lines, or ``None`` for no console output.
    level:
        Minimum level to record.
    """

    def __init__(
        self,
        name: str = "hjp",
        jsonl_path: str | Path | None = None,
        run_id: str | None = None,
        console: TextIO | None = sys.stderr,
        level: int = logging.INFO,
    ) -> None:
        self.run_id = run_id or new_run_id()
        self.name = name
        self.jsonl_path = Path(jsonl_path) if jsonl_path is not None else None
        # A unique child logger keeps handlers of different runs apart.
        self._logger = logging.getLogger(f"{name}.{self.run_id}.{id(self)}")
        self._logger.setLevel(level)
        self._logger.propagate = False
        self._handlers: list[logging.Handler] = []
        if self.jsonl_path is not None:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(self.jsonl_path, encoding="utf-8")
            file_handler.setFormatter(JsonLinesFormatter(self.run_id, name))
            self._add_handler(file_handler)
        if console is not None:
            stream_handler = logging.StreamHandler(console)
            stream_handler.setFormatter(ConsoleFormatter())
            self._add_handler(stream_handler)

    def _add_handler(self, handler: logging.Handler) -> None:
        self._logger.addHandler(handler)
        self._handlers.append(handler)

    def add_stream(self, stream: IO[str], json_lines: bool = True) -> None:
        """Send records to another stream, as JSON lines or console text."""
        handler = logging.StreamHandler(stream)
        handler.setFormatter(
            JsonLinesFormatter(self.run_id, self.name) if json_lines else ConsoleFormatter()
        )
        self._add_handler(handler)

    def _emit(
        self,
        level: int,
        msg: str,
        event: str,
        fields: Mapping[str, Any],
        exc_info: bool = False,
    ) -> None:
        self._logger.log(
            level,
            msg,
            exc_info=exc_info,
            extra={_EVENT_ATTR: event, _FIELDS_ATTR: dict(fields)},
        )

    def event(self, event: str, msg: str = "", level: int = logging.INFO, **fields: Any) -> None:
        self._emit(level, msg or event, event, fields)

    def info(self, msg: str, **fields: Any) -> None:
        self.event("message", msg, logging.INFO, **fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self.event("warning", msg, logging.WARNING, **fields)

    def error(self, msg: str, **fields: Any) -> None:
        self.event("error", msg, logging.ERROR, **fields)

    @contextmanager
    def step(self, name: str, **fields: Any) -> Iterator[dict[str, Any]]:
        """Time a step and log ``step_start`` and ``step_end`` events.

        The yielded dictionary can be filled with results (counts, paths); they
        are added to the ``step_end`` event. On an exception the step is logged
        with ``status="error"`` and the exception is re-raised.
        """
        extra: dict[str, Any] = {}
        start_wall = time.perf_counter()
        start_cpu = time.process_time()
        self._emit(logging.INFO, f"start {name}", "step_start", {"step": name, **fields})
        try:
            yield extra
        except BaseException as exc:
            payload = {
                **fields,
                **extra,
                "step": name,
                "status": "error",
                "duration_s": round(time.perf_counter() - start_wall, 6),
                "cpu_s": round(time.process_time() - start_cpu, 6),
                "error": f"{type(exc).__name__}: {exc}",
            }
            self._emit(logging.ERROR, f"failed {name}", "step_end", payload, exc_info=True)
            raise
        payload = {
            **fields,
            **extra,
            "step": name,
            "status": "ok",
            "duration_s": round(time.perf_counter() - start_wall, 6),
            "cpu_s": round(time.process_time() - start_cpu, 6),
        }
        self._emit(logging.INFO, f"done {name}", "step_end", payload)

    def close(self) -> None:
        for handler in self._handlers:
            handler.flush()
            handler.close()
            self._logger.removeHandler(handler)
        self._handlers.clear()

    def __enter__(self) -> RunLogger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSON lines file, skipping blank lines and reporting the bad line number."""
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: not valid JSON: {exc.msg}") from exc
            if isinstance(value, dict):
                records.append(value)
    return records


def step_durations(records: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Collect ``duration_s`` of each finished step, keyed by step name."""
    durations: dict[str, list[float]] = {}
    for record in records:
        if record.get("event") == "step_end" and "duration_s" in record:
            durations.setdefault(str(record.get("step")), []).append(float(record["duration_s"]))
    return durations

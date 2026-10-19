"""Terminal view of a watch log, refreshed as new lines arrive."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from hpc_job_profiler.units import format_memory
from hpc_job_profiler.watch import read_watch_log

_BARS = " ▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 40) -> str:
    """Unicode bar sparkline of the last ``width`` values."""
    data = values[-width:]
    if not data:
        return ""
    top = max(data) or 1.0
    return "".join(_BARS[min(8, int(8 * v / top))] for v in data)


def render(records: list[dict[str, Any]]) -> Group:
    """Build the view from watch log records."""
    samples = [r for r in records if r.get("type") == "sample"]
    alerts = [r for r in records if r.get("type") == "alert"]
    table = Table(title="hjp watch", show_header=True)
    for column in ("elapsed", "RSS", "peak RSS", "CPU s", "procs/steps"):
        table.add_column(column)
    if samples:
        last = samples[-1]
        peak = max(int(s.get("rss_bytes") or 0) for s in samples)
        procs = last.get("num_procs", len(last.get("steps", [])))
        table.add_row(
            f"{float(last.get('elapsed_s') or 0):.0f} s",
            format_memory(int(last.get("rss_bytes") or 0)),
            format_memory(peak),
            f"{float(last.get('cpu_seconds') or 0):.1f}",
            str(procs),
        )
    rss_line = Text("RSS " + sparkline([float(s.get("rss_bytes") or 0) for s in samples]))
    alert_lines = [
        Text(
            f"{a.get('ts', '')} {a.get('alert')} {a.get('status')}: {a.get('message', '')}",
            style="bold red" if a.get("status") == "started" else "green",
        )
        for a in alerts[-5:]
    ]
    return Group(table, rss_line, *alert_lines)


def tail(
    path: str | Path, follow: bool = True, refresh_s: float = 1.0, console: Console | None = None
) -> None:
    """Show the watch log; with ``follow`` keep refreshing until the log ends or Ctrl-C."""
    out = console or Console()
    if not follow:
        out.print(render(read_watch_log(path)))
        return
    with Live(render(read_watch_log(path)), console=out, refresh_per_second=4) as live:
        try:
            while True:
                records = read_watch_log(path)
                live.update(render(records))
                if records and records[-1].get("type") == "end":
                    break
                time.sleep(refresh_s)
        except KeyboardInterrupt:
            pass

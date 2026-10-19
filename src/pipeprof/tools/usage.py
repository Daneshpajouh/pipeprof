"""The resource use of one step, as reported by a measuring tool."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StepUsage:
    """What one tool measured for one step.

    Fields the tool does not report stay ``None``; a reader never fills a gap with
    a guess. ``source`` names the tool so a report can cite its evidence.
    """

    source: str
    step: str | None = None
    wall_s: float | None = None
    user_s: float | None = None
    sys_s: float | None = None
    cpu_percent: float | None = None
    max_rss_bytes: int | None = None
    read_bytes: int | None = None
    write_bytes: int | None = None
    exit_code: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def cpu_s(self) -> float | None:
        """User plus system time, when both are known."""
        if self.user_s is None or self.sys_s is None:
            return None
        return self.user_s + self.sys_s

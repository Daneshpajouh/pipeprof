"""Read py-spy ``--format speedscope`` JSON profiles.

Format: the speedscope file format schema
(https://www.speedscope.app/file-format-schema.json, read 2026-10-19).
Frames live once in ``shared.frames`` and every stack refers to them by
index. A "sampled" profile carries parallel ``samples`` (each a stack, frame
indices ordered from the root at index 0 to the running leaf last) and
``weights`` arrays. An "evented" profile instead carries open ("O") and
close ("C") events against a frame index, each with an "at" timestamp; the
time between two consecutive events belongs to whatever the stack was during
that span. py-spy writes "sampled" profiles; both are supported here since
the schema allows either. py-spy is not installed in this environment; the
fixtures in ``tests/data/tools`` are hand-written from the schema.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class _Frame:
    name: str
    file: str | None
    line: int | None


@dataclass
class FunctionCost:
    """One function's share of a profile's total samples."""

    name: str
    file: str | None
    line: int | None
    self_samples: float
    total_samples: float
    self_share: float
    total_share: float


@dataclass
class SpeedscopeProfile:
    """A parsed profile: its frame table and the (stack, weight) samples that make it up."""

    frames: list[_Frame]
    profile_type: str
    unit: str
    stacks: list[tuple[int, ...]]
    weights: list[float]
    #: Names of the profiles read (py-spy writes one profile per thread).
    profile_names: list[str] = field(default_factory=list)

    def top_functions(self, n: int) -> list[FunctionCost]:
        """The ``n`` functions with the most total samples, busiest first.

        Self samples count a function only when it is the running leaf.
        Total samples count a function once per stack it appears in anywhere,
        even if it recurs more than once in that stack.
        """
        total_weight = sum(self.weights)
        self_weight: dict[int, float] = {}
        total_weight_by_frame: dict[int, float] = {}
        for stack, weight in zip(self.stacks, self.weights, strict=True):
            if stack:
                self_weight[stack[-1]] = self_weight.get(stack[-1], 0.0) + weight
            for frame_index in set(stack):
                total_weight_by_frame[frame_index] = (
                    total_weight_by_frame.get(frame_index, 0.0) + weight
                )

        costs = []
        for frame_index, total in total_weight_by_frame.items():
            frame = self.frames[frame_index]
            own = self_weight.get(frame_index, 0.0)
            costs.append(
                FunctionCost(
                    name=frame.name,
                    file=frame.file,
                    line=frame.line,
                    self_samples=own,
                    total_samples=total,
                    self_share=own / total_weight if total_weight else 0.0,
                    total_share=total / total_weight if total_weight else 0.0,
                )
            )
        costs.sort(key=lambda cost: cost.total_samples, reverse=True)
        return costs[:n]


def _frames_from_shared(shared: Any) -> list[_Frame]:
    try:
        raw_frames = shared["frames"]
    except (KeyError, TypeError) as exc:
        raise ValueError("not a speedscope profile: missing shared.frames") from exc
    frames = []
    for raw in raw_frames:
        try:
            frames.append(_Frame(name=raw["name"], file=raw.get("file"), line=raw.get("line")))
        except (KeyError, TypeError) as exc:
            raise ValueError(f"not a speedscope profile: bad frame entry {raw!r}") from exc
    return frames


def _stacks_from_sampled(profile: dict[str, Any]) -> tuple[list[tuple[int, ...]], list[float]]:
    try:
        raw_samples = profile["samples"]
        raw_weights = profile["weights"]
    except KeyError as exc:
        raise ValueError("sampled profile: missing samples or weights") from exc
    if len(raw_samples) != len(raw_weights):
        raise ValueError("sampled profile: samples and weights differ in length")
    stacks = [tuple(int(index) for index in stack) for stack in raw_samples]
    weights = [float(weight) for weight in raw_weights]
    return stacks, weights


def _stacks_from_evented(profile: dict[str, Any]) -> tuple[list[tuple[int, ...]], list[float]]:
    events = profile.get("events", [])
    stack: list[int] = []
    stacks: list[tuple[int, ...]] = []
    weights: list[float] = []
    previous_at: float | None = None
    for event in events:
        try:
            at = float(event["at"])
            kind = event["type"]
            frame_index = int(event["frame"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"evented profile: bad event {event!r}") from exc
        if previous_at is not None and stack:
            weights.append(at - previous_at)
            stacks.append(tuple(stack))
        if kind == "O":
            stack.append(frame_index)
        elif kind == "C":
            if not stack or stack[-1] != frame_index:
                raise ValueError(f"evented profile: close does not match open ({event!r})")
            stack.pop()
        else:
            raise ValueError(f"evented profile: unknown event type {kind!r}")
        previous_at = at
    return stacks, weights


def _read_profile(profile: dict[str, Any]) -> tuple[list[tuple[int, ...]], list[float]]:
    profile_type = profile.get("type")
    if profile_type == "sampled":
        return _stacks_from_sampled(profile)
    if profile_type == "evented":
        return _stacks_from_evented(profile)
    raise ValueError(f"unsupported speedscope profile type: {profile_type!r}")


def parse_speedscope(text: str, profile_index: int | None = None) -> SpeedscopeProfile:
    """Parse a speedscope JSON document: every profile, or one on request.

    py-spy writes one profile per thread, and ``activeProfileIndex`` only
    says which one the viewer opens first, so by default every profile is
    read and :meth:`SpeedscopeProfile.top_functions` ranks over all threads.
    ``profile_index`` reads that one profile only. All profiles read together
    must share one unit, since their weights are added.

    Raises ``ValueError`` if ``text`` is not JSON, is not a speedscope
    document, has no profiles, mixes units, or uses a profile type other
    than "sampled" or "evented".
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not JSON: {exc}") from exc
    if not isinstance(doc, dict) or "shared" not in doc or "profiles" not in doc:
        raise ValueError("not a speedscope document: missing 'shared' or 'profiles'")

    frames = _frames_from_shared(doc["shared"])
    profiles = doc.get("profiles") or []
    if not profiles:
        raise ValueError("speedscope document has no profiles")
    if profile_index is not None:
        if not 0 <= profile_index < len(profiles):
            raise ValueError(
                f"profile index {profile_index} is out of range ({len(profiles)} profiles)"
            )
        profiles = [profiles[profile_index]]

    units = {p.get("unit", "") for p in profiles}
    if len(units) > 1:
        raise ValueError(f"profiles use different units {sorted(units)}; pass profile_index")
    types = {p.get("type") for p in profiles}
    stacks: list[tuple[int, ...]] = []
    weights: list[float] = []
    for profile in profiles:
        more_stacks, more_weights = _read_profile(profile)
        stacks += more_stacks
        weights += more_weights

    return SpeedscopeProfile(
        frames=frames,
        profile_type=str(types.pop()) if len(types) == 1 else "mixed",
        unit=units.pop(),
        stacks=stacks,
        weights=weights,
        profile_names=[str(p.get("name", "")) for p in profiles],
    )

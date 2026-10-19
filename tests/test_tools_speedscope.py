from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeprof.tools.speedscope import parse_speedscope


def test_sampled_top_functions(data: Path) -> None:
    text = (data / "tools" / "speedscope_sampled.json").read_text()
    profile = parse_speedscope(text)
    assert profile.profile_type == "sampled"
    top = profile.top_functions(3)
    by_name = {cost.name: cost for cost in top}
    assert [cost.name for cost in top] == ["main", "bar", "foo"]
    # A frame appearing twice in one stack ([0, 1, 1], foo calling itself)
    # counts once toward that sample's total, not twice.
    assert by_name["foo"].total_samples == 6
    assert by_name["foo"].self_samples == 3
    assert by_name["bar"].total_samples == 7
    assert by_name["bar"].self_samples == 7
    assert by_name["main"].total_samples == 10
    assert by_name["main"].self_samples == 0
    assert by_name["main"].total_share == pytest.approx(1.0)
    assert by_name["foo"].total_share == pytest.approx(0.6)
    assert by_name["bar"].file == "app.py"
    assert by_name["bar"].line == 20


def test_sampled_top_n_limits_results(data: Path) -> None:
    profile = parse_speedscope((data / "tools" / "speedscope_sampled.json").read_text())
    assert len(profile.top_functions(1)) == 1


def test_evented_reconstructs_stacks(data: Path) -> None:
    text = (data / "tools" / "speedscope_evented.json").read_text()
    profile = parse_speedscope(text)
    assert profile.profile_type == "evented"
    top = profile.top_functions(3)
    by_name = {cost.name: cost for cost in top}
    assert by_name["main"].total_samples == 6
    assert by_name["foo"].total_samples == 5
    assert by_name["bar"].total_samples == 2
    assert by_name["foo"].self_samples == 3
    assert by_name["bar"].self_samples == 2


def test_evented_mismatched_close_raises() -> None:
    doc = {
        "shared": {"frames": [{"name": "a"}, {"name": "b"}]},
        "profiles": [
            {
                "type": "evented",
                "events": [
                    {"type": "O", "frame": 0, "at": 0},
                    {"type": "C", "frame": 1, "at": 1},
                ],
            }
        ],
    }
    with pytest.raises(ValueError, match="close does not match open"):
        parse_speedscope(json.dumps(doc))


def test_not_json() -> None:
    with pytest.raises(ValueError, match="not JSON"):
        parse_speedscope("not json at all")


def test_not_speedscope_document() -> None:
    with pytest.raises(ValueError, match="not a speedscope document"):
        parse_speedscope(json.dumps({"foo": "bar"}))


def test_unsupported_profile_type() -> None:
    doc = {
        "shared": {"frames": []},
        "profiles": [{"type": "flamechart"}],
    }
    with pytest.raises(ValueError, match="unsupported speedscope profile type"):
        parse_speedscope(json.dumps(doc))


def test_no_profiles() -> None:
    doc = {"shared": {"frames": []}, "profiles": []}
    with pytest.raises(ValueError, match="no profiles"):
        parse_speedscope(json.dumps(doc))


def _two_thread_doc() -> str:
    # py-spy writes one profile per thread; hand-written from the schema.
    return json.dumps({
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "shared": {"frames": [{"name": "main"}, {"name": "worker"}, {"name": "io_wait"}]},
        "profiles": [
            {"type": "sampled", "name": "MainThread", "unit": "samples", "startValue": 0,
             "endValue": 2, "samples": [[0], [0]], "weights": [1, 1]},
            {"type": "sampled", "name": "Thread-1", "unit": "samples", "startValue": 0,
             "endValue": 8, "samples": [[1], [1, 2]], "weights": [5, 3]},
        ],
        "activeProfileIndex": 0,
        "exporter": "py-spy",
    })


def test_all_profiles_are_read_by_default() -> None:
    profile = parse_speedscope(_two_thread_doc())
    top = {cost.name: cost for cost in profile.top_functions(5)}
    # The worker thread holds 8 of the 10 samples; reading only the active
    # profile (the main thread) would miss it entirely.
    assert top["worker"].total_samples == 8
    assert top["io_wait"].self_samples == 3
    assert top["main"].total_share == pytest.approx(0.2)
    assert profile.profile_names == ["MainThread", "Thread-1"]


def test_one_profile_on_request() -> None:
    profile = parse_speedscope(_two_thread_doc(), profile_index=1)
    assert [cost.name for cost in profile.top_functions(5)][0] == "worker"
    assert profile.profile_names == ["Thread-1"]
    with pytest.raises(ValueError, match="out of range"):
        parse_speedscope(_two_thread_doc(), profile_index=5)

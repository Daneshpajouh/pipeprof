"""Draw docs/img/validation.svg from the held-out validation table in docs/results.md.

Usage: python3 scripts/plot_validation.py
Standard library only, so the figure can be rebuilt without the plotting stack.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs" / "results.md"
OUT = ROOT / "docs" / "img" / "validation.svg"

NUMBER = re.compile(r"[\d,]+(?:\.\d+)?")
METRIC_LABEL = {"wall": "wall time", "CPU": "CPU time", "peak RSS": "peak memory"}

LO, HI = 0.5, 4.0
LEFT, RIGHT, TOP, ROW = 210, 640, 70, 24
INSIDE, OUTSIDE = "#1b7837", "#c2410c"


@dataclass
class Row:
    run: str
    metric: str
    predicted: float
    low: float
    high: float
    actual: float
    inside: bool


def numbers(cell: str) -> list[float]:
    return [float(n.replace(",", "")) for n in NUMBER.findall(cell)]


def read_rows(text: str) -> list[Row]:
    rows: list[Row] = []
    run = ""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| Run | Metric |"))
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        name, metric, _model, predicted, interval, actual, _ratio, inside = cells
        # The first row of each run carries its label ("A sort, 1 to 20%"); the
        # others repeat only the letter.
        if not run or name.split(maxsplit=1)[0] != run.split(maxsplit=1)[0]:
            run = name
        low, high = numbers(interval)[:2]
        rows.append(
            Row(
                run,
                METRIC_LABEL[metric],
                numbers(predicted)[0],
                low,
                high,
                numbers(actual)[0],
                inside == "yes",
            )
        )
    return rows


def x_of(ratio: float) -> float:
    if not LO <= ratio <= HI:
        raise SystemExit(f"ratio {ratio:.3f} is outside the axis {LO} to {HI}; widen it")
    share = (math.log(ratio) - math.log(LO)) / (math.log(HI) - math.log(LO))
    return LEFT + share * (RIGHT - LEFT)


def svg(rows: list[Row]) -> str:
    height = TOP + ROW * len(rows) + 70
    inside = sum(r.inside for r in rows)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="680" height="{height}" '
        f'viewBox="0 0 680 {height}" font-family="Helvetica, Arial, sans-serif" font-size="12">',
        f"<title>pipeprof calibrate: {len(rows)} predictions checked against "
        f"{len({r.run for r in rows})} held-out full runs</title>",
        "<desc>Predicted wall time, CPU time and peak memory with their 95 percent "
        "intervals, divided by the value measured on the full run, for four "
        "calibrations on ClinVar and a 1000 Genomes BAM. "
        f"{inside} of {len(rows)} intervals contain the measured value.</desc>",
        f'<rect width="680" height="{height}" fill="#ffffff"/>',
        '<text x="20" y="24" font-size="14" font-weight="bold" fill="#111">'
        "Prediction divided by the measured full run (log scale)</text>",
        '<text x="20" y="44" fill="#444">Bar: 95% interval. Dot: point prediction. '
        "Line at 1: the measured value. Data: docs/results.md.</text>",
    ]
    bottom = TOP + ROW * len(rows)
    for tick in (0.5, 1.0, 2.0, 4.0):
        x = x_of(tick)
        colour = "#111" if tick == 1.0 else "#ddd"
        out.append(
            f'<line x1="{x:.1f}" y1="{TOP - 8}" x2="{x:.1f}" y2="{bottom}" stroke="{colour}"/>'
        )
        out.append(
            f'<text x="{x:.1f}" y="{bottom + 16}" text-anchor="middle" fill="#444">{tick:g}</text>'
        )
    last_run = ""
    for index, row in enumerate(rows):
        y = TOP + ROW * index + ROW / 2
        if row.run != last_run:
            last_run = row.run
            if index:
                out.append(
                    f'<line x1="20" y1="{y - ROW / 2:.1f}" x2="660" y2="{y - ROW / 2:.1f}" '
                    'stroke="#bbb" stroke-dasharray="2 3"/>'
                )
            out.append(f'<text x="20" y="{y + 4:.1f}" fill="#111">{last_run}</text>')
        out.append(
            f'<text x="{LEFT - 10}" y="{y + 4:.1f}" text-anchor="end" fill="#444">'
            f"{row.metric}</text>"
        )
        colour = INSIDE if row.inside else OUTSIDE
        x_low = x_of(row.low / row.actual)
        x_high = x_of(row.high / row.actual)
        x_point = x_of(row.predicted / row.actual)
        out.append(
            f'<line x1="{x_low:.1f}" y1="{y:.1f}" x2="{max(x_high, x_low + 2):.1f}" y2="{y:.1f}" '
            f'stroke="{colour}" stroke-width="6" stroke-opacity="0.45"/>'
        )
        out.append(f'<circle cx="{x_point:.1f}" cy="{y:.1f}" r="4" fill="{colour}"/>')
    legend = bottom + 40
    out += [
        f'<circle cx="26" cy="{legend - 4}" r="4" fill="{INSIDE}"/>',
        f'<text x="36" y="{legend}" fill="#111">interval contains the measured value '
        f"({inside} of {len(rows)})</text>",
        f'<circle cx="346" cy="{legend - 4}" r="4" fill="{OUTSIDE}"/>',
        f'<text x="356" y="{legend}" fill="#111">interval misses it '
        f"({len(rows) - inside} of {len(rows)})</text>",
        "</svg>",
    ]
    return "\n".join(out) + "\n"


def main() -> None:
    rows = read_rows(RESULTS.read_text(encoding="utf-8"))
    if len(rows) != 12:
        raise SystemExit(f"expected 12 table rows in {RESULTS}, found {len(rows)}")
    inside = sum(r.inside for r in rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(svg(rows), encoding="utf-8")
    print(f"wrote {OUT}: {len(rows)} predictions, {inside} inside their interval")


if __name__ == "__main__":
    main()

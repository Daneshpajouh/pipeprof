"""Write a seeded synthetic single-sample VCF with contigs of unequal size.

The sizes are chosen so the largest contig holds a quarter of the records,
which is the shape that makes per-contig fan-out uneven in real data.
"""

from __future__ import annotations

import argparse
import random
import sys

CONTIGS = {"c1": 25, "c2": 18, "c3": 14, "c4": 12, "c5": 10, "c6": 9, "c7": 7, "c8": 5}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", type=int, required=True, help="total records")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    out = sys.stdout
    out.write("##fileformat=VCFv4.2\n")
    for name, share in CONTIGS.items():
        out.write(f"##contig=<ID={name},length={share * 10_000_000}>\n")
    out.write('##INFO=<ID=DP,Number=1,Type=Integer,Description="Depth">\n')
    out.write('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n')
    out.write('##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">\n')
    out.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n")
    bases = "ACGT"
    total = sum(CONTIGS.values())
    for name, share in CONTIGS.items():
        n = args.records * share // total
        length = share * 10_000_000
        # Positions are drawn unsorted on purpose: the workflow has a sort step.
        positions = rng.sample(range(1, length), n)
        for pos in positions:
            ref = rng.choice(bases)
            alt = rng.choice([b for b in bases if b != ref])
            gt = rng.choice(["0/1", "1/1", "0/1", "0/0"])
            out.write(
                f"{name}\t{pos}\t.\t{ref}\t{alt}\t{rng.randint(10, 99)}\tPASS\t"
                f"DP={rng.randint(5, 80)}\tGT:GQ\t{gt}:{rng.randint(1, 99)}\n"
            )


if __name__ == "__main__":
    main()

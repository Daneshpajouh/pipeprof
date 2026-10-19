# Written by hjp calibrate on 2026-10-19 from the full ClinVar sort (docs/results.md, run A).
import math

def sort_vcf_mem_mb(wildcards, input, attempt):
    # linear fit; safety factor 1.224 covers the 95% upper bound
    # and headroom; each retry doubles the request.
    size_mb = max(input.size_mb, 1e-6)
    return max(256, math.ceil((7.01718 + 9.28544 * size_mb) * 1.2243) * 2 ** (attempt - 1))

def sort_vcf_runtime(wildcards, input, attempt):
    # linear fit in minutes; safety factor 1.643.
    size_mb = max(input.size_mb, 1e-6)
    return max(5, math.ceil((0.00223647 + 0.00197227 * size_mb) * 1.6427) * 2 ** (attempt - 1))


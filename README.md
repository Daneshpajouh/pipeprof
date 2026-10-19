# hpc-job-profiler

`hjp` measures how much memory, CPU and time a bioinformatics job needs, before,
during and after it runs on a Slurm cluster, and turns the measurements into
resource requests for Slurm, Nextflow and Snakemake.

## The problem

A cluster job has to say in advance how much memory and time it needs. Ask for
too little and the scheduler kills the job (out of memory or time limit), often
hours in. Ask for too much and the job waits longer in the queue, blocks other
users, and burns allocation that the lab pays for. Most requests are guesses
copied from an older script.

`hjp` replaces the guess with a measurement:

1. Before submission, `hjp calibrate` runs the command on 1, 2, 5, 10 and 20
   percent subsamples of the real input, fits how time and memory grow with
   input size, and predicts the full run with an uncertainty range.
2. During the run, `hjp watch` samples the job and warns when memory nears
   the limit, CPUs sit idle, or the job stops making progress.
3. After the run, `hjp report` reads Slurm accounting (`sacct`), Nextflow
   trace files and Snakemake benchmark files, shows what was used against what
   was requested, and recommends the next request.

A plain-language explanation of why this saves cluster time is in
[docs/why_profiling.md](docs/why_profiling.md).

## Quick start (about 5 minutes)

Needs Python 3.11 or newer. The calibration example also needs `bcftools`
and `tabix` (for example `brew install bcftools htslib` or
`apt-get install bcftools tabix`).

```bash
git clone https://github.com/Daneshpajouh/hpc-job-profiler
cd hpc-job-profiler
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

# 1. After: a report from the sacct test data shipped in the repository
hjp report --sacct tests/data/sacct_slurm_26_05.txt --outdir report
open report/report.html   # or xdg-open on Linux

# 2. Before: calibrate bcftools sort on the public ClinVar VCF (200 MB)
curl -O ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz
curl -O ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz.tbi
hjp calibrate clinvar.vcf.gz --outdir cal --job-name sort_vcf --validate-full -- \
    bcftools sort -m 512M -T '{workdir}/sorttmp' -Oz -o '{output}' '{input}'
cat cal/sbatch_header.sh cal/snakemake_resources.smk

# 3. During: watch a command and view the log
hjp watch --out watch.jsonl --interval 1 --mem-limit 512M --cpus 2 -- \
    bcftools sort -m 512M -T ./wsort -Oz -o sorted.vcf.gz clinvar.vcf.gz
hjp tail watch.jsonl --once
```

What step 2 printed on an Apple M3 Ultra (28 cores, 96 GiB, macOS 27.0.1) on 2026-10-19, for the
ClinVar release of 2026-10-11 (4,594,764 records):

```
metric          model   predicted   95% interval
wall_s          linear  22.75 s     20.59 s to 24.92 s
cpu_s           linear  21.26 s     21.07 s to 21.45 s
peak_rss_bytes  linear  1.7G        1.6G to 1.7G
Request: --cpus-per-task=1 --mem=3G --time=00:05:00
full run wall_s: predicted 22.75, actual 23.6, inside interval: True
full run cpu_s: predicted 21.26, actual 22.09, inside interval: False
full run peak_rss_bytes: predicted 1.782e+09, actual 4.873e+08, inside interval: False
```

Time was predicted within 4 percent. Memory was over-predicted 3.7 times:
`bcftools sort -m 512M` stops growing its buffer at 512 MB, and the largest
subsample (20 percent) never reached that cap, so the straight line kept going.
Calibrating with larger subsamples (5 to 50 percent) brought the error to
2.0 times. [docs/results.md](docs/results.md) has all four held-out runs,
including two where time was under-predicted, and how to choose fractions when a tool has a memory cap.

## What it reads and writes

| Command | Reads | Writes |
|---|---|---|
| `hjp calibrate INPUT -- CMD` | VCF (`.vcf`, `.vcf.gz`), BAM or CRAM (via samtools), FASTQ, line based text | `calibration.json`, `sbatch_header.sh`, `snakemake_resources.smk`, `snakemake_slurm_profile.yaml`, `nextflow_resources.config`, `nextflow_directives.nf`, `hjp.log.jsonl` |
| `hjp watch` | a local PID, a command to start, or a Slurm job ID (`sstat`) | JSON lines: samples and alerts |
| `hjp tail` | a watch log | live terminal view |
| `hjp report` | `sacct --parsable2` output (Slurm 20.11 and 21.08+ formats), Nextflow trace files (default and raw), Snakemake benchmark files and run log | `report.md`, `report.html`, `report.json` |
| `hjp provenance` | files to hash | JSON: host, tool versions, git commit, SHA-256 |

The logging library is importable on its own:

```python
from hpc_job_profiler.logkit import RunLogger

log = RunLogger("variant_qc", jsonl_path="logs/variant_qc.jsonl")
with log.step("bcftools_norm", sample="NA12878") as fields:
    fields["records_out"] = 412_093
log.close()
```

## Documentation

- [Why profiling saves cluster time](docs/why_profiling.md), for non-specialists
- [User guide](docs/user_guide.md)
- [SOP: profiling a new pipeline step](docs/sop_profile_new_step.md)
- [Scaling fit and its validation](docs/algorithm.md)
- [Results from real runs](docs/results.md)
- [Testing strategy](docs/testing.md)
- [Input formats and their sources](docs/formats.md)
- [Code walk-through for reviewers](docs/code_walkthrough.md)
- [Runbook for failed HPC jobs](docs/operations/runbook.md) and [postmortems](docs/operations/)
- [Data sources](docs/data_sources.md), [roadmap](ROADMAP.md), [changelog](CHANGELOG.md)

## Development

```bash
make dev      # venv with test and lint tools
make check    # ruff, ruff format --check, mypy --strict, pytest with coverage
make build    # sdist and wheel, then twine check
```

Licence: Apache-2.0. Author: Amir Daneshpajouh.

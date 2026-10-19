# Running pipeprof on a cluster

Notes from runs on Narval, a Digital Research Alliance of Canada cluster, on
2026-10-19. The install steps use the Alliance software stack and its
wheelhouse.

## Install

`pipeprof` is pure Python. Its dependencies (psutil, typer, rich) come from the
cluster's wheelhouse; `pipeprof` itself comes as a wheel built from this
repository on any machine:

```bash
# On any machine with the repository: build the pipeprof wheel, no dependencies.
python3 -m pip wheel . --no-deps -w dist
scp dist/pipeprof-*-py3-none-any.whl narval:~/wheels/

# On the cluster login node.
module load python            # Python 3.11 or newer
virtualenv --no-download ~/pipeprof-env
source ~/pipeprof-env/bin/activate
pip install --no-index --upgrade pip
pip install --no-index "psutil<7.2" typer rich
pip install --no-index --no-deps ~/wheels/pipeprof-*-py3-none-any.whl
pipeprof --version
```

Pin psutil below 7.2. With `psutil 7.2.2+computecanada` the Python process
aborted at exit after `children(recursive=True)` followed by `cpu_count()`.
`pipeprof calibrate` makes both: the process tree sampler lists children, and the
provenance record reads the CPU count. The same runs with psutil 7.0.0 were
clean.

## No /usr/bin/time on compute nodes

The compute nodes have no `/usr/bin/time`. `pipeprof` does not use it.
`pipeprof calibrate` takes CPU time and the kernel's peak RSS from `os.wait4`,
and where that is missing from `resource.getrusage(RUSAGE_CHILDREN)` read
before and after the wait (`measure.py`); memory of the whole process tree
comes from psutil sampling either way. `pipeprof watch` samples with psutil only.

`pipeprof usage gnu-time` needs a GNU `time -v` report, so it does not apply
there. For per-task readings on such nodes, run the task under
`pipeprof watch --out task.jsonl -- CMD` and read the logs with
`pipeprof report --watch-log`.

## Shared filesystems

On Lustre the same `bcftools sort` took 15.15 s on one node and 4.27 s on
another, with 3.56 s and 3.25 s of CPU. Wall time there depends on the
filesystem's load, not on the input, so `pipeprof calibrate` does not fit it when
a run is mostly waiting; see
[algorithm.md](algorithm.md#wall-time-on-a-waiting-filesystem). Calibrating
from `$SLURM_TMPDIR` (node-local disk) avoids the shared filesystem; that
only predicts the real job if it also reads from there.

## Measured overhead

On Narval (job 6413772, 2026-10-19), `pipeprof watch` added 0.29 s to a 4.3 s
job, and its highest sample, 176,578,560 bytes, was within 0.3 percent of the
kernel's rusage peak of 176,107,520 bytes.

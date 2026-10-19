"""Profile bioinformatics jobs before, during and after Slurm submission.

The package has four parts:

* ``calibrate``: run a command on graduated subsamples and predict resources
  for the full input (before submission).
* ``watch`` and ``tail``: sample a running process tree or Slurm job (during).
* ``report``: read sacct, Nextflow trace and Snakemake benchmark files and
  explain how well the requested resources were used (after).
* ``logkit`` and ``provenance``: structured JSON lines logging that pipelines
  can import too.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.1.0"

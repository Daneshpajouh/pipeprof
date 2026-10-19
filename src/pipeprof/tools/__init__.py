"""Readers for the output of standard profiling tools.

Each reader turns one tool's text into a ``StepUsage`` so the workflow analysis
can treat GNU time, BSD time, psrecord and seff output the same way. py-spy
speedscope profiles are function-level and have their own result type.
"""

from pipeprof.tools.bsd_time import parse_bsd_time
from pipeprof.tools.gnu_time import parse_gnu_time
from pipeprof.tools.psrecord import parse_psrecord, psrecord_samples
from pipeprof.tools.speedscope import FunctionCost, SpeedscopeProfile, parse_speedscope
from pipeprof.tools.usage import StepUsage

__all__ = [
    "FunctionCost",
    "SpeedscopeProfile",
    "StepUsage",
    "parse_bsd_time",
    "parse_gnu_time",
    "parse_psrecord",
    "parse_speedscope",
    "psrecord_samples",
]

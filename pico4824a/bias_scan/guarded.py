"""Compatibility entry point; both modes share the continuous bias runner."""
from .batched import finish_cycle, run_batches


def run_guarded(owner):
    return run_batches(owner)

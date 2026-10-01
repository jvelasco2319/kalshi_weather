"""Paid-depth, outcome-blind readiness layer for the KLAX V5 continuation."""

__all__ = [
    "PaidDepthError",
    "build_paid_execution_manifest",
    "build_paid_depth_readiness",
    "run_paid_depth_readiness",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    from . import paid_depth
    return getattr(paid_depth, name)

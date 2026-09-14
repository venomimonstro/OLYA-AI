from __future__ import annotations


def install_work_quality_floor_patch() -> None:
    """Compatibility shim.

    The router now owns three real user quality levels: fast/work/deep map to
    Simple/Medium/High. The former patch upgraded Fast to Work and would destroy
    the latency difference requested by the product UX, so it intentionally no
    longer rewrites routing decisions.
    """
    return

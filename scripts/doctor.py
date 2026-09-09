#!/usr/bin/env python3
"""Compatibility entrypoint for the current production doctor.

Keep this tiny wrapper stable because installer/operator documentation invokes
`scripts/doctor.py`. The auditable implementation lives in doctor_runtime.py.
"""

from scripts.doctor_runtime import main


if __name__ == "__main__":
    raise SystemExit(main())

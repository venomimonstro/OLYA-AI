from __future__ import annotations

"""Canonical ORM model registry.

The complete model implementation is stored losslessly in a gzip payload because
an earlier repository transfer could not carry this large source file through
the GitHub connector. Importing this module executes that exact source in this
module namespace, so existing ``from app.models import ...`` imports and
SQLAlchemy's shared registry keep normal semantics.
"""

import gzip
from pathlib import Path

_payload = Path(__file__).with_name("_models_impl.py.gz")
_source = gzip.decompress(_payload.read_bytes()).decode("utf-8")
exec(compile(_source, str(_payload.with_suffix("")), "exec"), globals(), globals())
del _source

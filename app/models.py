from __future__ import annotations

"""Canonical ORM model registry.

The base model implementation is stored losslessly in a gzip payload because an
earlier repository transfer could not carry the large source file. Sprint model
extensions are imported below into the same public registry so services never
see a partial ORM surface.
"""

import gzip
from pathlib import Path

_payload = Path(__file__).with_name("_models_impl.py.gz")
_source = gzip.decompress(_payload.read_bytes()).decode("utf-8")
exec(compile(_source, str(_payload.with_suffix("")), "exec"), globals(), globals())
del _source

# Canonical extension registry. These modules share app.db.Base and therefore
# become part of the same SQLAlchemy metadata used by tests/migrations/runtime.
from app.models_sprint27 import *  # noqa: F401,F403,E402
from app.models_sprint29 import *  # noqa: F401,F403,E402
from app.models_sprint30 import *  # noqa: F401,F403,E402
from app.models_sprint31 import *  # noqa: F401,F403,E402

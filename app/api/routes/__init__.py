"""API route package bootstrap.

Runtime chat quality guards are installed before individual route modules import
and bind inference functions. Keeping this at the package boundary makes the
policy apply consistently to normal chat, streaming chat and verification calls.
"""

from app.runtime_quality_patch import install_runtime_quality_patch

install_runtime_quality_patch()

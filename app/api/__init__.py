"""API package bootstrap.

Install cross-cutting HTML surface guards before route modules are imported so
admin pages share one navigation shell and product pages receive the current
OLYA AI UX refresh consistently.
"""

from app.admin_surface_patch import install_admin_surface_patch
from app.product_ui_refresh import install_product_ui_refresh
from app.user_surface_finalizer import install_user_surface_finalizer
from app.workspace_client_v3 import install_workspace_client_v3
from app.task_solver_access_patch import install_task_solver_access_patch
from app.source_metadata_patch import install_source_metadata_patch

install_admin_surface_patch()
install_product_ui_refresh()
install_user_surface_finalizer()
install_workspace_client_v3()
install_task_solver_access_patch()
install_source_metadata_patch()

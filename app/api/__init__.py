"""API package bootstrap.

Install cross-cutting HTML surface guards before route modules are imported so
admin pages share one navigation shell and product pages receive the current
OLYA AI UX refresh consistently.
"""

from app.admin_surface_patch import install_admin_surface_patch
from app.product_ui_refresh import install_product_ui_refresh

install_admin_surface_patch()
install_product_ui_refresh()

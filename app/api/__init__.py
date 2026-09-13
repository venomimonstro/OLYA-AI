"""API package bootstrap.

Admin UI routes are defined across several modules. Install the shared admin
surface patch before any of those routers are imported so every admin page gets
the same protected, stable navigation shell.
"""

from app.admin_surface_patch import install_admin_surface_patch

install_admin_surface_patch()

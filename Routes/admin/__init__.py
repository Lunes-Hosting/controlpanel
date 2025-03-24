"""
Admin Routes Package
=================

This package contains all administrative routes and functionality for the control panel.
It provides interfaces for user management, server administration, and support ticket handling.

Modules:
-------
- users.py: User management routes
- servers.py: Server management routes
- tickets.py: Support ticket management routes
- nodes.py: Node management routes
- dashboard.py: Admin dashboard routes
- activity_logs.py: Activity logs management routes

Access Control:
-------------
All routes are protected by admin_required verification
"""

from flask import Blueprint

# Create a blueprint for the admin routes
admin = Blueprint('admin', __name__)

# Import all admin route modules
from Routes.admin.dashboard import *
from Routes.admin.users import *
from Routes.admin.servers import *
from Routes.admin.tickets import *
from Routes.admin.nodes import *
from Routes.admin.activity_logs import *

# All routes are registered to the admin blueprint in their respective modules

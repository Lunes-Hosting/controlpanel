"""
Admin Dashboard Routes
=================

This module handles the admin dashboard routes for the control panel.

Templates Used:
-------------
- admin/admin.html: Admin dashboard homepage

Session Requirements:
------------------
All routes require:
- email: User's email address
- Admin status verification

Access Control:
-------------
All routes are protected by admin_required verification
"""

from flask import render_template, session
from Routes.admin import admin
from scripts import admin_required

@admin.route("/")
@admin_required
def admin_index():
    """
    Display admin dashboard homepage.
    
    Session Requirements:
        - email: User must be logged in
        
    Access Control:
        - User must be admin
        
    Templates:
        - admin/admin.html: Main admin dashboard
        
    Database Queries:
        - Verify admin status
        
    Returns:
        template: admin/admin.html
        str: Error message if not admin
        
    Related Functions:
        - is_admin(): Verifies admin privileges
    """
    return render_template("admin/admin.html")

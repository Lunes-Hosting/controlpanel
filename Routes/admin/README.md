# Admin Routes Structure

This folder contains all administrative routes and functionality for the control panel, organized into separate modules for better maintainability.

## Module Structure

- `__init__.py`: Initializes the admin blueprint and imports all route modules
- `dashboard.py`: Admin dashboard routes
- `users.py`: User management routes
- `servers.py`: Server management routes
- `tickets.py`: Support ticket management routes
- `nodes.py`: Node management routes

## Access Control

All routes are protected by `admin_required` verification to ensure that only authorized users can access admin functionalities.

## Usage

The admin blueprint is registered in the main app.py file with the URL prefix '/admin'.

```python
from Routes.admin import admin
app.register_blueprint(admin, url_prefix="/admin")
```

## Templates

All admin templates are located in the `/templates/admin/` directory.

"""
Managers Package
==============

This package contains specialized manager classes that handle different aspects of the application.
Each manager is responsible for a specific domain of functionality, following the Single Responsibility Principle.

Key Managers:
- BaseManager: Common functionality for all managers
- DatabaseManager: Handles database operations
- UserManager: Handles user-related operations
- ServerManager: Handles server-related operations
- CreditManager: Handles credit-related operations
- EmailManager: Handles email-related operations
- Authentication: Handles login and registration
- Maintenance: Handles scheduled tasks
- Logging: Handles webhook logging
- Utils: Provides utility functions
"""

from .base_manager import BaseManager
from .database_manager import DatabaseManager

# Import new modules
from .user_manager import *
from .server_manager import *
from .credit_manager import *
from .email_manager import *
from .authentication import *
from .maintenance import *
from .logging import *
from .utils import *

# Export constants
from .utils import HEADERS

__all__ = [
    'BaseManager', 
    'DatabaseManager',
    # User Manager
    'get_ptero_id', 
    'get_id', 
    'get_name', 
    'account_get_information',
    'update_ip', 
    'update_last_seen', 
    'get_last_seen', 
    'is_admin',
    'check_if_user_suspended', 
    'get_user_verification_status_and_suspension_status',
    'instantly_delete_user',
    # Server Manager
    'get_nodes', 
    'get_eggs', 
    'get_autodeploy_info', 
    'improve_list_servers',
    'get_server_information', 
    'suspend_server', 
    'unsuspend_server',
    'get_node_allocation', 
    'transfer_server',
    # Credit Manager
    'add_credits', 
    'remove_credits', 
    'get_credits', 
    'convert_to_product',
    'use_credits', 
    'check_to_unsuspend',
    # Email Manager
    'send_email', 
    'generate_verification_token', 
    'send_verification_email',
    'generate_reset_token', 
    'send_reset_email',
    # Authentication
    'login', 
    'register',
    'login_required',
    'admin_required',
    # Maintenance
    'sync_users_script',
    # Logging
    'webhook_log',
    # Utils
    'get_db_connection',
    # Constants
    'HEADERS'
]

"""
Core functionality module for the Pterodactyl Control Panel.
This module serves as the central interface between the web application and both the Pterodactyl API 
and the local database. It handles all core operations including user management, server provisioning,
credit system, and automated maintenance tasks.

Architecture Overview:
    - Web Layer: Flask routes interface with this module for all core operations
    - Database Layer: MySQL database stores user data, credits, and application state
    - API Layer: Communicates with Pterodactyl API for server management
    - Cache Layer: Uses PteroCache for optimizing API calls
    - Task Layer: Runs scheduled tasks for credit processing and server maintenance

Key Components:
    1. User Management:
        - Registration/Authentication
        - Credit system
        - Role management (admin/client)
        - Email verification
        - Password reset
    
    2. Server Management:
        - Server provisioning
        - Suspension/Unsuspension
        - Resource allocation
        - Node management
        - Server transfers
    
    3. Credit System:
        - Credit tracking
        - Automated usage calculation
        - Payment processing integration
    
    4. Security:
        - Password hashing (bcrypt)
        - Session management
        - IP tracking
        - Admin access control

Database Schema:
    users table:
        - id: int (primary key)
        - name: str
        - email: str (unique)
        - password: str (bcrypt hashed)
        - pterodactyl_id: int
        - credits: int
        - role: str
        - ip: str
        - email_verified_at: datetime
        - last_seen: datetime

Dependencies:
    - flask: Web framework
    - mysql-connector: Database interface
    - bcrypt: Password hashing
    - requests: HTTP client for API calls
    - flask_mail: Email functionality
    - pterocache: Custom caching layer

Environment Variables Required:
    - PTERODACTYL_URL: Base URL for Pterodactyl panel
    - PTERODACTYL_ADMIN_KEY: Admin API key
    - DATABASE: MySQL database name
    - MAIL_* settings for email configuration

Constants:
    HEADERS: API headers for admin access
"""
"""
DEPRECATION NOTICE:
==================
This file is being deprecated in favor of the modular structure in the 'managers' package.
All functions in this file will be removed in a future version.
Please update your imports to use the appropriate modules from the managers package.

Example:
  Old: from scripts import login
  New: from managers.authentication import login
"""
import datetime
import time
import string
import threading
import sys
from pterocache import PteroCache
import bcrypt
import mysql.connector
# Establish a connection to the database
import mysql.connector
import requests
from flask import url_for, redirect, current_app, Flask, request, session, render_template
from functools import wraps

import logging
from werkzeug.datastructures.headers import EnvironHeaders
from managers.database_manager import DatabaseManager
from threadedreturn import ThreadWithReturnValue
from config import *
from products import products
import secrets
import random
import json
from flask_mail import Mail, Message
import inspect
import warnings


cache = PteroCache()

# API authentication headers
HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

# Create a decorator for deprecation warnings
def deprecated(new_location):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get the caller's filename and line number
            caller_frame = inspect.currentframe().f_back
            caller_filename = caller_frame.f_code.co_filename
            caller_lineno = caller_frame.f_lineno
            
            # Log the deprecation warning with caller information
            warnings.warn(
                f"DEPRECATION WARNING: Function '{func.__name__}' called from {caller_filename}:{caller_lineno} "
                f"is deprecated and will be removed in a future version. "
                f"Please use 'from {new_location} import {func.__name__}' instead.",
                DeprecationWarning, stacklevel=2
            )
            
            # Log to webhook if available
            try:
                threading.Thread(
                    target=webhook_log,
                    args=(f"Deprecated function '{func.__name__}' called from {caller_filename}:{caller_lineno}", 1)
                ).start()
            except:
                pass
                
            return func(*args, **kwargs)
        return wrapper
    return decorator

@deprecated("managers.authentication")
def login_required(f):
    from managers.authentication import login_required as new_login_required
    return new_login_required(f)

@deprecated("managers.authentication")
def admin_required(f):
    from managers.authentication import admin_required as new_admin_required
    return new_admin_required(f)

@deprecated("managers.maintenance")
def sync_users_script():
    from managers.maintenance import sync_users_script as new_sync_users_script
    return new_sync_users_script()

@deprecated("managers.user_manager")
def get_ptero_id(email: str):
    from managers.user_manager import get_ptero_id as new_get_ptero_id
    return new_get_ptero_id(email)

@deprecated("managers.user_manager")
def get_id(email: str):
    from managers.user_manager import get_id as new_get_id
    return new_get_id(email)

@deprecated("managers.user_manager")
def get_name(user_id: int):
    from managers.user_manager import get_name as new_get_name
    return new_get_name(user_id)

@deprecated("managers.user_manager")
def account_get_information(email: str):
    from managers.user_manager import account_get_information as new_account_get_information
    return new_account_get_information(email)

@deprecated("managers.user_manager")
def update_ip(email: str, real_ip: str):
    from managers.user_manager import update_ip as new_update_ip
    return new_update_ip(email, real_ip)

@deprecated("managers.user_manager")
def update_last_seen(email: str, everyone: bool = False):
    from managers.user_manager import update_last_seen as new_update_last_seen
    return new_update_last_seen(email, everyone)

@deprecated("managers.user_manager")
def get_last_seen(email: str):
    from managers.user_manager import get_last_seen as new_get_last_seen
    return new_get_last_seen(email)

@deprecated("managers.user_manager")
def is_admin(email: str):
    from managers.user_manager import is_admin as new_is_admin
    return new_is_admin(email)

@deprecated("managers.user_manager")
def check_if_user_suspended(pterodactyl_id: str):
    from managers.user_manager import check_if_user_suspended as new_check_if_user_suspended
    return new_check_if_user_suspended(pterodactyl_id)

@deprecated("managers.user_manager")
def get_user_verification_status_and_suspension_status(email):
    from managers.user_manager import get_user_verification_status_and_suspension_status as new_get_user_verification_status_and_suspension_status
    return new_get_user_verification_status_and_suspension_status(email)

@deprecated("managers.user_manager")
def instantly_delete_user(email: str, skip_email: bool = False):
    from managers.user_manager import instantly_delete_user as new_instantly_delete_user
    return new_instantly_delete_user(email, skip_email)




@deprecated("managers.server_manager")
def get_nodes(all: bool = False):
    from managers.server_manager import get_nodes as new_get_nodes
    return new_get_nodes(all)

@deprecated("managers.server_manager")
def get_eggs():
    from managers.server_manager import get_eggs as new_get_eggs
    return new_get_eggs()

@deprecated("managers.server_manager")
def get_autodeploy_info(project_id: int):
    from managers.server_manager import get_autodeploy_info as new_get_autodeploy_info
    return new_get_autodeploy_info(project_id)

@deprecated("managers.server_manager")
def improve_list_servers(pterodactyl_id: int = None):
    from managers.server_manager import improve_list_servers as new_improve_list_servers
    return new_improve_list_servers(pterodactyl_id)

@deprecated("managers.server_manager")
def get_server_information(server_id: int):
    from managers.server_manager import get_server_information as new_get_server_information
    return new_get_server_information(server_id)

@deprecated("managers.server_manager")
def suspend_server(server_id: int):
    from managers.server_manager import suspend_server as new_suspend_server
    return new_suspend_server(server_id)

@deprecated("managers.server_manager")
def unsuspend_server(server_id: int):
    from managers.server_manager import unsuspend_server as new_unsuspend_server
    return new_unsuspend_server(server_id)

@deprecated("managers.server_manager")
def get_node_allocation(node_id: int):
    from managers.server_manager import get_node_allocation as new_get_node_allocation
    return new_get_node_allocation(node_id)

@deprecated("managers.server_manager")
def transfer_server(server_id: int, target_node_id: int):
    from managers.server_manager import transfer_server as new_transfer_server
    return new_transfer_server(server_id, target_node_id)


@deprecated("managers.credit_manager")
def add_credits(email: str, amount: int, set_client: bool = True):
    from managers.credit_manager import add_credits as new_add_credits
    return new_add_credits(email, amount, set_client)

@deprecated("managers.credit_manager")
def remove_credits(email: str, amount: float):
    from managers.credit_manager import remove_credits as new_remove_credits
    return new_remove_credits(email, amount)

@deprecated("managers.credit_manager")
def get_credits(email: str):
    from managers.credit_manager import get_credits as new_get_credits
    return new_get_credits(email)

@deprecated("managers.credit_manager")
def convert_to_product(data):
    from managers.credit_manager import convert_to_product as new_convert_to_product
    return new_convert_to_product(data)

@deprecated("managers.credit_manager")
def use_credits():
    from managers.credit_manager import use_credits as new_use_credits
    return new_use_credits()

@deprecated("managers.credit_manager")
def check_to_unsuspend():
    from managers.credit_manager import check_to_unsuspend as new_check_to_unsuspend
    return new_check_to_unsuspend()


@deprecated("managers.authentication")
def login(email: str, password: str, ip: str):
    from managers.authentication import login as new_login
    return new_login(email, password, ip)

@deprecated("managers.authentication")
def register(email: str, password: str, name: str, ip: str):
    from managers.authentication import register as new_register
    return new_register(email, password, name, ip)

@deprecated("managers.email_manager")
def send_email(email: str, title: str, message: str, inner_app):
    from managers.email_manager import send_email as new_send_email
    return new_send_email(email, title, message, inner_app)

@deprecated("managers.email_manager")
def generate_verification_token():
    from managers.email_manager import generate_verification_token as new_generate_verification_token
    return new_generate_verification_token()

@deprecated("managers.email_manager")
def send_verification_email(email: str, verification_token: str, inner_app):
    from managers.email_manager import send_verification_email as new_send_verification_email
    return new_send_verification_email(email, verification_token, inner_app)

@deprecated("managers.email_manager")
def generate_reset_token():
    from managers.email_manager import generate_reset_token as new_generate_reset_token
    return new_generate_reset_token()

@deprecated("managers.email_manager")
def send_reset_email(email: str, reset_token: str, inner_app):
    from managers.email_manager import send_reset_email as new_send_reset_email
    return new_send_reset_email(email, reset_token, inner_app)

@deprecated("managers.maintenance")
def sync_users_script():
    from managers.maintenance import sync_users_script as new_sync_users_script
    return new_sync_users_script()

@deprecated("managers.logging")
def webhook_log(embed_message: str, status: int = -1, non_embed_message: str = None):
    from managers.logging import webhook_log as new_webhook_log
    return new_webhook_log(embed_message, status, non_embed_message)

@deprecated("managers.utils")
def get_db_connection(database=DATABASE):
    from managers.utils import get_db_connection as new_get_db_connection
    return new_get_db_connection(database)

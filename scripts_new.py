"""
Core functionality module for the Pterodactyl Control Panel.
This module serves as the central interface between the web application and both the Pterodactyl API 
and the local database. It handles all core operations including user management, server provisioning,
credit system, and automated maintenance tasks.

IMPORTANT: This file is now a compatibility layer that imports from the modular managers package.
For new development, please use the appropriate modules directly from the managers package.

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
"""

# Import all functions from the managers package
from managers import *

# Import any other dependencies needed for backward compatibility
import datetime
import time
import string
import threading
import sys
from pterocache import PteroCache
import bcrypt
import mysql.connector
import requests
from flask import url_for, redirect, current_app, Flask, request, session, render_template
from functools import wraps
import logging
from werkzeug.datastructures.headers import EnvironHeaders
from threadedreturn import ThreadWithReturnValue
from config import *
from products import products
import secrets
import random
import json
from flask_mail import Mail, Message

# Initialize cache for backward compatibility
cache = PteroCache()

# Create login_required and admin_required decorators for backward compatibility
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'email' not in session:
            return redirect(url_for('login'))
        
        if not is_admin(session['email']):
            return "You are not authorized to access this page."
        
        return f(*args, **kwargs)
    return decorated_function

# Re-export admin_required and login_required for backward compatibility
__all__ = [
    'login_required',
    'admin_required',
    # All other functions are imported from managers package
]

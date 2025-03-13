"""
DEPRECATION TEMPLATE
===================

This file demonstrates how to add deprecation warnings to functions in scripts.py.
Follow this pattern for all functions in the original file.

Steps to implement deprecation warnings:
1. Add the imports at the top of scripts.py:
   - import inspect
   - import warnings

2. Add the 'deprecated' decorator function shown below

3. Apply the @deprecated decorator to each function, specifying the new location

4. Update the function body to import and call the new implementation

5. Add a clear deprecation notice at the top of the file
"""

import inspect
import warnings
from functools import wraps
import threading

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

# Example of how to apply the decorator to a function:

@deprecated("managers.user_manager")
def get_ptero_id(email: str):
    """
    Gets Pterodactyl ID for a user by their email.
    
    Args:
        email: User's email address
    
    Returns:
        tuple[int]: Tuple containing Pterodactyl ID at index 0
        None: If user not found
    """
    # Import the new implementation
    from managers.user_manager import get_ptero_id as new_get_ptero_id
    # Call and return the result from the new implementation
    return new_get_ptero_id(email)

@deprecated("managers.server_manager")
def get_nodes(all: bool = False):
    """
    Returns cached list of available nodes from Pterodactyl.
    
    Returns:
        list[dict]: List of node information
    """
    from managers.server_manager import get_nodes as new_get_nodes
    return new_get_nodes(all)

@deprecated("managers.credit_manager")
def add_credits(email: str, amount: int, set_client: bool = True):
    """
    Adds credits to a user's account.
    
    Args:
        email: User's email
        amount: Number of credits to add
        set_client: Whether to set user role to 'client'
    
    Returns:
        None
    """
    from managers.credit_manager import add_credits as new_add_credits
    return new_add_credits(email, amount, set_client)

# Add the following notice at the top of scripts.py:
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

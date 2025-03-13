"""
Utilities Module
=================

This module provides general utility functions and constants used across the application.
It serves as a central location for common operations and configuration.
"""

import mysql.connector
from config import DATABASE, HOST, USER, PASSWORD, PTERODACTYL_ADMIN_KEY

# API authentication headers
HEADERS = {
    "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

def get_db_connection(database=DATABASE):
    """
    Creates and returns a database connection with standard configuration.
    
    Args:
        database: Database name to connect to
        
    Returns:
        tuple: (connection, cursor)
    """
    connection = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=database
    )
    cursor = connection.cursor()
    return connection, cursor

# Add any other utility functions or constants here that are used across multiple modules

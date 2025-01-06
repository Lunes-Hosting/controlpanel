"""
Managers Package
==============

This package contains specialized manager classes that handle different aspects of the application.
Each manager is responsible for a specific domain of functionality, following the Single Responsibility Principle.

Key Managers:
- BaseManager: Common functionality for all managers
- DatabaseManager: Handles database operations
"""

from .base_manager import BaseManager
from .database_manager import DatabaseManager

__all__ = ['BaseManager', 'DatabaseManager']

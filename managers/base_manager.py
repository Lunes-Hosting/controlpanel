"""
Base Manager Module
================

This module provides the BaseManager class that all other managers inherit from.
It contains common functionality and utilities used across different managers.
"""

import requests
from typing import Optional, Any, Dict
from config import PTERODACTYL_URL, PTERODACTYL_ADMIN_KEY

class BaseManager:
    """
    Base manager class providing common functionality for all managers.
    
    Attributes:
        HEADERS (Dict[str, str]): Default headers for API requests
        
    Methods:
        make_request: Makes HTTP requests with proper error handling
    """
    
    HEADERS = {
        "Authorization": f"Bearer {PTERODACTYL_ADMIN_KEY}",
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
    
    @classmethod
    def make_request(
        cls,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Any:
        """
        Makes an HTTP request with proper error handling.
        
        Args:
            method: HTTP method (GET, POST, etc)
            endpoint: API endpoint to call
            data: Request body data
            params: URL parameters
            
        Returns:
            Response data if successful
            
        Raises:
            Exception: If request fails
        """
        url = f"{PTERODACTYL_URL}{endpoint}"
        
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=cls.HEADERS,
                json=data,
                params=params
            )
            response.raise_for_status()
            return response.json() if response.text else None
            
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            raise

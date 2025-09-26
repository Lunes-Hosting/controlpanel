"""
Announcement Manager Module
==========================

This module provides the AnnouncementManager class that handles all announcement operations.
It manages creating, updating, and retrieving system announcements for the control panel.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from .database_manager import DatabaseManager
from .base_manager import BaseManager

class AnnouncementManager(BaseManager):
    """
    Manages announcement operations including creation, updates, and retrieval.
    Handles both active and inactive announcements with date-based filtering.
    """
    
    @classmethod
    def create_announcement(
        cls,
        title: str,
        message: str,
        created_by: str,
        announcement_type: str = 'info',
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        auto_end: bool = False,
        priority: int = 1
    ) -> int:
        """
        Create a new announcement.
        
        Args:
            title: Announcement title
            message: Announcement content
            created_by: Email of admin creating the announcement
            announcement_type: Type of announcement (info, warning, success, error)
            start_date: When to start showing (None = immediate)
            end_date: When to stop showing (None = manual end only)
            auto_end: Whether to auto-end based on end_date
            priority: Display priority (higher = more important)
            
        Returns:
            int: ID of created announcement
            
        Raises:
            Exception: If database error occurs
        """
        query = """
        INSERT INTO announcements 
        (title, message, type, created_by, start_date, end_date, auto_end, priority)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        values = (
            title, message, announcement_type, created_by,
            start_date, end_date, auto_end, priority
        )
        
        connection, cursor = DatabaseManager.get_connection()
        try:
            cursor.execute(query, values)
            connection.commit()
            return cursor.lastrowid
        finally:
            cursor.close()
            connection.close()
    
    @classmethod
    def get_active_announcements(cls) -> List[Dict[str, Any]]:
        """
        Get all currently active announcements that should be displayed to users.
        
        Returns:
            List[Dict]: List of active announcements with all fields
            
        Raises:
            Exception: If database error occurs
        """
        query = """
        SELECT id, title, message, type, created_by, created_at, 
               start_date, end_date, auto_end, priority
        FROM announcements 
        WHERE is_active = 1 
        AND (start_date IS NULL OR start_date <= NOW())
        AND (end_date IS NULL OR end_date > NOW())
        ORDER BY priority DESC, created_at DESC
        """
        
        result = DatabaseManager.execute_query(query, fetch_all=True)
        
        if not result:
            return []
        
        announcements = []
        for row in result:
            announcements.append({
                'id': row[0],
                'title': row[1],
                'message': row[2],
                'type': row[3],
                'created_by': row[4],
                'created_at': row[5],
                'start_date': row[6],
                'end_date': row[7],
                'auto_end': bool(row[8]),
                'priority': row[9]
            })
        
        return announcements
    
    @classmethod
    def get_all_announcements(cls) -> List[Dict[str, Any]]:
        """
        Get all announcements (active and inactive) for admin management.
        
        Returns:
            List[Dict]: List of all announcements
            
        Raises:
            Exception: If database error occurs
        """
        query = """
        SELECT id, title, message, type, is_active, created_by, created_at,
               start_date, end_date, auto_end, priority
        FROM announcements 
        ORDER BY created_at DESC
        """
        
        result = DatabaseManager.execute_query(query, fetch_all=True)
        
        if not result:
            return []
        
        announcements = []
        for row in result:
            announcements.append({
                'id': row[0],
                'title': row[1],
                'message': row[2],
                'type': row[3],
                'is_active': bool(row[4]),
                'created_by': row[5],
                'created_at': row[6],
                'start_date': row[7],
                'end_date': row[8],
                'auto_end': bool(row[9]),
                'priority': row[10]
            })
        
        return announcements
    
    @classmethod
    def update_announcement(
        cls,
        announcement_id: int,
        title: Optional[str] = None,
        message: Optional[str] = None,
        announcement_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        auto_end: Optional[bool] = None,
        priority: Optional[int] = None
    ) -> bool:
        """
        Update an existing announcement.
        
        Args:
            announcement_id: ID of announcement to update
            title: New title (optional)
            message: New message (optional)
            announcement_type: New type (optional)
            is_active: New active status (optional)
            start_date: New start date (optional)
            end_date: New end date (optional)
            auto_end: New auto-end setting (optional)
            priority: New priority (optional)
            
        Returns:
            bool: True if update successful, False otherwise
            
        Raises:
            Exception: If database error occurs
        """
        # Build dynamic update query
        updates = []
        values = []
        
        if title is not None:
            updates.append("title = %s")
            values.append(title)
        
        if message is not None:
            updates.append("message = %s")
            values.append(message)
        
        if announcement_type is not None:
            updates.append("type = %s")
            values.append(announcement_type)
        
        if is_active is not None:
            updates.append("is_active = %s")
            values.append(is_active)
        
        if start_date is not None:
            updates.append("start_date = %s")
            values.append(start_date)
        
        if end_date is not None:
            updates.append("end_date = %s")
            values.append(end_date)
        
        if auto_end is not None:
            updates.append("auto_end = %s")
            values.append(auto_end)
        
        if priority is not None:
            updates.append("priority = %s")
            values.append(priority)
        
        if not updates:
            return True  # No updates to make
        
        query = f"UPDATE announcements SET {', '.join(updates)} WHERE id = %s"
        values.append(announcement_id)
        
        try:
            DatabaseManager.execute_query(query, tuple(values))
            return True
        except Exception:
            return False
    
    @classmethod
    def delete_announcement(cls, announcement_id: int) -> bool:
        """
        Delete an announcement.
        
        Args:
            announcement_id: ID of announcement to delete
            
        Returns:
            bool: True if deletion successful, False otherwise
            
        Raises:
            Exception: If database error occurs
        """
        query = "DELETE FROM announcements WHERE id = %s"
        
        try:
            DatabaseManager.execute_query(query, (announcement_id,))
            return True
        except Exception:
            return False
    
    @classmethod
    def end_announcement(cls, announcement_id: int) -> bool:
        """
        Manually end an announcement by setting is_active to False.
        
        Args:
            announcement_id: ID of announcement to end
            
        Returns:
            bool: True if update successful, False otherwise
            
        Raises:
            Exception: If database error occurs
        """
        return cls.update_announcement(announcement_id, is_active=False)
    
    @classmethod
    def get_announcement_by_id(cls, announcement_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific announcement by ID.
        
        Args:
            announcement_id: ID of announcement to retrieve
            
        Returns:
            Dict: Announcement data or None if not found
            
        Raises:
            Exception: If database error occurs
        """
        query = """
        SELECT id, title, message, type, is_active, created_by, created_at,
               start_date, end_date, auto_end, priority
        FROM announcements 
        WHERE id = %s
        """
        
        result = DatabaseManager.execute_query(query, (announcement_id,))
        
        if not result:
            return None
        
        return {
            'id': result[0],
            'title': result[1],
            'message': result[2],
            'type': result[3],
            'is_active': bool(result[4]),
            'created_by': result[5],
            'created_at': result[6],
            'start_date': result[7],
            'end_date': result[8],
            'auto_end': bool(result[9]),
            'priority': result[10]
        }

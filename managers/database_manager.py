"""
Database Manager Module
===================

This module provides the DatabaseManager class that handles all database operations.
It encapsulates database connection and query execution while maintaining compatibility
with existing codebase functionality.
"""

import mysql.connector
from typing import Optional, Union, Tuple, List, Any
from .base_manager import BaseManager
from config import DATABASE, HOST, PASSWORD, USER

class DatabaseManager(BaseManager):
    """
    Manages database operations including connections and query execution.
    Designed to be compatible with existing database usage patterns.
    """
    
    @classmethod
    def get_connection(cls, database: str = DATABASE) -> Tuple[mysql.connector.MySQLConnection, mysql.connector.cursor.MySQLCursor]:
        """
        Creates and returns a database connection with standard configuration.
        Direct replacement for get_db_connection.
        
        Args:
            database: Database name to connect to
            
        Returns:
            tuple: (connection, cursor)
        """
        connection = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database=database,
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        cursor = connection.cursor(buffered=True)
        return connection, cursor

    @classmethod
    def execute_query(
        cls,
        query: str,
        values: Optional[tuple] = None,
        database: str = DATABASE,
        fetch_all: bool = False
    ) -> Optional[Union[Tuple, List[Tuple]]]:
        """
        Executes a database query. For SELECT queries returns results, otherwise returns None.
        Direct replacement for use_database.
        
        Args:
            query: SQL query to execute
            values: Query parameter values
            database: Target database name
            fetch_all: Whether to return all results for SELECT queries
            
        Returns:
            tuple: First row for SELECT queries when fetch_all=False
            list[tuple]: All rows for SELECT queries when fetch_all=True
            None: For non-SELECT queries
            
        Raises:
            Exception: If database error occurs
        """
        connection = None
        cursor = None
        try:
            connection, cursor = cls.get_connection(database)
            
            if values:
                cursor.execute(query, values)
            else:
                cursor.execute(query)
                
            if query.strip().upper().startswith('SELECT'):
                if fetch_all:
                    result = cursor.fetchall()
                else:
                    result = cursor.fetchone()
                return result
                
            connection.commit()
            return None
            
        except Exception as e:
            if connection:
                connection.rollback()
            print(f"Database error: {e}")
            raise
            
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()
                
    @classmethod
    def execute_many(
        cls,
        query: str,
        values: List[tuple],
        database: str = DATABASE
    ) -> None:
        """
        Executes a query with multiple sets of values.
        
        Args:
            query: SQL query to execute
            values: List of value tuples
            database: Target database name
            
        Raises:
            Exception: If database error occurs
        """
        connection = None
        cursor = None
        try:
            connection, cursor = cls.get_connection(database)
            cursor.executemany(query, values)
            connection.commit()
            
        except Exception as e:
            if connection:
                connection.rollback()
            print(f"Database error: {e}")
            raise
            
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

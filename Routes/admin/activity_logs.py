"""
Admin Activity Logs Routes
=================

This module handles the admin activity logs routes for the control panel.

Templates Used:
-------------
- admin/activity_logs.html: Activity logs management interface
- admin/log_details.html: Detailed log view

Database Tables Used:
------------------
- activity_logs: System activity logging

Session Requirements:
------------------
All routes require:
- email: User's email address
- Admin status verification

Access Control:
-------------
All routes are protected by admin_required verification
"""

from flask import render_template, request, session, redirect, url_for, flash, jsonify
from managers.authentication import admin_required
from Routes.admin import admin
from managers.database_manager import DatabaseManager
import json

@admin.route("/activity_logs")
@admin_required
def activity_logs():
    """
    Display paginated list of activity logs with search functionality.
    
    Query Parameters:
        - page: Current page number (default 1)
        - search: Optional search term
        
    Templates:
        - admin/activity_logs.html: Activity logs management interface
        
    Database Queries:
        - Get paginated activity logs list
        - Count total activity logs
        
    Process:
        1. Verify admin status
        2. Parse pagination parameters
        3. Execute search if term provided
        4. Fetch activity logs data
        
    Returns:
        template: admin/activity_logs.html with:
            - logs: Paginated activity logs list
            - total_logs: Total logs count
            - current_page: Active page number
            - search_term: Current search filter
    """

    # Get query parameters
    page = int(request.args.get('page', 1))
    search_term = request.args.get('search', '').strip()
    per_page = 100
    
    # Prepare base queries
    base_query = "SELECT id, create_time, content FROM activity_logs"
    count_query = "SELECT COUNT(*) FROM activity_logs"
    
    # Prepare search and pagination parameters
    search_params = []
    
    # Apply search if term exists
    if search_term:
        base_query += " WHERE content LIKE %s"
        count_query += " WHERE content LIKE %s"
        search_params = [f'%{search_term}%']
    
    # Add sorting and pagination
    base_query += " ORDER BY create_time DESC LIMIT %s OFFSET %s"
    
    # Prepare final parameters
    if search_term:
        # For count query, use search params
        count_params = tuple(search_params)
        # For main query, add pagination params
        query_params = tuple(search_params + [per_page, (page - 1) * per_page])
    else:
        # No search, just use pagination params
        count_params = tuple()
        query_params = (per_page, (page - 1) * per_page)
    
    # Execute count query
    total_logs_result = DatabaseManager.execute_query(count_query, count_params)
    total_logs = total_logs_result[0] if total_logs_result else 0
    
    # Execute logs query
    logs_from_db = DatabaseManager.execute_query(base_query, query_params, fetch_all=True)
    
    # Process logs
    formatted_logs = []
    if logs_from_db:
        for log in logs_from_db:
            log_id = log[0]
            create_time = log[1]
            
            # Parse JSON content
            try:
                content_json = json.loads(log[2])
                # Format the content for display
                status = content_json.get('status', 'Unknown')
                message = content_json.get('message', '')
                non_embed_message = content_json.get('non_embed_message', '')
                is_ticket = content_json.get('is_ticket', False)
                
                formatted_logs.append({
                    "id": log_id,
                    "create_time": create_time,
                    "status": status,
                    "message": message,
                    "non_embed_message": non_embed_message,
                    "is_ticket": is_ticket,
                    "raw_content": log[2]  
                })
            except json.JSONDecodeError:
                # Handle non-JSON content
                formatted_logs.append({
                    "id": log_id,
                    "create_time": create_time,
                    "status": "Unknown",
                    "message": log[2],
                    "non_embed_message": "",
                    "is_ticket": False,
                    "raw_content": log[2]
                })
    
    # Calculate total pages
    total_pages = max(1, (total_logs + per_page - 1) // per_page)
    
    return render_template(
        "admin/activity_logs.html", 
        logs=formatted_logs, 
        total_pages=total_pages, 
        current_page=page, 
        total_logs=total_logs,
        search_term=search_term
    )

@admin.route("/activity_logs/view/<int:log_id>")
@admin_required
def view_log_details(log_id):
    """
    Display detailed view of a specific log entry.
    
    Args:
        log_id: ID of the log to view
        
    Templates:
        - admin/log_details.html: Detailed log view
        
    Database Queries:
        - Get specific log by ID
        
    Process:
        1. Verify admin status
        2. Fetch log data by ID
        3. Parse and format log content
        
    Returns:
        template: admin/log_details.html with log details
    """
    # Fetch log by ID
    query = "SELECT id, create_time, content FROM activity_logs WHERE id = %s"
    log_data = DatabaseManager.execute_query(query, (log_id,))
    
    if not log_data:
        flash("Log entry not found", "error")
        return redirect(url_for('admin.activity_logs'))
    
    log_id = log_data[0]
    create_time = log_data[1]
    raw_content = log_data[2]
    
    # Parse JSON content
    try:
        content_json = json.loads(raw_content)
        # Format the content for display
        status = content_json.get('status', 'Unknown')
        message = content_json.get('message', '')
        non_embed_message = content_json.get('non_embed_message', '')
        
        formatted_log = {
            "id": log_id,
            "create_time": create_time,
            "status": status,
            "message": message,
            "non_embed_message": non_embed_message,
            "raw_content": raw_content
        }
    except json.JSONDecodeError:
        # Handle non-JSON content
        formatted_log = {
            "id": log_id,
            "create_time": create_time,
            "status": "Unknown",
            "message": raw_content,
            "non_embed_message": "",
            "raw_content": raw_content
        }
    
    return render_template("admin/log_details.html", log=formatted_log)

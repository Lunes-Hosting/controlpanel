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
from display_cache import DisplayCache
import json

log_count_cache = DisplayCache(max_entries=64)

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
    page = max(1, request.args.get('page', 1, type=int))
    search_term = request.args.get('search', '').strip()
    per_page = 20
    
    # Prepare base queries
    base_query = "SELECT id, create_time FROM activity_logs"
    count_query = "SELECT COUNT(*) FROM activity_logs"
    
    # Prepare search and pagination parameters
    search_params = []
    
    # Apply search if term exists
    if search_term:
        base_query += " WHERE content LIKE %s"
        count_query += " WHERE content LIKE %s"
        search_params = [f'%{search_term}%']
    
    # Add sorting and pagination
    base_query += " ORDER BY create_time DESC, id DESC LIMIT %s OFFSET %s"
    
    count_params = tuple(search_params)

    # Counts can scan large tables (especially substring searches); reuse them
    # briefly while always fetching current entries for the visible page.
    def load_count():
        result = DatabaseManager.execute_query(count_query, count_params)
        return result[0] if result else 0
    total_logs = log_count_cache.get_or_load(('log-count', search_term), load_count, ttl=10)
    total_pages = max(1, (total_logs + per_page - 1) // per_page)
    page = min(page, total_pages)
    query_params = tuple(search_params + [per_page, (page - 1) * per_page])

    # Page through the small ordering index first, then retrieve LONGTEXT only
    # for the selected entries. Deep pages no longer read every skipped body.
    base_query = (
        "SELECT l.id, l.create_time, l.content FROM activity_logs l JOIN (" + base_query +
        ") page_rows ON page_rows.id=l.id ORDER BY page_rows.create_time DESC, page_rows.id DESC"
    )
    
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
                if not isinstance(content_json, dict):
                    raise ValueError("Log content is not an object")
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
            except (ValueError, TypeError):
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
            # The table is a preview; the details route retains the complete
            # message. CSS truncation alone still transfers the entire body.
            preview = str(formatted_logs[-1]['message'] or '')
            formatted_logs[-1]['message'] = preview[:500] + ('…' if len(preview) > 500 else '')
            formatted_logs[-1].pop('raw_content', None)

    total_logs = max(total_logs, (page - 1) * per_page + len(formatted_logs))
    
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
        if not isinstance(content_json, dict):
            raise ValueError("Log content is not an object")
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
    except (ValueError, TypeError):
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

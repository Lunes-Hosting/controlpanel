"""
Admin Ticket Management Routes
=================

This module handles the admin ticket management routes for the control panel.

Templates Used:
-------------
- admin/tickets.html: Ticket management interface

Database Tables Used:
------------------
- tickets: Support ticket tracking
- ticket_comments: Support communication

Session Requirements:
------------------
All routes require:
- email: User's email address
- Admin status verification

Access Control:
-------------
All routes are protected by admin_required verification
"""

from flask import render_template, request, session, redirect, url_for, flash
from managers.authentication import admin_required, support_required
from Routes.admin import admin
from managers.user_manager import get_ptero_id
from managers.database_manager import DatabaseManager
from datetime import datetime, timedelta

@admin.route('/tickets')
@support_required
def admin_tickets_index():
    """
    Display list of all support tickets with filter options.
    
    Query Parameters:
        - filter: Ticket status filter (default 'open')
        
    Templates:
        - admin/tickets.html: Ticket management interface
        
    Database Queries:
        - Get tickets based on filter
        - Get ticket authors
        - Get comment counts
        
    Process:
        1. Verify admin status
        2. Apply status filter
        3. Fetch filtered tickets
        4. Sort by priority
        5. Load user information
        6. Count responses
        
    Returns:
        template: admin/tickets.html with:
            - tickets: Filtered ticket list
            - filter: Current filter status
            - authors: User information
            - responses: Comment counts
            - priorities: Priority levels
            
    Related Functions:
        - get_tickets(): Fetches tickets
        - get_ticket_responses(): Counts comments
    """

    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    # Get filter parameter, default to 'open'
    ticket_filter = request.args.get('filter', 'open')
    
    # Build query based on filter
    if ticket_filter == 'all':
        query = "SELECT * FROM tickets"
        params = ()
    else:
        query = "SELECT * FROM tickets WHERE status = %s"
        params = ('open',)
    
    # Get tickets based on filter
    tickets_list = DatabaseManager.execute_query(
        query, 
        params,
        fetch_all=True
    )
    
    # Calculate ticket age
    current_time = datetime.now()
    old_ticket_threshold = timedelta(days=4)
    
    tickets_with_age = []
    for ticket in tickets_list:
        ticket_data = list(ticket)
        # Check if created_at timestamp exists and is not None
        if ticket[4] and isinstance(ticket[4], datetime):
            ticket_age = current_time - ticket[4]
            ticket_data.append(ticket_age > old_ticket_threshold)
        else:
            ticket_data.append(False)
        tickets_with_age.append(ticket_data)

    return render_template('admin/tickets.html', tickets=tickets_with_age, filter=ticket_filter)


@admin.route('/tickets/api/updates')
@support_required
def tickets_api_updates():
    def stream():
        while True:
            try:
                filter_type = request.args.get('filter', 'open')
                
                if filter_type == 'all':
                    query = "SELECT * FROM tickets"
                    params = ()
                else:
                    query = "SELECT * FROM tickets WHERE status = %s"
                    params = ('open',)
                
                tickets = DatabaseManager.execute_query(query, params, fetch_all=True)
                current_time = datetime.now()
                threshold = timedelta(days=4)
                
                data = []
                for ticket in tickets:
                    ticket_info = {
                        'id': ticket[0],
                        'user_id': ticket[1],
                        'title': ticket[2],
                        'status': ticket[3],
                        'created_at': ticket[4].isoformat() if ticket[4] else None,
                        'reply_status': ticket[5],
                        'last_reply': ticket[6].isoformat() if ticket[6] else None,
                        'is_old': False
                    }
                    
                    if ticket[4] and isinstance(ticket[4], datetime):
                        age = current_time - ticket[4]
                        ticket_info['is_old'] = age > threshold
                    
                    data.append(ticket_info)
                
                yield f"data: {json.dumps({'tickets': data})}\n\n"
                time.sleep(5)
                
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                time.sleep(5)
    
    return Response(stream(), mimetype='text/event-stream')
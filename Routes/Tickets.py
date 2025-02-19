"""
Support Ticket Module
===================

This module handles all support ticket functionality in the control panel,
including ticket creation, messaging, and status management.

Templates Used:
-------------
- tickets.html: Ticket list overview
- ticket.html: Individual ticket view
- ticket_message.html: Message composition

Database Tables Used:
------------------
- tickets: Ticket metadata and status
- ticket_comments: Message content
- users: User information
- admins: Admin privileges

Access Control:
-------------
- Users can only view their own tickets
- Admins can view all tickets
- Only ticket owner or admin can close tickets
- Closed tickets visible only to admins

Session Requirements:
------------------
- email: User's email address
- pterodactyl_id: User's panel ID
- admin: Admin status (optional)

Notifications:
------------
- Email notifications on:
  - New ticket creation
  - New messages
  - Ticket closure
- Discord webhooks for admins

Message Formatting:
----------------
- Supports markdown
- Code block highlighting
- Image attachments
"""

import asyncio
from hashlib import sha256
from flask import Blueprint, request, render_template, session, flash, current_app, redirect, url_for
import sys, time, datetime
from threadedreturn import ThreadWithReturnValue
sys.path.append("..")
from legacy_scripts import *
from products import products
from managers.database_manager import DatabaseManager

tickets = Blueprint('tickets', __name__)

@tickets.route('/')
def tickets_index():
    """Display list of open tickets for the authenticated user."""
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    asyncio.run(after_request_async(session=session, request=request.environ, require_login=True))
    
    #if 'pterodactyl_id' not in session: scope wise; this code is not significant.
    #    ptero_id = get_ptero_id(session['email'])
    #    session['pterodactyl_id'] = ptero_id

    #user_id = get_id(session['email'])
    tickets_list = DatabaseManager.execute_query(
        #"SELECT * FROM tickets WHERE (user_id = %s AND status = 'open')",
        "SELECT t.* FROM tickets t JOIN users u ON t.user_id = u.id WHERE (u.email = %s AND t.status = 'open');",
        #(user_id[0],),
        (session["email"],),
        fetch_all=True
    )

    return render_template('tickets.html', tickets=tickets_list)

@tickets.route('/create/submit', methods=['POST'])
def create_ticket_submit():
    """Handle ticket creation form submission."""
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)

    title = request.form['title']
    message = request.form['message']
    user_id = get_id(session['email'])[0]
    ts = time.time()
    timestamp = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    
    # Get next ticket ID
    ticket_id = DatabaseManager.execute_query(
        "SELECT * FROM tickets ORDER BY id DESC LIMIT 0, 1"
    )
    ticket_id = 0 if ticket_id is None else ticket_id[0] + 1
    
    # Create ticket
    DatabaseManager.execute_query(
        "INSERT INTO tickets (id, user_id, title, status, created_at) VALUES (%s, %s, %s, %s, %s)",
        (ticket_id, user_id, title, "open", timestamp)
    )
 
    # Get next comment ID
    comment_id = DatabaseManager.execute_query(
        "SELECT * FROM ticket_comments ORDER BY id DESC LIMIT 0, 1"
    )
    comment_id = 0 if comment_id is None else comment_id[0] + 1
    
    # Add initial message
    DatabaseManager.execute_query(
        "INSERT INTO ticket_comments (id, ticket_id, user_id, ticketcomment, created_at) VALUES (%s, %s, %s, %s, %s)",
        (comment_id, ticket_id, user_id, message, timestamp)
    )
    
    webhook_log(f"Ticket created by `{session['email']}` with title `{title}` <@&1024761808428466257> https://betadash.lunes.host/tickets/{ticket_id}")
    return redirect(url_for('tickets.ticket', ticket_id=ticket_id))

@tickets.route('/message/submit/<ticket_id>', methods=['POST'])
def add_message_submit(ticket_id):
    """Add a new message to an existing ticket."""
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)

    message = request.form['message']
    user_id = get_id(session['email'])[0]
    ts = time.time()
    timestamp = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

    # Get ticket info
    tick_info = DatabaseManager.execute_query(
        "SELECT user_id, title FROM tickets WHERE (id = %s)",
        (ticket_id,)
    )

    # Get next comment ID
    comment_id = DatabaseManager.execute_query(
        "SELECT * FROM ticket_comments ORDER BY id DESC LIMIT 0, 1"
    )
    comment_id = 0 if comment_id is None else comment_id[0] + 1
    
    # Add message
    DatabaseManager.execute_query(
        "INSERT INTO ticket_comments (id, ticket_id, user_id, ticketcomment, created_at) VALUES (%s, %s, %s, %s, %s)",
        (comment_id, ticket_id, user_id, message, timestamp)
    )

    if not is_admin(session['email']):
        webhook_log(f"Ticket comment added by `{session['email']}` with message `{message}` <@&1024761808428466257> https://betadash.lunes.host/tickets/{ticket_id}")
    else:
        email = DatabaseManager.execute_query(
            "SELECT email FROM users WHERE (id = %s)",
            (tick_info[0],)
        )[0]
        ThreadWithReturnValue(target=send_email, args=(email, f"Ticket comment added by staff member", f"Ticket comment added by staff member with message `{message}` https://betadash.lunes.host/tickets/{ticket_id}", current_app._get_current_object())).start()
        webhook_log(f"Ticket comment added by staff member `{session['email']}` with message `{message}` https://betadash.lunes.host/tickets/{ticket_id}")
    
    return redirect(url_for('tickets.ticket', ticket_id=ticket_id))

@tickets.route('/<ticket_id>')
def ticket(ticket_id):
    """Display a specific ticket and its messages."""
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    
    user_info = DatabaseManager.execute_query(
        "SELECT * from users where email = %s",
        (session['email'],)
    )
    
    # Get ticket info
    info = DatabaseManager.execute_query(
        "SELECT * FROM tickets where id = %s",
        (ticket_id,)
    )

    print(user_info)
    print(info)
    
    # Check permissions
    if info[3] == "closed" and not is_admin(session['email']):
        return redirect(url_for('tickets.tickets_index'))
    if user_info[2] != "admin" and info[1] != user_info[0]:
        return redirect(url_for('tickets.tickets_index'))
    
    # Get messages
    messages_tuple = DatabaseManager.execute_query(
        "SELECT * FROM ticket_comments where ticket_id = %s",
        (ticket_id,),
        fetch_all=True
    )
    
    messages = []
    for message in messages_tuple:
        messages.append({
            "author": get_name(message[2])[0],
            "message": message[3],
            "created_at": message[4]
        })
    
    real_info = {
        "author": get_name(info[1])[0],
        "title": info[2],
        "created_at": info[4],
        "id": info[0]
    }
    
    return render_template("ticket.html", messages=messages, info=real_info)

@tickets.route('/close/<ticket_id>', methods=['POST'])
def close_ticket(ticket_id):
    """Close a support ticket."""
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    
    user_info = DatabaseManager.execute_query(
        "SELECT * from users where email = %s",
        (session['email'],)
    )
    
    info = DatabaseManager.execute_query(
        "SELECT * FROM tickets where id = %s",
        (ticket_id,)
    )
    
    if user_info[2] != "admin" and info[1] != user_info[0]:
        return redirect(url_for('tickets.tickets_index'))
    
    DatabaseManager.execute_query(
        "UPDATE tickets SET status = 'closed' WHERE id = %s",
        (ticket_id,)
    )
    
    return redirect(url_for('tickets.tickets_index'))

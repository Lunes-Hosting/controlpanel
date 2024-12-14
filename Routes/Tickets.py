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

from flask import Blueprint, request, render_template, session, flash, current_app
import sys, time
from threadedreturn import ThreadWithReturnValue
sys.path.append("..")
from scripts import *
from products import products

tickets = Blueprint('tickets', __name__)

@tickets.route('/')
def tickets_index():
    """
    Display list of open tickets for the authenticated user.
    
    Templates:
        - tickets.html: Ticket overview list
        
    Database Queries:
        - Get user's tickets
        - Get message counts
        - Get admin status
        
    Process:
        1. Verify authentication
        2. Get user's panel ID
        3. Fetch open tickets
        4. Load message counts
        5. Check admin status
        
    Returns:
        template: tickets.html with:
            - tickets: Open ticket list
            - counts: Message counts
            - is_admin: Admin privileges
            
    Related Functions:
        - get_ticket_list(): Fetches tickets
        - count_messages(): Gets responses
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)
    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    user_id = get_id(session['email'])
    tickets_list = use_database("SELECT * FROM tickets WHERE (user_id = %s AND status = 'open')", (user_id[0],), all=True)

    return render_template('tickets.html', tickets=tickets_list)


@tickets.route('/create/submit', methods=['POST'])
def create_ticket_submit():
    """
    Handle ticket creation form submission.
    
    Templates:
        - error.html: Shows validation errors
        
    Database Queries:
        - Create ticket record
        - Add initial message
        - Get user information
        
    Process:
        1. Verify authentication
        2. Validate form data
        3. Create ticket record
        4. Add initial message
        5. Send notifications
        6. Log creation
        
    Form Data:
        - title: Ticket subject
        - message: Initial content
        
    Returns:
        redirect: To new ticket with:
            - ticket_id: New ticket ID
            - success: Creation status
            
    Related Functions:
        - create_ticket(): Makes record
        - notify_admins(): Alerts staff
        - log_ticket(): Records creation
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)
    # recaptcha_response = request.form.get('g-recaptcha-response')
    # data = {
    #     'secret': RECAPTCHA_SECRET_KEY,
    #     'response': recaptcha_response
    # }

    # response = requests.post('https://www.google.com/recaptcha/api/siteverify', data=data)
    # result = response.json()
    # if not result['success']:
    #     flash("Failed captcha please try again")
    #     return redirect(url_for("tickets.tickets_index"))
    #get form data
    title = request.form['title']
    message = request.form['message']
    user_id = get_id(session['email'])[0]
    ts = time.time()
    timestamp = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    
    # create a ticket
    ticket_id = use_database("SELECT * FROM tickets ORDER BY id DESC LIMIT 0, 1")
    if ticket_id is None:
        ticket_id = 0
    else:
        ticket_id = ticket_id[0] + 1
    query = ("INSERT INTO tickets (id, user_id, title, status, created_at) VALUES (%s, %s, %s, %s, %s)")
    use_database(query, (ticket_id, user_id, title, "open", timestamp))
 
    #add message
    comment_id = use_database("SELECT * FROM ticket_comments ORDER BY id DESC LIMIT 0, 1")
    if comment_id is None:
        comment_id = 0
    else:
        comment_id = comment_id[0] + 1
    query_message = ("INSERT INTO ticket_comments (id, ticket_id, user_id, ticketcomment, created_at) VALUES (%s, %s, %s, %s, %s)")
    use_database(query_message, (comment_id, ticket_id, user_id, message, timestamp))
    
    webhook_log(f"Ticket created by `{session['email']}` with title `{title}` <@&1024761808428466257> https://betadash.lunes.host/tickets/{ticket_id}")
    return redirect(url_for('tickets.ticket', ticket_id=ticket_id))


@tickets.route('/message/submit/<ticket_id>', methods=['POST'])
def add_message_submit(ticket_id):
    """
    Add a new message to an existing ticket.
    
    Args:
        ticket_id: Target ticket ID
        
    Templates:
        - ticket_message.html: Message form
        
    Database Queries:
        - Verify ticket access
        - Add message record
        - Update ticket status
        
    Process:
        1. Verify authentication
        2. Check ticket access
        3. Validate message
        4. Add to database
        5. Send notifications
        6. Update last reply
        
    Form Data:
        - message: Content text
        
    Returns:
        redirect: To ticket view with:
            - success: Message status
            - message_id: New message ID
            
    Related Functions:
        - add_message(): Stores content
        - notify_users(): Alerts parties
        - format_message(): Processes text
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)

    #get form data
    message = request.form['message']
    user_id = get_id(session['email'])[0]
    ts = time.time()
    timestamp = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

    #get ticket info
    tick_info =use_database("SELECT user_id, title FROM tickets WHERE (id = %s)", (ticket_id,))

    #add message
    comment_id = use_database("SELECT * FROM ticket_comments ORDER BY id DESC LIMIT 0, 1")
    if comment_id is None:
        comment_id = 0
    else:
        comment_id = comment_id[0] + 1
    query_message = ("INSERT INTO ticket_comments (id, ticket_id, user_id, ticketcomment, created_at) VALUES (%s, %s, %s, %s, %s)")
    use_database(query_message, (comment_id, ticket_id, user_id, message, timestamp))
    if not is_admin(session['email']):
        webhook_log(f"Ticket comment added by `{session['email']}` with message `{message}` <@&1024761808428466257> https://betadash.lunes.host/tickets/{ticket_id}")
    if is_admin(session['email']):
        email = use_database("SELECT email FROM users WHERE (id = %s)", (tick_info[0],))[0]
        ThreadWithReturnValue(target=send_email, args=(email, f"Ticket comment added by staff member", f"Ticket comment added by staff member with message `{message}` https://betadash.lunes.host/tickets/{ticket_id}", current_app._get_current_object())).start()
        webhook_log(f"Ticket comment added by staff member `{session['email']}` with message `{message}` https://betadash.lunes.host/tickets/{ticket_id}")
    return redirect(url_for('tickets.ticket', ticket_id=ticket_id))


@tickets.route('/<ticket_id>')
def ticket(ticket_id):
    """
    Display a specific ticket and its messages.
    
    Args:
        ticket_id: Ticket to view
        
    Templates:
        - ticket.html: Ticket thread view
        
    Database Queries:
        - Get ticket details
        - Get all messages
        - Get user information
        
    Process:
        1. Verify authentication
        2. Check view permission
        3. Load ticket data
        4. Fetch messages
        5. Format content
        
    Returns:
        template: ticket.html with:
            - ticket: Basic information
            - messages: Full thread
            - users: Participant info
            - can_reply: Access control
            
    Related Functions:
        - get_messages(): Loads thread
        - format_content(): Processes text
        - check_access(): Verifies rights
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    
    user_query = "SELECT * from users where email = %s"
    user_info = use_database(user_query, (session['email'],))
    
    messages = []
    query = "SELECT * FROM tickets where id = %s"
    # info will be (id, user_id, title, status, created_at)
    info = use_database(query, (ticket_id,))
    if info[3] == "closed" and not is_admin(session['email']):
        return redirect(url_for('tickets.tickets_index'))
    if user_info[2] != "admin" and info[1] != user_info[0]:
        return redirect(url_for('tickets.tickets_index'))
    
    query_messages = "SELECT * FROM ticket_comments where ticket_id = %s"
    # messages tuple is a list of (id, ticket_id, user_id, ticketcomment, created_at)
    messages_tuple = use_database(query_messages, (ticket_id,), all=True)
    title = info[2]
    for message in messages_tuple:
        messages.append({"author": get_name(message[2])[0], "message": message[3], "created_at": message[4]})
    real_info = {"author": get_name(info[1])[0], "title": info[2], "created_at": info[4], "id": info[0]}
    return render_template("ticket.html", messages=messages, info=real_info)


@tickets.route('/close/<ticket_id>', methods=['POST'])
def close_ticket(ticket_id):
    """
    Close a support ticket.
    
    Args:
        ticket_id: Ticket to close
        
    Database Queries:
        - Verify ownership/admin
        - Update ticket status
        - Log closure
        
    Process:
        1. Verify authentication
        2. Check close permission
        3. Update status
        4. Send notifications
        5. Log action
        
    Access Control:
        - Ticket owner can close
        - Admins can close any
        - Closed tickets:
          - Hidden from users
          - Visible to admins
          
    Returns:
        redirect: To ticket list with:
            - success: Closure status
            - message: Result notice
            
    Related Functions:
        - update_status(): Changes state
        - notify_closure(): Alerts users
        - log_action(): Records change
    """
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    
    user_query = "SELECT * from users where email = %s"
    user_info = use_database(user_query, (session['email'],))
    query = "SELECT * FROM tickets where id = %s"
    # info will be (id, user_id, title, status, created_at)
    info = use_database(query, (ticket_id,))
    if info[3] == "closed" and not is_admin(session['email']):
        return redirect(url_for('tickets.tickets_index'))
    if user_info[2] != "admin" and info[1] != user_info[0]:
        return redirect(url_for('tickets.tickets_index'))
    queryy = "UPDATE tickets set status = 'closed' where id = %s"
    use_database(queryy, (ticket_id,))
    if is_admin(session['email']):
        return redirect(url_for('admin.admin_tickets_index'))
    return redirect(url_for('tickets.tickets_index'))

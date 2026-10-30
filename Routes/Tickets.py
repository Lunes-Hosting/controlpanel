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
- Replies require ticket ownership or an admin/support role
- Only admins can reply to closed tickets

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

from hashlib import sha256
from uuid import uuid4
import secrets
import hmac
from flask import Blueprint, request, render_template, session, flash, current_app, redirect, url_for, abort
import sys, time, datetime
from threadedreturn import ThreadWithReturnValue
sys.path.append("..")
from managers.authentication import login_required
from managers.user_manager import get_id, get_name, is_admin, is_support
from managers.email_manager import send_email
from managers.logging import webhook_log
from managers.database_manager import DatabaseManager
from managers.ticket_ids import ensure_ticket_id_schema, ensure_comment_auto_increment
from managers.account_review import get_account_review, decide_account_review

ensure_ticket_id_schema()
ensure_comment_auto_increment()

try:
    from discord_bot.ticket_bridge import (
        schedule_ticket_channel_creation,
        schedule_ticket_channel_deletion,
        schedule_ticket_message,
        schedule_ticket_channel_status_update,
        schedule_account_review_refresh,
    )
except Exception:
    def schedule_ticket_channel_creation(*args, **kwargs):
        return None

    def schedule_ticket_message(*args, **kwargs):
        return None

    def schedule_ticket_channel_deletion(*args, **kwargs):
        return None

    def schedule_ticket_channel_status_update(*args, **kwargs):
        return None

    def schedule_account_review_refresh(*args, **kwargs):
        return None

tickets = Blueprint('tickets', __name__)

@tickets.route('/')
@login_required
def tickets_index():
    """Display list of open tickets for the authenticated user."""
    tickets_list = DatabaseManager.execute_query(
        #"SELECT * FROM tickets WHERE (user_id = %s AND status = 'open')",
        "SELECT t.* FROM tickets t JOIN users u ON t.user_id = u.id WHERE (u.email = %s AND t.status = 'open') ORDER BY t.created_at DESC, t.id DESC",
        #(user_id[0],),
        (session["email"],),
        fetch_all=True
    )

    return render_template('tickets.html', tickets=tickets_list)

@tickets.route('/create/submit', methods=['POST'])
@login_required
def create_ticket_submit():
    """Handle ticket creation form submission."""

    title = request.form['title']
    message = request.form['message']
    user_id = get_id(session['email'])[0]
    ts = time.time()
    timestamp = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    
    existing_open_ticket = DatabaseManager.execute_query(
        "SELECT id FROM tickets WHERE user_id = %s AND status = 'open' LIMIT 1",
        (user_id,)
    )
    if existing_open_ticket:
        flash("You already have an open ticket. Please close it before creating a new one.")
        return redirect(url_for('tickets.tickets_index'))

    ticket_id = str(uuid4())
    
    # Create ticket
    DatabaseManager.execute_query(
        "INSERT INTO tickets (id, user_id, title, status, created_at) VALUES (%s, %s, %s, %s, %s)",
        (ticket_id, user_id, title, "open", timestamp)
    )
 
    # Add initial message
    DatabaseManager.execute_query(
        "INSERT INTO ticket_comments (ticket_id, user_id, ticketcomment, created_at) VALUES (%s, %s, %s, %s)",
        (ticket_id, user_id, message, timestamp)
    )

    webhook_log(f"Ticket created by `{session['email']}` with title `{title}` https://betadash.lunes.host/tickets/{ticket_id}", is_ticket=True)
    ticket_url = url_for('tickets.ticket', ticket_id=ticket_id, _external=True)
    opener_name_data = get_name(user_id)
    opener_name = opener_name_data[0] if opener_name_data else session['email']
    schedule_ticket_channel_creation(
        ticket_id,
        title,
        opener_name,
        session['email'],
        message,
        ticket_url,
    )
    return redirect(url_for('tickets.ticket', ticket_id=ticket_id))

@tickets.route('/message/submit/<ticket_id>', methods=['POST'])
@login_required
def add_message_submit(ticket_id):
    """Add a new message to an existing ticket."""

    user = get_id(session['email'])
    if not user:
        abort(403)
    user_id = user[0]

    # Authorize this ticket before writing a comment or sending notifications.
    tick_info = DatabaseManager.execute_query(
        "SELECT user_id, status FROM tickets WHERE (id = %s)",
        (ticket_id,)
    )
    if not tick_info:
        abort(404)

    admin = is_admin(session['email'])
    staff = admin or is_support(session['email'])
    if tick_info[0] != user_id and not staff:
        # Do not disclose whether another user's ticket exists.
        abort(404)
    if tick_info[1] == 'closed' and not admin:
        abort(403)

    message = request.form['message']
    ts = time.time()
    timestamp = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

    # Add message
    DatabaseManager.execute_query(
        "INSERT INTO ticket_comments (ticket_id, user_id, ticketcomment, created_at) VALUES (%s, %s, %s, %s)",
        (ticket_id, user_id, message, timestamp)
    )

    author_name_data = get_name(user_id)
    author_name = author_name_data[0] if author_name_data else session['email']
    if not staff:
        DatabaseManager.execute_query(
            "UPDATE tickets SET reply_status = 'waiting', last_reply = NOW() WHERE id = %s",
            (ticket_id,)
        )

        webhook_log(f"Ticket comment added by `{session['email']}` with message `{message}` https://betadash.lunes.host/tickets/{ticket_id}", is_ticket=True)
        schedule_ticket_message(ticket_id, author_name, session['email'], message, "Panel User")
        schedule_ticket_channel_status_update(ticket_id)

    else:
        email = DatabaseManager.execute_query(
            "SELECT users.email FROM tickets JOIN users ON tickets.user_id = users.id WHERE tickets.id = %s",
            (ticket_id,)
        )[0]
        ThreadWithReturnValue(target=send_email, args=(email, f"Ticket comment added by staff member", f"Ticket comment added by staff member with message `{message}` https://betadash.lunes.host/tickets/{ticket_id}", current_app._get_current_object())).start()
        DatabaseManager.execute_query(
            "UPDATE tickets SET reply_status = 'responded', last_reply = NOW() WHERE id = %s",
            (ticket_id,)
        )

        webhook_log(f"Ticket comment added by staff member `{session['email']}` with message `{message}` https://betadash.lunes.host/tickets/{ticket_id}", is_ticket=True)
        schedule_ticket_message(ticket_id, author_name, session['email'], message, "Staff Panel")
        schedule_ticket_channel_status_update(ticket_id)

    return redirect(url_for('tickets.ticket', ticket_id=ticket_id))

@tickets.route('/<ticket_id>')
@login_required
def ticket(ticket_id):
    """Display a specific ticket and its messages."""

    user_info = DatabaseManager.execute_query(
        "SELECT * from users where email = %s",
        (session['email'],)
    )
    
    # Get ticket info
    info = DatabaseManager.execute_query(
        "SELECT * FROM tickets where id = %s",
        (ticket_id,)
    )

    # Graceful fallback if a ticket vanishes into thin air so you never hit a 500.
    if not info:
        return redirect(url_for('tickets.tickets_index'))
    
    # Check permissions
    if info[3] == "closed" and not is_admin(session['email']):
        return redirect(url_for('tickets.tickets_index'))
    if user_info[2] != "admin" and user_info[2] != "support" and info[1] != user_info[0]:
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
        "status": info[3],
        "created_at": info[4],
        "id": info[0]
    }
    
    account_review = get_account_review(info[1]) if is_admin(session['email']) else None
    review_token = None
    if account_review and account_review['pending']:
        review_token = session.setdefault('ticket_review_token', secrets.token_urlsafe(32))
    return render_template("ticket.html", messages=messages, info=real_info,
                           account_review=account_review, review_token=review_token)


@tickets.route('/<ticket_id>/account-approval', methods=['POST'])
@login_required
def review_ticket_account(ticket_id):
    if not is_admin(session['email']):
        abort(403)
    token = request.form.get('review_token', '')
    expected = session.get('ticket_review_token')
    if not expected or len(token) > 128 or not token.isascii() or not hmac.compare_digest(token, expected):
        abort(403)
    row = DatabaseManager.execute_query('SELECT user_id FROM tickets WHERE id=%s', (ticket_id,))
    if not row:
        abort(404)
    try:
        state = decide_account_review(row[0], request.form.get('decision'),
                                      f"web:{session['account_id']}", ticket_id)
    except ValueError:
        abort(400)
    except (LookupError, PermissionError):
        abort(409)
    try:
        schedule_account_review_refresh(row[0])
    except Exception:
        # The decision is already durable; Discord refresh must not undo it.
        current_app.logger.exception('Could not schedule account review refresh')
    if state['changed']:
        try:
            webhook_log(f"Account ID {row[0]} {state['status']} by {state['reviewer']} via ticket {ticket_id}", database_log=True)
        except Exception:
            current_app.logger.exception('Could not mirror the saved account review to the activity log')
        flash('Free server approval saved: ' + state['status'] + '.')
    else:
        flash('This account has already been reviewed or no longer needs approval.')
    return redirect(url_for('tickets.ticket', ticket_id=ticket_id))

@tickets.route('/toggle/<ticket_id>', methods=['POST'])
@login_required
def toggle_ticket_status(ticket_id):
    """Toggle the status of a support ticket (open/closed)."""

    user_info = DatabaseManager.execute_query(
        "SELECT * FROM users WHERE email = %s",
        (session['email'],)
    )

    info = DatabaseManager.execute_query(
        "SELECT * FROM tickets WHERE id = %s",
        (ticket_id,)
    )

    # Ensure the ticket exists
    if not info:
        return redirect(url_for('tickets.tickets_index'))

    current_status = info[3]

    if user_info[2] != "admin" and user_info[2] != "support" and info[1] != user_info[0]:
        return redirect(url_for('tickets.tickets_index'))

    # Toggle the status
    new_status = 'closed' if current_status == 'open' else 'open'

    if new_status == 'closed':
        DatabaseManager.execute_query(
            "UPDATE tickets SET status = 'closed', reply_status = 'responded', last_reply = NOW() WHERE id = %s",
            (ticket_id,)
        )
        schedule_ticket_channel_deletion(ticket_id, "Closed via web panel")
        schedule_ticket_channel_status_update(ticket_id)
    else:
        DatabaseManager.execute_query(
            "UPDATE tickets SET status = 'open', reply_status = 'waiting', last_reply = NOW() WHERE id = %s",
            (ticket_id,)
        )
        schedule_ticket_channel_status_update(ticket_id)
    if is_admin(session['email']):
      return redirect(url_for('admin.admin_tickets_index'))
    return redirect(url_for('tickets.tickets_index'))

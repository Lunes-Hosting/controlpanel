from flask import Blueprint, request, render_template, session, flash
import sys, time

sys.path.append("..")
from scripts import *
from products import products

tickets = Blueprint('tickets', __name__)

@tickets.route('/')
def tickets_index():
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
    message = request.form['title']
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
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    after_request(session=session, request=request.environ, require_login=True)

    #get form data
    message = request.form['message']
    user_id = get_id(session['email'])[0]
    ts = time.time()
    timestamp = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


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
        webhook_log(f"Ticket comment added by staff member `{session['email']}` with message `{message}` https://betadash.lunes.host/tickets/{ticket_id}")
    return redirect(url_for('tickets.ticket', ticket_id=ticket_id))

@tickets.route('/<ticket_id>')
def ticket(ticket_id):
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    
    user_query = "SELECT * from users where email = %s"
    user_info = use_database(user_query, (session['email'],))
    
    messages = []
    query = "SELECT * FROM tickets where id = %s"
    # info will be (id, user_id, title, status, created_at)
    info = use_database(query, (ticket_id,))
    if info[3] == "closed":
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
    return redirect(url_for('tickets.tickets_index'))

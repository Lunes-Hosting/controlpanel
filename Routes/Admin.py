from flask import Blueprint, request, render_template, session, flash
import sys

sys.path.append("..")
from scripts import *
import json

# Create a blueprint for the user routes
admin = Blueprint('admin', __name__)


@admin.route("/")
def admin_index():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    return render_template("admin/admin.html")


@admin.route("/users")
def users():
    full_users = []
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    # resp = requests.get(f"{PTERODACTYL_URL}api/application/users?per_page=100000", headers=HEADERS).json()
    query = "SELECT name, credits, role, email, suspended, id FROM users"
    users_from_db = use_database(query, all=True)
    for user in users_from_db:
        full_users.append({"name": user[0], "credits": int(user[1]), "role": user[2], "email": user[3], "suspended": user[4],
                           "id": user[5]})
    return render_template("admin/users.html", users=full_users)


@admin.route("/servers")
def admin_servers():
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    resp = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=10000", headers=HEADERS).json()
    return render_template("admin/servers.html", servers=resp['data'])


@admin.route('/user/<user_id>')
def admin_user(user_id):
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    resp = requests.get(f"{PTERODACTYL_URL}api/application/users/{user_id}", headers=HEADERS).json()
    return resp


@admin.route('/server/<server_id>')
def admin_server(server_id):
    if 'email' not in session:
        return redirect(url_for("user.login_user"))
    if not is_admin(session['email']):
        return "YOUR NOT ADMIN BRO"
    after_request(session=session, request=request.environ, require_login=True)

    if 'pterodactyl_id' in session:
        ptero_id = session['pterodactyl_id']
    else:
        ptero_id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = ptero_id

    products_local = list(products)
    info = get_server_information(server_id)
    product = convert_to_product(info)
    return render_template('admin/server.html', info=info, products=products_local, product=product)

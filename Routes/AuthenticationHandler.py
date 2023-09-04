from flask import Blueprint, request, render_template, redirect, url_for, session, flash
import sys
sys.path.append("..") 
from scripts import *
import json

# Create a blueprint for the user routes
user = Blueprint('user', __name__)



@user.route('/login', methods=['POST', 'GET'])
def login_user():
    if request.method == "POST":
        print(1)
        data = request.form
        email = data.get('email')
        password = data.get('password')
        try:
            response = login(email, password)
            if response is None:
                flash("Not correct info please make sure you have an account")
                return redirect(url_for('user.login_user'))
            print(8)
            try:
                _ = json.load(response)
                return response
            except Exception:
                session['email'] = email
                print("yes")
                return redirect(url_for('index'))
        except Exception as e:
            print(e)
            pass
    else:
        print(2)
        return render_template("login.html")
    
@user.route('/')
def index():
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
    update_ip(session['email'], request.headers)
    update_last_seen(session['email'])
    credits = get_credits(session['email'])
    return render_template("account.html", credits=int(credits))

@user.route('/logout', methods=['GET'])
def logout():
    session.clear()
    return redirect(url_for("index"))

from flask import Blueprint, request, render_template, redirect, url_for, session
import sys
sys.path.append("..") 
from scripts import *
import json

# Create a blueprint for the user routes
user = Blueprint('user', __name__)

@user.route('/register', methods=['POST', 'GET'])
def register_user():
    if request.method == "POST":
        print(1)
        data = request.form
        email = data.get('email')
        password = data.get('password')
        name = data.get('username')

        try:
            response = register(email, password, name)
            print(8)
            try:
                _ = json.load(response)
                return response
            except Exception:
                session['email'] = email
                
                return redirect(url_for('index'))
        except Exception as e:
            pass
    else:
        print(2)
        return render_template("register.html")


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
                return redirect(url_for('user.login_user'))
            print(8)
            try:
                _ = json.load(response)
                return response
            except Exception:
                session['email'] = email
                return redirect(url_for('index'))
        except Exception as e:
            pass
    else:
        print(2)
        return render_template("login.html")
    
@user.route('/')
def index():
    if not 'email' in session:
        return redirect(url_for('user.login_user'))
    credits = get_credits(session['email'])
    return render_template("account.html", credits=credits)

@user.route('/logout', methods=['GET'])
def logout():
    session.clear()
    return redirect(url_for("index"))
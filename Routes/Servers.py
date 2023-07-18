from flask import Blueprint, request, render_template, redirect, url_for, session
import sys
sys.path.append("..") 
from scripts import *
import json
servers = Blueprint('servers', __name__)

@servers.route('/')
def servers_index():
    if 'pterodactyl_id' in session:
        id = session['pterodactyl_id']
    else:
        id = get_ptero_id(session['email'])
        session['pterodactyl_id'] = id
        
    servers = list_servers(id[0][0])
    print(servers)
    return render_template('servers.html', servers=servers)

@servers.route('/<server_id>')
def server(server_id):
    print(server_id)
    info = get_server_information(server_id)
    print(info)
    return render_template('server.html', info=info)
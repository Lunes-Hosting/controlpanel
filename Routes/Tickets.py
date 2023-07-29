from flask import Blueprint, request, render_template, redirect, url_for, session
import sys
sys.path.append("..") 
from scripts import *
import json

# Create a blueprint for the user routes
tickets = Blueprint('tickets', __name__)

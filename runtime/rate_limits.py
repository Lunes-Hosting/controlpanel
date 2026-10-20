"""One limiter shared by routes and the app, initialized after configuration."""

from flask_limiter import Limiter
from runtime.request_security import client_ip

limiter = Limiter(key_func=client_ip)

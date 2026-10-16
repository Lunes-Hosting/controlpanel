#db variables
# Copy this file to config.py and set False to disable all automatic alt checks,
# shared-IP signup blocking, device tracking, and fingerprint-based review.
ALT_DETECTION_ENABLED = False
# When detection is enabled, False warns on login IP matches instead of suspending.
ALT_ENFORCE = False
# Database connections per web/bot worker (1-32).
DATABASE_POOL_SIZE = 10
# Use redis://... with the redis package for shared limits across workers.
RATELIMIT_STORAGE_URI = 'memory://'
HOST=
USER=
PASSWORD=
DATABASE=
PTERODACTYL_URL=
PTERODACTYL_ADMIN_KEY = 
PTERODACTYL_CLIENT_KEY = 
STRIPE_API_KEY = 
STRIPE_SECRET_KEY = 
URL = "http://127.0.0.1:3040/"
YOUR_SUCCESS_URL = f'{URL}store/success'
YOUR_CANCEL_URL = f'{URL}store/cancel'
HOSTED_URL = URL
SECRET_KEY =
MAIL_SERVER=
MAIL_PORT =
MAIL_USERNAME =
MAIL_PASSWORD = 
MAIL_DEFAULT_SENDER = 
RECAPTCHA_SITE_KEY = 
RECAPTCHA_SECRET_KEY =
WEBHOOK_URL =
#DISCORD BOT
ENABLE_BOT = True
TOKEN = 
DEBUG_FRONTEND_MODE =
DISCORD_GUILD_ID = 
TICKET_DISCORD_CATEGORY_ID = 
ACCOUNT_APPROVAL_CHANNEL_ID = "1539336127880896532"
AUTODEPLOY_NEST_ID: int = 
#DISCORD INVITE
DISCORD_INVITE = ""
DOWNLOAD_API_URL = ""

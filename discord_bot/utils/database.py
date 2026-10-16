from managers.database_manager import DatabaseManager
from managers.suspension_manager import set_user_suspension, require_reason, get_user_suspension
from managers.email_manager import send_email
from flask import current_app
from ..utils.logger import logger
class UserDB():

    def get_user_info(email):
        try:
            user_info = DatabaseManager.execute_query("SELECT credits, role, pterodactyl_id, id, suspended FROM users WHERE email = %s",(email,) )
            if not user_info:
                return "Error: User not found"
            return {"credits": user_info[0], "role": user_info[1], "pterodactyl_id": user_info[2], "id": user_info[3], "suspended": user_info[4], "suspension_reason": get_user_suspension(user_info[3]) if user_info[4] else None}
        except Exception as e:
            logger.error(f"Error fetching user info: {str(e)}")
            return "Error fetching user info"
    
    def get_discord_user_info(discord_id):
        try:
            user_info = DatabaseManager.execute_query("SELECT credits, role, pterodactyl_id, id, suspended, email FROM users WHERE discord_id = %s",(discord_id,) )
            if not user_info:
                return "Error: User not found"
            return {"credits": user_info[0], "role": user_info[1], "pterodactyl_id": user_info[2], "id": user_info[3], "suspended": user_info[4], "email": user_info[5], "suspension_reason": get_user_suspension(user_info[3]) if user_info[4] else None}
        except Exception as e:
            logger.error(f"Error fetching user info: {str(e)}")
            return "Error fetching user info"
        

    def suspend_user(email, reason, actor="discord"):
        try:
            reason = require_reason(reason)
            row = DatabaseManager.execute_query("SELECT id FROM users WHERE email = %s", (email,))
            if not row:
                return "User not found"
            set_user_suspension(row[0], True, reason, source="discord", actor=actor)
            if row:
                from managers.alt_detection import log_alt_action
                log_alt_action(int(row[0]), "ban", actor=actor, source="discord")
            return "User suspended"
        except Exception as e:
            logger.error(f"Error suspending user: {str(e)}")
            return "Error suspending user"
        
    def get_unban_history(email):
        """Earlier unbans for this account as a readable line, or None if there are none."""
        try:
            row = DatabaseManager.execute_query("SELECT id FROM users WHERE email = %s", (email,))
            if not row:
                return None
            from managers.alt_detection import unban_history, describe_unban_history
            return describe_unban_history(unban_history([int(row[0])])) or None
        except Exception as e:
            logger.error(f"Error fetching unban history: {str(e)}")
            return None

    def unsuspend_user(email, actor="discord"):
        try:
            send_email(email, "Account Unsuspended", "Your account has been unsuspended.", current_app._get_current_object())
            row = DatabaseManager.execute_query("SELECT id FROM users WHERE email = %s", (email,))
            if row:
                set_user_suspension(row[0], False)
            if row:
                from managers.alt_detection import exempt_user
                exempt_user(int(row[0]), actor=actor, source="discord")
            return "User unsuspended"
        except Exception as e:
            logger.error(f"Error unsuspending user: {str(e)}")
            return "Error unsuspending user"
        
    def get_all_users():
        try:
            total_users = DatabaseManager.execute_query("SELECT COUNT(*) FROM users")[0]
            return total_users
        except Exception as e:
            logger.error(f"Error fetching users: {str(e)}")
            return "Error fetching users"
    def get_suspended_users():
        try:
            total_users = DatabaseManager.execute_query("SELECT COUNT(*) FROM users WHERE suspended = 1")[0]
            return total_users
        except Exception as e:
            logger.error(f"Error fetching suspended users: {str(e)}")
            return "Error fetching suspended users"
    def link_discord(email, discord_id):
        try:
            DatabaseManager.execute_query("UPDATE users SET discord_id = %s WHERE email = %s",(discord_id,email))
            return "User linked to Discord"
        except Exception as e:
            logger.error(f"Error linking user to Discord: {str(e)}")
            return "Error linking user to Discord"

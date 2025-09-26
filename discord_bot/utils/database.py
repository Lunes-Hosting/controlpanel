from managers.database_manager import DatabaseManager
from managers.email_manager import send_email
from flask import current_app
from ..utils.logger import logger
class UserDB():

    def get_user_info(email):
        try:
            user_info = DatabaseManager.execute_query("SELECT credits, role, pterodactyl_id, id, suspended FROM users WHERE email = %s",(email,) )
            if not user_info:
                return "Error: User not found"
            return {"credits": user_info[0], "role": user_info[1], "pterodactyl_id": user_info[2], "id": user_info[3], "suspended": user_info[4]}
        except Exception as e:
            logger.error(f"Error fetching user info: {str(e)}")
            return "Error fetching user info"
    
    def get_discord_user_info(discord_id):
        try:
            user_info = DatabaseManager.execute_query("SELECT credits, role, pterodactyl_id, id, suspended, email, last_seen FROM users WHERE discord_id = %s",(discord_id,) )
            if not user_info:
                return "Error: User not found"
            return {"credits": user_info[0], "role": user_info[1], "pterodactyl_id": user_info[2], "id": user_info[3], "suspended": user_info[4], "email": user_info[5], "last_seen": user_info[6]}
        except Exception as e:
            logger.error(f"Error fetching user info: {str(e)}")
            return "Error fetching user info"
        

    def suspend_user(email):
        try:
            send_email(email, "Account Suspended", "Your account has been suspended.", current_app._get_current_object())
            DatabaseManager.execute_query("UPDATE users SET suspended = 1 WHERE email = %s",(email,))
            return "User suspended"
        except Exception as e:
            logger.error(f"Error suspending user: {str(e)}")
            return "Error suspending user"
        
    def unsuspend_user(email):
        try:
            send_email(email, "Account Unsuspended", "Your account has been unsuspended.", current_app._get_current_object())
            DatabaseManager.execute_query("UPDATE users SET suspended = 0 WHERE email = %s",(email,))
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
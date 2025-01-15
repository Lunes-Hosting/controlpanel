from managers.database_manager import DatabaseManager
from ..utils.logger import logger
class UserDB():

    def get_user_info(email):
        try:
            user_info = DatabaseManager.execute_query("SELECT credits, role, pterodactyl_id, suspended FROM users WHERE email = %s",(email,) )
            if not user_info:
                return "Error: User not found"
            return {"credits": user_info[0], "role": user_info[1], "pterodactyl_id": user_info[2], "suspended": user_info[3]}
        except Exception as e:
            logger.error(f"Error fetching user info: {str(e)}")
            return "Error fetching user info"
        
    def suspend_user(email):
        try:
            DatabaseManager.execute_query("UPDATE users SET suspended = 1 WHERE email = %s",(email,))
            return "User suspended"
        except Exception as e:
            logger.error(f"Error suspending user: {str(e)}")
            return "Error suspending user"
        
    def unsuspend_user(email):
        try:
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
from scripts import *

# res =login("harelmoreno@gmail.com", "Abc1234")
# print(res)

# res = describe_users()
# print(res)

# register("dwattynip1a23@gmail.com", "datnipthecaoolest", "Dwatnip", "0.0.0.0")
# update_last_seen(everyone=True)
cnx = mysql.connector.connect(
        host=HOST,
        user=USER,
        password=PASSWORD,
        database=DATABASE
        )
cursor = cnx.cursor(buffered=True)
    
query = f"Select email_verified_at FROM users where email='strazimir@selectel.ru'"
cursor.execute(query)
results = cursor.fetchone()
print(results)
cnx.commit()

# res = get_all_users()
# delete_user(None)

# add_credits("harelmoreno@gmail.com", 100)
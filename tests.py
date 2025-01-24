import requests
import random
from scripts import *

db = DatabaseManager()
res = db.execute_query("SELECT * FROM users", fetch_all=True)
for re in res:
    res_again = db.execute_query("SELECT id FROM users WHERE email = %s", database="panel", values=(re[7],))
    if res_again is None:
        print(re[7])
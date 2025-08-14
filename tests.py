import requests
resp = requests.get("https://lunes.host/blockedemails.txt", timeout=60)
blocked_emails = [line.strip() for line in resp.text.splitlines() if line.strip()]

print(blocked_emails)

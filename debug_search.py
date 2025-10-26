from pathlib import Path
import database

# Use a local debug DB file
dbfile = str(Path.cwd() / "test_debug.db")
database.set_database_file(dbfile)
database.init_db()

import main as app_main
from fastapi.testclient import TestClient
client = TestClient(app_main.app)

print('start booking')
resp = client.post('/booking_flow/start', json={'flight_id':'AI-201','seats':1})
print('start', resp.status_code, resp.text)
pnr = resp.json()['pnr']
resp = client.post(f'/booking_flow/{pnr}/passenger', json={'full_name':'Search User','last_name':'Finder','age':29,'phone':9002002002,'passport_no':'S11111'})
print('passenger', resp.status_code, resp.text)
resp = client.post(f'/booking_flow/{pnr}/pay', json={'payment_method':'card','fail_rate':0.0})
print('pay', resp.status_code, resp.text)
resp = client.get('/bookings/search', params={'name':'Search'})
print('search', resp.status_code, resp.text)

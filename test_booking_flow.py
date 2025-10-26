import os
import os
from fastapi.testclient import TestClient


def setup_test_db(tmp_path):
    # ensure database uses a temp file for tests
    import database
    dbfile = str(tmp_path / "test_flights.db")
    database.set_database_file(dbfile)
    # init db file
    database.init_db()
    return dbfile


def test_happy_booking_flow(tmp_path):
    # prepare test DB
    dbfile = setup_test_db(tmp_path)

    # import main after DB configured
    import main as app_main
    client = TestClient(app_main.app)

    # check initial seats
    flight = next(f for f in app_main.flights_db if f["flight_id"] == "AI-201")
    initial_seats = flight["seats_available"]

    # start booking (reserve seats)
    resp = client.post("/booking_flow/start", json={"flight_id": "AI-201", "seats": 2})
    assert resp.status_code == 200
    data = resp.json()
    pnr = data["pnr"]
    assert data["status"] == "reserved"

    # attach passenger
    passenger = {
        "full_name": "Test User",
        "last_name": "User",
        "age": 30,
        "phone": 9000000000,
        "passport_no": "P1234567"
    }
    resp = client.post(f"/booking_flow/{pnr}/passenger", json=passenger)
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_payment"

    # pay with fail_rate 0 to force success
    resp = client.post(f"/booking_flow/{pnr}/pay", json={"payment_method": "card", "fail_rate": 0.0})
    assert resp.status_code == 200
    pay = resp.json()
    assert pay["status"] == "confirmed"
    booking_id = pay["booking_id"]

    # booking persisted in DB
    resp = client.get(f"/bookings/{booking_id}")
    assert resp.status_code == 200
    persisted = resp.json()
    assert persisted["booking_id"] == booking_id
    assert persisted["status"] == "confirmed"

    # cancel booking
    resp = client.delete(f"/bookings/{booking_id}")
    assert resp.status_code == 200
    cancelled = resp.json()
    assert cancelled["status"] == "cancelled"

    # seats should have been released
    flight_after = next(f for f in app_main.flights_db if f["flight_id"] == "AI-201")
    assert flight_after["seats_available"] == initial_seats


def test_payment_failure_releases_seats(tmp_path):
    setup_test_db(tmp_path)
    import main as app_main
    client = TestClient(app_main.app)

    flight = next(f for f in app_main.flights_db if f["flight_id"] == "AI-202")
    initial = flight["seats_available"]

    resp = client.post("/booking_flow/start", json={"flight_id": "AI-202", "seats": 1})
    assert resp.status_code == 200
    pnr = resp.json()["pnr"]

    resp = client.post(f"/booking_flow/{pnr}/passenger", json={
        "full_name": "Fail User",
        "last_name": "User",
        "age": 28,
        "phone": 9111111111,
        "passport_no": "P7654321"
    })
    assert resp.status_code == 200

    # use fail_rate=1.0 to guarantee failure
    resp = client.post(f"/booking_flow/{pnr}/pay", json={"payment_method": "card", "fail_rate": 1.0})
    assert resp.status_code == 200
    assert resp.json()["status"] == "payment_failed"

    # seats returned
    flight_after = next(f for f in app_main.flights_db if f["flight_id"] == "AI-202")
    assert flight_after["seats_available"] == initial


def test_insufficient_seats(tmp_path):
    setup_test_db(tmp_path)
    import main as app_main
    client = TestClient(app_main.app)

    # attempt to reserve more seats than available
    flight = next(f for f in app_main.flights_db if f["flight_id"] == "AI-201")
    resp = client.post("/booking_flow/start", json={"flight_id": "AI-201", "seats": flight["seats_available"] + 10})
    assert resp.status_code == 400


def test_get_booking_by_pnr_and_temporary(tmp_path):
    # prepare db and app
    setup_test_db(tmp_path)
    import main as app_main
    client = TestClient(app_main.app)

    # create a persisted booking through flow
    resp = client.post("/booking_flow/start", json={"flight_id": "AI-201", "seats": 1})
    assert resp.status_code == 200
    pnr_tmp = resp.json()["pnr"]

    # attach passenger
    passenger = {
        "full_name": "PNR Test",
        "last_name": "User",
        "age": 31,
        "phone": 9001001001,
        "passport_no": "PX11111"
    }
    resp = client.post(f"/booking_flow/{pnr_tmp}/passenger", json=passenger)
    assert resp.status_code == 200

    # confirm payment
    resp = client.post(f"/booking_flow/{pnr_tmp}/pay", json={"payment_method": "card", "fail_rate": 0.0})
    assert resp.status_code == 200
    pay = resp.json()
    assert pay["status"] == "confirmed"
    final_pnr = pay["pnr"]

    # GET persisted booking by PNR
    resp = client.get(f"/bookings/pnr/{final_pnr}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pnr"] == final_pnr
    assert data["status"] == "confirmed"

    # create a temporary booking (no payment)
    resp = client.post("/booking_flow/start", json={"flight_id": "AI-202", "seats": 1})
    assert resp.status_code == 200
    temp_pnr = resp.json()["pnr"]

    # GET temporary booking via /bookings/pnr should fall back to temp bookings
    resp = client.get(f"/bookings/pnr/{temp_pnr}")
    assert resp.status_code == 200
    tb = resp.json()
    assert tb["pnr"] == temp_pnr


def test_search_endpoint(tmp_path):
    setup_test_db(tmp_path)
    import main as app_main
    client = TestClient(app_main.app)

    # create a persisted booking with known passenger name
    resp = client.post("/booking_flow/start", json={"flight_id": "AI-201", "seats": 1})
    pnr = resp.json()["pnr"]
    passenger = {
        "full_name": "Search User",
        "last_name": "Finder",
        "age": 29,
        "phone": 9002002002,
        "passport_no": "S11111"
    }
    client.post(f"/booking_flow/{pnr}/passenger", json=passenger)
    client.post(f"/booking_flow/{pnr}/pay", json={"payment_method": "card", "fail_rate": 0.0})

    # search by name (persisted)
    resp = client.get("/bookings/search", params={"name": "Search"})
    # debug output when something goes wrong
    if resp.status_code != 200:
        print('SEARCH RESPONSE (DEBUG):', resp.status_code, resp.text)
    assert resp.status_code == 200
    results = resp.json()
    assert any("Search User" in (b.get("passenger_name") or "") for b in results)

    # create a temporary booking and attach passenger
    resp = client.post("/booking_flow/start", json={"flight_id": "AI-202", "seats": 1})
    tmp_pnr = resp.json()["pnr"]
    tmp_passenger = {
        "full_name": "Temp Search",
        "last_name": "Tester",
        "age": 26,
        "phone": 9003003003,
        "passport_no": "TMP12345"
    }
    client.post(f"/booking_flow/{tmp_pnr}/passenger", json=tmp_passenger)

    # search temp bookings by name
    resp = client.get("/bookings/search", params={"name": "Temp"})
    assert resp.status_code == 200
    results = resp.json()
    assert any("Temp Search" in (b.get("passenger_name") or "") for b in results)

 
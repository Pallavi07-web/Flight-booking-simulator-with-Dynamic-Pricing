import os
import sqlite3
from sqlite3 import Error
from contextlib import contextmanager

# Allow overriding DB file via env var or by assigning to DATABASE_FILE
DATABASE_FILE = os.getenv("FLIGHT_DB_FILE", "flights.db")

def set_database_file(path: str):
    global DATABASE_FILE
    DATABASE_FILE = path


@contextmanager
def get_db_connection():
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        yield conn
    except Error as e:
        print(f"Error connecting to database: {e}")
        raise
    finally:
        if conn:
            conn.close()


def init_db():
    with get_db_connection() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
            pnr TEXT UNIQUE,
            flight_id TEXT NOT NULL,
            passenger_name TEXT,
            passenger_email TEXT,
            passenger_phone TEXT,
            seats INTEGER NOT NULL,
            status TEXT NOT NULL,
            price REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()


def init_flights_table(seed_flights: list[dict] | None = None):
    """Create flights table (if missing) and optionally seed it with flights.
    This function is idempotent: it only inserts seed rows when the table is empty.
    """
    with get_db_connection() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS flights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_id TEXT UNIQUE,
            origin TEXT,
            destination TEXT,
            duration TEXT,
            price REAL,
            seats_available INTEGER
        )
        """)
        conn.commit()

        # seed if table empty and seed_flights provided
        if seed_flights:
            cur = conn.execute("SELECT COUNT(1) as c FROM flights")
            count = cur.fetchone()[0]
            if count == 0:
                for f in seed_flights:
                    conn.execute(
                        "INSERT INTO flights (flight_id, origin, destination, duration, price, seats_available) VALUES (?, ?, ?, ?, ?, ?)",
                        (f.get("flight_id"), f.get("origin"), f.get("destination"), f.get("duration"), f.get("price"), f.get("seats_available"))
                    )
                conn.commit()


def get_flight(flight_id: str):
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT flight_id, origin, destination, duration, price, seats_available FROM flights WHERE flight_id = ?", (flight_id,)).fetchone()
            return dict(row) if row else None
    except Error:
        # flights table may not exist in some setups; treat as no DB-backed flight
        return None


def reserve_seats(flight_id: str, seats: int) -> tuple[bool, str | None]:
    """Atomically reserve seats for a flight using a DB transaction.
    Returns (True, None) on success or (False, reason) on failure.
    """
    if seats <= 0:
        return False, "seats must be >= 1"
    try:
        with get_db_connection() as conn:
            # use immediate transaction to acquire a reserved lock for writing
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT seats_available FROM flights WHERE flight_id = ?", (flight_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return False, "flight not found"
            available = int(row[0])
            if available < seats:
                conn.execute("ROLLBACK")
                return False, "not enough seats"
            conn.execute("UPDATE flights SET seats_available = seats_available - ? WHERE flight_id = ?", (seats, flight_id))
            conn.commit()
            return True, None
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, str(e)


def release_seats(flight_id: str, seats: int) -> tuple[bool, str | None]:
    """Atomically release seats back to a flight.
    Returns (True, None) on success.
    """
    if seats <= 0:
        return False, "seats must be >= 1"
    try:
        with get_db_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT seats_available FROM flights WHERE flight_id = ?", (flight_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return False, "flight not found"
            conn.execute("UPDATE flights SET seats_available = seats_available + ? WHERE flight_id = ?", (seats, flight_id))
            conn.commit()
            return True, None
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, str(e)
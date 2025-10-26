from fastapi import FastAPI, HTTPException, status, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from enum import Enum
import re
import random
import asyncio
from database import get_db_connection, init_db, init_flights_table, reserve_seats, release_seats, get_flight
from datetime import datetime, date, timedelta
import math
import time
from typing import Dict, List, Optional
from collections import defaultdict
from statistics import mean, median
import json
from pydantic import BaseModel, Field
import string
import secrets

app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
demand_simulation = {
    "is_running": False,
    "current_demand_levels": {},  
    "last_update": None,
    "update_interval": 30,  
}
fare_history = defaultdict(list)

class PricePoint(BaseModel):
    timestamp: datetime
    price: float
    base_price: float
    demand_level: float
    seats_available: int
    breakdown: Optional[dict] = None

class FareHistory(BaseModel):
    flight_id: str
    history: List[PricePoint]
    analytics: Optional[dict] = None
def record_price_change(
    flight_id: str,
    price: float,
    base_price: float,
    demand_level: float,
    seats_available: int,
    breakdown: dict | None = None
):
    fare_history[flight_id].append(
        PricePoint(
            timestamp=datetime.now(),
            price=price,
            base_price=base_price,
            demand_level=demand_level,
            seats_available=seats_available,
            breakdown=breakdown
        )
    )
    cutoff = datetime.now() - timedelta(days=7)
    fare_history[flight_id] = [
        point for point in fare_history[flight_id]
        if point.timestamp >= cutoff
    ]
async def simulate_demand_changes():
    while demand_simulation["is_running"]:
        now = datetime.now()
        demand_simulation["last_update"] = now
        for flight in flights_db:
            flight_id = flight.get("flight_id")
            if not flight_id:
                continue
            current_demand = demand_simulation["current_demand_levels"].get(flight_id, random.random())
            change = random.gauss(0, 0.1)
            mean_reversion = 0.5 - current_demand
            new_demand = current_demand + change + (mean_reversion * 0.1)
            new_demand = max(0.0, min(1.0, new_demand))
            hour = now.hour
            if 9 <= hour <= 17:
                new_demand += 0.1
            if hour in [8, 9, 17, 18]:
                new_demand += 0.2
            new_demand = max(0.0, min(1.0, new_demand))
            demand_simulation["current_demand_levels"][flight_id] = new_demand
            seats = flight.get("seats_available", 0)
            if seats > 0:
                booking_chance = new_demand * 0.3
                if random.random() < booking_chance:
                    seats_change = -random.randint(1, min(3, seats))
                    flight["seats_available"] = max(0, seats + seats_change)
        await asyncio.sleep(demand_simulation["update_interval"])
async def start_demand_simulation(background_tasks: BackgroundTasks):
    if not demand_simulation["is_running"]:
        demand_simulation["is_running"] = True
        # schedule the async simulation loop as a background task
        background_tasks.add_task(asyncio.create_task, simulate_demand_changes())
        return {"status": "Demand simulation started"}
    return {"status": "Demand simulation already running"}
async def stop_demand_simulation():
    if demand_simulation["is_running"]:
        demand_simulation["is_running"] = False
        return {"status": "Demand simulation stopped"}
    return {"status": "Demand simulation not running"}

@app.on_event("shutdown")
async def shutdown_event():
    await stop_demand_simulation()

@app.on_event("startup")
async def startup_event():
    init_db()
    # ensure flights table exists and is seeded with in-memory flights_db defaults (only if empty)
    try:
        init_flights_table(seed_flights=flights_db)
    except Exception:
        # do not fail startup if seeding flights fails
        pass
    # ensure booking history table exists
    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS booking_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    booking_id INTEGER,
                    pnr TEXT,
                    event_type TEXT,
                    timestamp TEXT,
                    details TEXT
                )
                """
            )
            conn.commit()
    except Exception:
        # non-fatal: history table creation failed
        pass

@app.get("/")
def read_root():
    return {"message": "Welcome to the flight booking system"}

class SortBy(str, Enum):
    price = "price"
    duration = "duration"

class Order(str, Enum):
    asc = "asc"
    desc = "desc"
def _parse_duration_to_hours(duration_str: str) -> float:
    if not duration_str:
        raise ValueError("empty duration")
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(duration_str))
    if not m:
        raise ValueError(f"Cannot parse duration: {duration_str}")
    val = float(m.group(1))
    if re.search(r"min|minute|mins", duration_str, re.I):
        return val / 60.0
    return val

@app.get("/flights")
def get_all_flights(
    sort_by: SortBy | None = Query(None, description="Sort by 'price' or 'duration'."),
    order: Order = Query(Order.asc, description="Sort order: 'asc' or 'desc'."),
    max_price: float | None = Query(None, description="Optional maximum price to filter flights by (>=0)."),
    travel_date: str | None = Query(None, description="Optional travel date (YYYY-MM-DD) for dynamic pricing."),
    demand_level: float = Query(0.0, description="Demand level (0.0-1.0) for dynamic pricing.", ge=0.0, le=1.0),
    include_price_breakdown: bool = Query(False, description="Include dynamic price calculation breakdown."),
):
    if max_price is not None and max_price < 0:
        raise HTTPException(status_code=400, detail="max_price must be >= 0")
    results = []
    for flight in flights_db:
        try:
            price_info = compute_dynamic_price(
                base_price=float(flight.get("price") or 0.0),
                seats_available=int(flight.get("seats_available", 0)),
                travel_date=travel_date,
                demand_level=demand_level if demand_level is not None else get_flight_demand(flight.get("flight_id", ""))
            )
            flight_copy = dict(flight)
            flight_copy["base_price"] = flight_copy.get("price")
            flight_copy["price"] = price_info["final_price"]
            if include_price_breakdown:
                flight_copy["price_breakdown"] = price_info["breakdown"]
            results.append(flight_copy)
        except ValueError as e:
            continue
    if max_price is not None:
        filtered = []
        for f in results:
            price = f.get("price")
            if isinstance(price, (int, float)) and price <= max_price:
                filtered.append(f)
        results = filtered
    if sort_by is not None:
        reverse = order == Order.desc
        if sort_by == SortBy.price:
            results.sort(key=lambda f: (float('inf') if not isinstance(f.get("price"), (int, float)) else f["price"]), reverse=reverse)
        elif sort_by == SortBy.duration:
            try:
                results.sort(key=lambda f: _parse_duration_to_hours(f.get("duration", "")), reverse=reverse)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
    return results


@app.get("/flights/{flight_id}")
def get_flight_info(flight_id: str):
    return{
        "flight_no":flight_id,
        "Origin":"NewYork",
        "Destination":"London",
        "Duration":"7hours",
        "Price": 9000,
        "seats_available": 50
    }

@app.get("/flights/{flight_id}/{origin}/{destination}/{duration}")
def get_flight_details(flight_id: str, origin: str, destination: str, duration: str ):
    return{
        "search" : f"Flight {flight_id} from {origin} to {destination} taking {duration} ",
        "flights_found": 4,
        "flights":[
            {"flight_id" : "AI-201", "price" : 9000, "duration":"7hours"},
            {"flight_id" : "AI-202", "price" : 8500, "duration":"7hours"},
            {"flight_id" : "AI-203", "price" : 9200, "duration":"6.5hours"},
            {"flight_id" : "AI-204", "price" : 8800, "duration":"7hours"}
        ]
    }

@app.get("/search")
def search_with_filters(
    origin: str,
    destination: str,
    date: str = Query(None, description="Travel date (YYYY-MM-DD)"),
    price_limit: float = Query(None, description="Maximum price limit"),
    demand_level: float | None = Query(None, description="Optional override for demand level (0.0-1.0). If not provided, uses simulated demand.", ge=0.0, le=1.0),
    include_price_breakdown: bool = Query(False, description="Include dynamic price calculation breakdown"),
):
    all_flights = get_all_flights(
        travel_date=date,
        demand_level=demand_level,
        include_price_breakdown=include_price_breakdown,
    )
    matches = []
    for flight in all_flights:
        if (flight.get("origin", "").lower() == origin.lower() and
            flight.get("destination", "").lower() == destination.lower() and
            (price_limit is None or flight.get("price", float("inf")) <= price_limit)):
            matches.append(flight) 
    return {
        "search_criteria": {
            "origin": origin,
            "destination": destination,
            "date": date if date else "any",
            "price_limit": price_limit,
            "demand_level": demand_level,
        },
        "matches_found": len(matches),
        "flights": matches
    }

@app.get("/flights/filters")
def filter_flights(max_price: int=10000, direct_only: bool = False, airline_name: str = None):
    filters={
        "max_price": max_price,
        "direct_only_flights": direct_only,
        "airline_name": airline_name if airline_name else "All Airlines"
    }
    return {
        "applied_filters": filters,
        "results" : "Filtered flight would be displayed here......"
    }
    
@app.get("/airline_names")
def get_airline_names():
    airline_names= [
        {"flight_id": "AI-201", "airline_name": "Air India"},
        {"flight_id": "AI-202", "airline_name": "British Airways"},
        {"flight_id": "AI-203", "airline_name": "American Airlines"},
        {"flight_id": "AI-204", "airline_name": "Delta Airlines"}
    ]
    return airline_names

@app.get("/booking")
def fetch_all_bookings():
    booking_list = [
        {"booking_id": 101, "passenger_fullname": "simth tae", "seat_no": "12A"},
        {"booking_id": 102, "passenger_fullname": "john doe",  "seat_no": "14B"},
        {"booking_id": 103, "passenger_fullname": "jane doe", "seat_no": "16C"},
        {"booking_id": 104, "passenger_fullname": "alice smith", "seat_no": "18D"}
    ]
    return booking_list

@app.get("/booking/{booking_id}")
def get_booking_info(booking_id: int):
    if booking_id == 101:
        return {
            "booking_id": booking_id,
            "passenger_fullname": "simth tae",
            "flight_id": "AI-201",
            "seat_no": "12A",
            "status": "confirmed"
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Booking with ID {booking_id} not found."
        )
    
@app.get("/status")
def get_flight_status(flight_id: str):
    return {
        "flight_id": flight_id,
        "status": "on time",
        "estimated_departure": "2025-10-15T10:00:00Z",
        "estimated_arrival": "2025-10-15T17:00:00Z"
    }

@app.get("/reviews/{flight_id}")
def get_flight_reviews(flight_id: str):
    return {
        "flight_id": flight_id,
        "reviews": [
            {"user": "alice", "rating": 5, "comment": "Great flight! Very comfortable."},
            {"user": "bob", "rating": 4, "comment": "Good service but a bit delayed."},
            {"user": "charlie", "rating": 3, "comment": "Average experience."}
        ]
    }

@app.get("/reviews")
def get_all_flight_reviews():
    return [
        {
            "flight_id": "AI-210",
            "reviews": [
                {"user": "alice", "rating": 5, "comment": "Great flight! very comfortable."},
                {"user": "bob", "rating": 4, "comment": "Good service but a bit delayed."}
            ]
        },
        {
            "flight_id": "AI-220",
            "reviews": [
                {"user": "charlie", "rating": 3, "comment": "Average experience."},
                {"user": "david", "rating": 3, "comment": "could be better."}
            ]
        }
    ]


@app.get("/amenities/{flight_id}")
def get_flight_amenities(flight_id: str):
    return {
        "flight_id": flight_id,
        "amenities": [
            "In-flight WiFi",
            "Extra Legroom Seats",
            "Complimentory Meals",
            "Entertainment System"
        ]
    }

@app.get("/amenities")
def get_all_flight_amenities():
    return [
        {
            "flight_id": "AI-210",
            "amenities": [
                "In-flight WiFi",
                "Extra Legroom Seats",
                "Complimentory Meals",
                "Entertainment System"
            ]
        },
        {
            "flight_id": "AI-220",
            "amenities": [
                "In-flight WiFi",
                "Complimentory Meals",
                "Entertainment System"
            ]
        }
    ]

@app.get("/special_offers")
def get_special_offers():
    return {
        "special_offers": [
            {"offer_id": "OFF-101", "description": "10% off on round-trip bookings", "valid until": "2025-12-31"},
            {"offer_id": "OFF-102", "description": "15% off for early bird bookings", "valid until": "2025-11-30"},
            {"offer_id": "OFF-103", "description": "5% off for students", "valid until": "2026-01-31"}
        ]
    }

@app.get("/special_offers/{offer_id}")
def get_offer_details(offer_id: str):
    offers = {
        "OFF-101": {"description": "10% off on round-trip bookings", "valid until": "2025-12-31"},
        "OFF-102": {"description": "15% off for early bird bookings", "valid until": "2025-11-30"},
        "OFF-103": {"description": "5% off for students", "valid until": "2026-01-31"}
    }
    if offer_id in offers:
        return {"offer_id": offer_id, **offers[offer_id]}
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"offer with ID {offer_id} not found."
        )


class Passenger(BaseModel):
    full_name: str
    last_name: str
    age: int
    phone: int
    passport_no: str

class BookingRequest(BaseModel):
    flight_id: str
    passenger: Passenger
    depature_city: str
    travel_date: date
    seat_no: Optional[str] = 'Any'
    

# Booking model moved above endpoint for correct reference
class Booking(BaseModel):
    booking_id: Optional[int] = Field(None, description="Unique booking identifier")
    flight_id: str = Field(..., description="ID of the flight")
    pnr: Optional[str] = Field(None, description="Booking PNR")
    passenger_name: Optional[str] = Field(None, description="Passenger's full name")
    passenger_email: Optional[str] = Field(None, description="Passenger's email address")
    passenger_phone: Optional[str] = Field(None, description="Passenger's phone number")
    seats: int = Field(..., description="Number of seats booked")
    status: str = Field(..., description="Booking status (confirmed, cancelled, pending)")
    price: float = Field(..., description="Total price of the booking")

@app.post("/bookings", response_model=Booking)
async def create_booking(booking: Booking):
    # generate a final PNR for this direct create flow
    final_pnr = generate_unique_pnr()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bookings
            (pnr, flight_id, passenger_name, passenger_email, passenger_phone, seats, status, price)
            VALUES ("AI-301","Valli Tade","valli@example.com","8907654321",1,"confirmed",8500.00)
        """, (
            final_pnr,
            booking.flight_id,
            booking.passenger_name,
            booking.passenger_email,
            booking.passenger_phone,
            booking.seats,
            booking.status,
            booking.price
        ))
        conn.commit()
        booking.booking_id = cursor.lastrowid
        booking.pnr = final_pnr
        return booking

@app.get("/bookings/{booking_id}", response_model=Booking)
async def get_booking(booking_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        result = cursor.execute(
            "SELECT * FROM bookings WHERE booking_id = ?",
            (booking_id,)
        ).fetchone()
        
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Booking with ID {booking_id} not found"
            )  
        return Booking(**dict(result))


@app.get("/bookings/pnr/{pnr}", response_model=Booking)
async def get_booking_by_pnr(pnr: str):
    """Fetch a booking by PNR. Checks persisted DB first, then temporary in-memory bookings."""
    # check persisted bookings
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE pnr = ?", (pnr,)).fetchone()
        if row:
            return Booking(**dict(row))


        @app.get("/bookings/{booking_id}/history")
        async def get_booking_history(booking_id: int):
            """Return history events for a given persisted booking_id."""
            with get_db_connection() as conn:
                cursor = conn.cursor()
                rows = cursor.execute("SELECT id, booking_id, pnr, event_type, timestamp, details FROM booking_history WHERE booking_id = ? ORDER BY id ASC", (booking_id,)).fetchall()
                events = []
                for r in rows:
                    rec = dict(r)
                    # parse details JSON if present
                    try:
                        rec_details = json.loads(rec.get("details") or "{}")
                    except Exception:
                        rec_details = rec.get("details")
                    events.append({
                        "id": rec.get("id"),
                        "booking_id": rec.get("booking_id"),
                        "pnr": rec.get("pnr"),
                        "event_type": rec.get("event_type"),
                        "timestamp": rec.get("timestamp"),
                        "details": rec_details,
                    })
                return {"booking_id": booking_id, "events": events}

    # fallback to temporary in-memory bookings
    tb = next((b for b in bookings_db if (b.get("pnr") == pnr or b.get("pnr") == pnr.upper())), None)
    if tb:
        data = {
            "booking_id": tb.get("booking_id"),
            "pnr": tb.get("pnr"),
            "flight_id": tb.get("flight_id"),
            "passenger_name": tb.get("passenger", {}).get("full_name") if tb.get("passenger") else None,
            "passenger_email": tb.get("passenger", {}).get("passport_no") if tb.get("passenger") else None,
            "passenger_phone": str(tb.get("passenger", {}).get("phone")) if tb.get("passenger") else None,
            "seats": tb.get("seats"),
            "status": tb.get("status"),
            "price": tb.get("total_price")
        }
        return Booking(**data)

    raise HTTPException(status_code=404, detail=f"Booking with PNR {pnr} not found")


@app.get("/bookings/search")
async def search_bookings(name: Optional[str] = None, email: Optional[str] = None, limit: int = 50):
    """Search persisted and temporary bookings by passenger name and/or email (case-insensitive, substring).
    At least one of name or email may be provided. If both are None, returns most recent persisted bookings (limited).
    """
    results: list[Booking] = []

    name_param = f"%{name}%" if name else None
    email_param = f"%{email}%" if email else None

    # search persisted bookings in DB
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if name_param is None and email_param is None:
            rows = cursor.execute("SELECT * FROM bookings ORDER BY booking_id DESC LIMIT ?", (limit,)).fetchall()
        else:
            # use lower(...) for case-insensitive match
            query = "SELECT * FROM bookings WHERE (? IS NULL OR lower(passenger_name) LIKE lower(?)) AND (? IS NULL OR lower(passenger_email) LIKE lower(?)) ORDER BY booking_id DESC LIMIT ?"
            rows = cursor.execute(query, (name_param, name_param, email_param, email_param, limit)).fetchall()
        for r in rows:
            results.append(dict(r))

    # search temporary in-memory bookings
    seen_pnrs = {b.pnr for b in results if b.pnr}
    for tb in bookings_db:
        p = tb.get("passenger")
        if not p:
            continue
        match = False
        if name:
            if name.lower() in (p.get("full_name") or "").lower():
                match = True
        if email and not match:
            # temporary bookings don't have passenger_email field; check passport_no as a fallback
            if email.lower() in (p.get("passport_no") or "").lower():
                match = True
        if (name is None and email is None):
            # if no filters given, skip temp bookings (we already returned persisted recent)
            break
        if match:
            pnr = tb.get("pnr")
            if pnr in seen_pnrs:
                continue
            seen_pnrs.add(pnr)
            data = {
                "booking_id": tb.get("booking_id"),
                "pnr": tb.get("pnr"),
                "flight_id": tb.get("flight_id"),
                "passenger_name": p.get("full_name"),
                "passenger_email": p.get("passport_no"),
                "passenger_phone": str(p.get("phone")) if p.get("phone") is not None else None,
                "seats": tb.get("seats"),
                "status": tb.get("status"),
                "price": tb.get("total_price")
            }
            results.append(data)

    return results

@app.get("/bookings", response_model=List[Booking])
async def list_bookings(limit: int = 10, offset: int = 0):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        results = cursor.execute(
            "SELECT * FROM bookings ORDER BY booking_id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        return [Booking(**dict(row)) for row in results]

@app.delete("/bookings/{booking_id}")
async def cancel_persisted_booking(booking_id: int):
    """Cancel a persisted booking: mark as cancelled and release seats back to flights_db."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM bookings WHERE booking_id = ?", (booking_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Booking {booking_id} not found")
        booking = dict(row)
        if booking.get("status") == "cancelled":
            return {"booking_id": booking_id, "status": "already_cancelled"}

        # update DB status
        cursor.execute("UPDATE bookings SET status = ? WHERE booking_id = ?", ("cancelled", booking_id))
        conn.commit()

    # release seats in DB (transactional) and in-memory flights_db if matching flight exists
    flight_id = booking.get("flight_id")
    seats = int(booking.get("seats", 0))
    ok, err = release_seats(flight_id, seats)
    if not ok:
        # if DB release failed (e.g. flight not present), still try to release in-memory
        flight = next((f for f in flights_db if f.get("flight_id") == flight_id), None)
        if flight:
            flight["seats_available"] = flight.get("seats_available", 0) + seats
    else:
        # DB updated; keep in-memory in sync
        flight = next((f for f in flights_db if f.get("flight_id") == flight_id), None)
        if flight:
            flight["seats_available"] = flight.get("seats_available", 0) + seats

    # simulate refund: currently full refund of the booking price (could be prorated in future)
    refund_amount = float(booking.get("price") or 0.0)

    # record cancellation event in booking_history
    try:
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO booking_history (booking_id, pnr, event_type, timestamp, details) VALUES (?, ?, ?, ?, ?)",
                (
                    booking_id,
                    booking.get("pnr"),
                    "cancelled",
                    datetime.now().isoformat(),
                    json.dumps({"seats_released": seats, "refund_amount": refund_amount}),
                ),
            )
            conn.commit()
    except Exception:
        # non-fatal: history recording failed
        pass

    return {"booking_id": booking_id, "status": "cancelled", "seats_released": seats, "refund_amount": refund_amount}


@app.patch("/bookings/{booking_id}", response_model=Booking)
async def update_persisted_booking(booking_id: int, update: Booking):
    """Basic update: allows updating passenger info and status (except booking_id)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = cursor.execute("SELECT * FROM bookings WHERE booking_id = ?", (booking_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Booking {booking_id} not found")
        current = dict(row)

        # prepare values to update; only allow specific fields
        allowed = {"passenger_name", "passenger_email", "passenger_phone", "status"}
        updates = {}
        for field in allowed:
            val = getattr(update, field, None)
            if val is not None:
                updates[field] = val

        if updates:
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            params = list(updates.values()) + [booking_id]
            cursor.execute(f"UPDATE bookings SET {set_clause} WHERE booking_id = ?", params)
            conn.commit()

        row = cursor.execute("SELECT * FROM bookings WHERE booking_id = ?", (booking_id,)).fetchone()
        return Booking(**dict(row))
    
class Flight(BaseModel):
    flight_id: str
    origin: str
    destination: str
    duration: str
    price: float
    seats_available: int

class BookingResponse(BaseModel):
    booking_id: int
    flight_id: str
    passenger_name: str
    seat_no: str
    status: str

class FlightStatusResponse(BaseModel):
    flight_id: str
    status: str
    estimated_departure: str
    estimated_arrival: str

class Review(BaseModel):
    user: str
    rating: int
    comment: str

class FlightReviewsResponse(BaseModel):
    flight_id: str
    reviews: list[Review]

class AmenitiesResponse(BaseModel):
    flight_id: str
    amenities: list[str]

class SpecialOffer(BaseModel):
    offer_id: str
    description: str
    valid_until: str

class SpecialOffersResponse(BaseModel):
    special_offer: list[SpecialOffer]

class SpecialOfferDetailsResponse(BaseModel):
    offer_id: str
    description: str
    valid_until: str

@app.post("/register")
def register_user(username: str, password: str, email: str):
    return {
        "message": f"User {username} registered successfully",
        "email": email,
        "password": password
    } 
  
@app.post("/passengers")
def create_passenger(passenger: Passenger):
    return {
        "message": "Passenger created successfully",
        "passenger_details": passenger,
        "passenger_id": 501,
        "data" : passenger
    } 

@app.post("/bookings")
def create_booking(booking: BookingRequest):
    return {
        "message": "Booking created successfully",
        "booking_details": booking,
        "booking_id": 1001
    }  

@app.post("/bookings/create_with_pnr")
def create_booking_with_pnr(booking: BookingRequest):
    PNR = "PNR" + "98735463273"
    return {
        "status": "Success",
        "message": "Booking created successfully",
        "pnr": PNR,
        "booking_details": {
            "flight_id": booking.flight_id,
            "passenger_name": booking.passenger.full_name,
            "depature_city": booking.depature_city,
            "travel_date": booking.travel_date,
            "seat_no": booking.seat_no,
            "price": 9000,
            "booking_id": 1001
        }
    }

@app.post("/flights")
def add_flight(flight: Flight):
    return {
        "message": "Flight added successfully",
        "flight_details": flight,
        "flight_id": flight.flight_id
    }

@app.post("/flights/status")
def update_flight_status(flight_status: FlightStatusResponse):
    return {
        "message": "Flight status updated successfully",
        "flight_status": flight_status,
        "flight_id": flight_status.flight_id
    }

@app.post("/flights/reviews")
def add_flight_review(flight_review: FlightReviewsResponse):
    return {
        "message": "Flight review added successfully",
        "flight_review": flight_review,
        "flight_id": flight_review.flight_id
    }

@app.post("/flights/amenities")
def add_flight_amenities(amenities: AmenitiesResponse):
    return {
        "message": "Flight amenities added successfully",
        "amenities": amenities,
        "flight_id": amenities.flight_id
    }

@app.post("/special_offers")
def add_special_offer(offer: SpecialOffer):
    return {
        "message": "Special offer added successfully",
        "offer": offer,
        "offer_id": offer.offer_id
    }
@app.post("/special_offers/{offer_id}")
def update_special_offer(offer_id: str, offer: SpecialOfferDetailsResponse):
    return {
        "message": "Special offer updated successfully",
        "offer_id": offer_id,
        "offer_details": offer
    }

@app.post("/admin/flights")
def add_flights(flight: Flight):
    return {
        "message": "Flight details added successfully",
        "flight_id": 101,
        "flight_details": flight
    }

@app.post("/flights/{flight_id}/book")
def book_specific_flight(flight_id: str, passenger: Passenger):
    return {
        "message": "Booking flight {flight_id}",
        "passenger": passenger.full_name,
        "status": "Confirmed"
    }

@app.post("/booking/create", status_code = status.HTTP_201_CREATED)
def create_booking_with_status(booking: BookingRequest):
    return {
        "message": "Booking created",
        "booking_id" : 8765
    }

@app.get("/sample/passenger")
def get_passenger_sample():
    return {
        "first_name": "Goala",
        "last_name": "Doet",
        "age": 29,
        "phone": 9876543210
    }

@app.get("/smaple/bookings")
def get_booking_sample():
    return {
        "flight_id" : "AI1",
        "passenger": {
            "first_name": "smith",
            "last_name": "tae",
            "age":26,
            "phone":9087654321
        },
        "travel_date": "2025-10-11",
        "seat_no": "12A",
        "seat_preference": "Window"
    }

flights_db= [
    {
        "flight_id": "AI-201",
        "origin": "NewYork",
        "destination": "London",
        "duration": "7hours",
        "price": 9000.0,
        "seats_available": 50
    },
    {
        "flight_id": "AI-202",
        "origin": "NewYork",
        "destination": "London",
        "duration": "6.5hours",
        "price": 8500.0,
        "seats_available": 45
    }
]
bookings_db= []
booking_counter= 1000

# Models and helpers for multi-step booking flow
class StartBookingRequest(BaseModel):
    flight_id: str
    seats: int = 1

class PaymentRequest(BaseModel):
    payment_method: Optional[str] = "card"
    # probability of failure (0.0 - 1.0). Higher means more likely to fail.
    fail_rate: float = 0.1

class TempBookingResponse(BaseModel):
    pnr: str
    flight_id: str
    seats: int
    total_price: float
    status: str

def _generate_pnr() -> str:
    global booking_counter
    booking_counter += 1
    # simple incremental temporary PNR for reservations
    return f"TMP{booking_counter}"


def _generate_final_pnr(length: int = 6) -> str:
    """Generate a random alphanumeric PNR of given length."""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_unique_pnr(db_conn_getter=get_db_connection, length: int = 6, max_attempts: int = 50) -> str:
    """Generate a PNR and ensure it is unique in the bookings table."""
    for _ in range(max_attempts):
        candidate = _generate_final_pnr(length)
        with db_conn_getter() as conn:
            row = conn.execute("SELECT 1 FROM bookings WHERE pnr = ?", (candidate,)).fetchone()
            if row is None:
                return candidate
    # fallback to a longer PNR using timestamp/secret
    return f"PNR{int(time.time())}{secrets.token_hex(3).upper()}"


@app.post("/booking_flow/start", response_model=TempBookingResponse)
def booking_flow_start(req: StartBookingRequest):
    # validate flight
    # attempt to use transactional DB reservation first (safer for concurrency)
    db_flight = get_flight(req.flight_id)
    if db_flight:
        if req.seats <= 0:
            raise HTTPException(status_code=400, detail="seats must be >= 1")
        if int(db_flight.get("seats_available", 0)) < req.seats:
            raise HTTPException(status_code=400, detail="Not enough seats available")

        ok, err = reserve_seats(req.flight_id, req.seats)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Unable to reserve seats: {err}")

        # reflect change in in-memory flights_db for UI endpoints
        flight = next((f for f in flights_db if f.get("flight_id") == req.flight_id), None)
        if flight:
            flight["seats_available"] = max(0, flight.get("seats_available", 0) - req.seats)

        # compute price using DB seat snapshot
        price_info = compute_dynamic_price(base_price=float(db_flight.get("price") or 0.0), seats_available=int(db_flight.get("seats_available", 0)) - req.seats)
        total_price = price_info["final_price"] * req.seats
    else:
        # fallback to in-memory reservation
        flight = next((f for f in flights_db if f.get("flight_id") == req.flight_id), None)
        if flight is None:
            raise HTTPException(status_code=404, detail=f"Flight {req.flight_id} not found")
        if req.seats <= 0:
            raise HTTPException(status_code=400, detail="seats must be >= 1")
        if flight.get("seats_available", 0) < req.seats:
            raise HTTPException(status_code=400, detail="Not enough seats available")

        # compute price (use existing dynamic pricing if available)
        price_info = compute_dynamic_price(base_price=float(flight.get("price") or 0.0), seats_available=int(flight.get("seats_available", 0)))
        total_price = price_info["final_price"] * req.seats

        # reserve seats in-memory
        flight["seats_available"] = max(0, flight.get("seats_available", 0) - req.seats)

    pnr = _generate_pnr()
    temp = {
        "pnr": pnr,
        "flight_id": req.flight_id,
        "seats": req.seats,
        "per_seat_price": price_info["final_price"],
        "total_price": total_price,
        "status": "reserved",
        "passenger": None,
    }
    bookings_db.append(temp)
    return TempBookingResponse(pnr=pnr, flight_id=req.flight_id, seats=req.seats, total_price=total_price, status="reserved")


@app.post("/booking_flow/{pnr}/passenger")
def booking_flow_passenger(pnr: str, passenger: Passenger):
    tb = next((b for b in bookings_db if b.get("pnr") == pnr.upper() or b.get("pnr") == pnr), None)
    if tb is None:
        raise HTTPException(status_code=404, detail=f"Temporary booking {pnr} not found")
    # use Pydantic v2 API .model_dump() instead of deprecated .dict()
    tb["passenger"] = passenger.model_dump()
    tb["status"] = "pending_payment"
    return {"pnr": tb["pnr"], "status": tb["status"]}


@app.post("/booking_flow/{pnr}/pay")
def booking_flow_pay(pnr: str, payment: PaymentRequest):
    tb = next((b for b in bookings_db if b.get("pnr") == pnr.upper() or b.get("pnr") == pnr), None)
    if tb is None:
        raise HTTPException(status_code=404, detail=f"Temporary booking {pnr} not found")
    if tb.get("status") == "confirmed":
        return {"pnr": tb["pnr"], "status": "already_confirmed"}

    # simulate payment
    if random.random() < payment.fail_rate:
        # payment failed: release seats
        # try to release via DB if present
        ok, err = release_seats(tb.get("flight_id"), tb.get("seats", 0))
        if not ok:
            # fallback to in-memory release if DB release failed or flight not present
            flight = next((f for f in flights_db if f.get("flight_id") == tb.get("flight_id")), None)
            if flight:
                flight["seats_available"] = flight.get("seats_available", 0) + tb.get("seats", 0)
        tb["status"] = "failed"
        return {"pnr": tb["pnr"], "status": "payment_failed"}

    # payment succeeded: persist to SQLite bookings table
    # generate unique final PNR and persist
    final_pnr = generate_unique_pnr()
    tb["pnr"] = final_pnr
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bookings
            (pnr, flight_id, passenger_name, passenger_email, passenger_phone, seats, status, price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            final_pnr,
            tb.get("flight_id"),
            tb.get("passenger", {}).get("full_name") if tb.get("passenger") else "",
            tb.get("passenger", {}).get("passport_no") if tb.get("passenger") else "",
            str(tb.get("passenger", {}).get("phone")) if tb.get("passenger") else "",
            tb.get("seats"),
            "confirmed",
            tb.get("total_price")
        ))
        conn.commit()
        booking_id = cursor.lastrowid

    tb["status"] = "confirmed"
    tb["booking_id"] = booking_id
    return {"pnr": tb["pnr"], "status": "confirmed", "booking_id": booking_id}


@app.get("/booking_flow/{pnr}")
def booking_flow_status(pnr: str):
    tb = next((b for b in bookings_db if b.get("pnr") == pnr.upper() or b.get("pnr") == pnr), None)
    if tb is None:
        raise HTTPException(status_code=404, detail=f"Temporary booking {pnr} not found")
    return tb

@app.get("/health")
def health_check():
    return {
        "status" : "healthy",
        "total_flights" : len(flights_db),
        "total_bookings" : len(bookings_db)
    }

@app.delete("/bookings/{pnr}")
def cancel_booking(pnr : str):
    for i, booking in enumerate(bookings_db):
        if booking["pnr"] == pnr.upper():
            for flights in flights_db:
                if flights.get("flight_id") == booking.get("flight_id"):
                   flights["seats_available"] += 1
                break
            cancel= bookings_db.pop(i)
            return {
                "message": "Booking cancelled successfully",
                "cancelled_booking": cancel
            }
    raise HTTPException(status_code=404, detail=f"Booking with PNR {pnr} not found")

@app.get("/flights/{flight_id}/fare-history")
def get_fare_history(
    flight_id: str,
    hours: int = Query(24, description="Hours of history to return", ge=1, le=168),
    include_breakdown: bool = Query(False, description="Include price calculation breakdown in results"),
) -> FareHistory:
    if flight_id not in fare_history:
        raise HTTPException(status_code=404, detail=f"No fare history found for flight {flight_id}")
    cutoff = datetime.now() - timedelta(hours=hours)
    history = [
        point for point in fare_history[flight_id]
        if point.timestamp >= cutoff
    ]
    if not include_breakdown:
        for point in history:
            point.breakdown = None
    if history:
        prices = [p.price for p in history]
        demands = [p.demand_level for p in history]
        seats = [p.seats_available for p in history]
        analytics = {
            "price": {
                "min": min(prices),
                "max": max(prices),
                "avg": round(mean(prices), 2),
                "median": round(median(prices), 2),
            },
            "demand": {
                "min": min(demands),
                "max": max(demands),
                "avg": round(mean(demands), 3),
            },
            "seats_available": {
                "min": min(seats),
                "max": max(seats),
                "last": seats[-1],
            },
            "total_changes": len(history),
            "period_hours": hours,
        }
    else:
        analytics = None
    return FareHistory(
        flight_id=flight_id,
        history=history,
        analytics=analytics
    )

@app.get("/flights/{flight_id}/price-alerts")
def get_price_alerts(
    flight_id: str,
    threshold_percent: float = Query(10.0, description="Alert threshold percentage", ge=0.1),
    hours: int = Query(24, description="Hours to analyze", ge=1, le=168),
):
    if flight_id not in fare_history:
        raise HTTPException(status_code=404, detail=f"No fare history found for flight {flight_id}")
    cutoff = datetime.now() - timedelta(hours=hours)
    history = [
        point for point in fare_history[flight_id]
        if point.timestamp >= cutoff
    ]
    if not history:
        return {"alerts": [], "threshold_percent": threshold_percent}
    alerts = []
    baseline = history[0].price
    for i, point in enumerate(history[1:], 1):
        pct_change = ((point.price - baseline) / baseline) * 100
        if abs(pct_change) >= threshold_percent:
            alerts.append({
                "timestamp": point.timestamp,
                "old_price": baseline,
                "new_price": point.price,
                "percent_change": round(pct_change, 2),
                "demand_level": point.demand_level,
                "seats_available": point.seats_available
            })
            baseline = point.price
    return {
        "alerts": alerts,
        "threshold_percent": threshold_percent,
        "total_changes": len(history) - 1,
        "significant_changes": len(alerts)
    }

@app.post("/simulation/start")
async def start_simulation(background_tasks: BackgroundTasks):
    return await start_demand_simulation(background_tasks)

@app.post("/simulation/stop")
async def stop_simulation():
    return await stop_demand_simulation()

@app.get("/simulation/status")
async def get_simulation_status():
    return {
        "is_running": demand_simulation["is_running"],
        "last_update": demand_simulation["last_update"].isoformat() if demand_simulation["last_update"] else None,
        "update_interval_seconds": demand_simulation["update_interval"],
        "demand_levels": demand_simulation["current_demand_levels"],
    }
def get_flight_demand(flight_id: str) -> float:
    return demand_simulation["current_demand_levels"].get(flight_id, 0.5)
def compute_dynamic_price(
    base_price: float,
    seats_available: int,
    total_seats: int | None = None,
    travel_date: str | None = None,
    demand_level: float = 0.0,
    pricing_tiers: dict | None = None,
):    
    if base_price <= 0:
        raise ValueError("base_price must be > 0")
    if seats_available < 0:
        raise ValueError("seats_available must be >= 0")
    if total_seats is None:
        total_seats = 100
    if total_seats <= 0:
        raise ValueError("total_seats must be > 0")
    if seats_available > total_seats:
        seats_available = total_seats
    demand_level = max(0.0, min(1.0, float(demand_level)))
    seats_remaining_pct = (seats_available / total_seats) * 100.0
    default_tiers = {
        10: 2.0,   
        20: 1.5,   
        50: 1.2,   
        100: 1.0,  
    }
    tiers = pricing_tiers or default_tiers
    tier_multiplier = 1.0
    try:
        sorted_thresholds = sorted((int(k), float(v)) for k, v in tiers.items())
    except Exception:
        sorted_thresholds = sorted((int(k), float(v)) for k, v in default_tiers.items())
    for thresh, mult in sorted_thresholds:
        if seats_remaining_pct <= thresh:
            tier_multiplier = mult
            break
    days_until = None
    if travel_date:
        try:
            dep_date = date.fromisoformat(travel_date)
        except Exception:
            dep_date = datetime.fromisoformat(travel_date).date()
        today = date.today()
        delta_days = (dep_date - today).days
        days_until = delta_days
    else:
        days_until = None
    if days_until is None:
        time_multiplier = 1.0
    elif days_until > 30:
        time_multiplier = 0.95  
    elif 7 < days_until <= 30:
        time_multiplier = 1.0
    elif 2 <= days_until <= 7:
        time_multiplier = 1.2
    elif days_until < 2:
        time_multiplier = 1.5
    demand_multiplier = 1.0 + (0.5 * demand_level)
    combined_multiplier = tier_multiplier * time_multiplier * demand_multiplier
    min_price = base_price * 0.8
    max_price = base_price * 3.0
    raw_price = base_price * combined_multiplier
    final_price = round(max(min_price, min(max_price, raw_price)), 2)
    breakdown = {
        "base_price": base_price,
        "seats_available": seats_available,
        "total_seats": total_seats,
        "seats_remaining_percentage": round(seats_remaining_pct, 2),
        "tier_multiplier": tier_multiplier,
        "time_multiplier": time_multiplier,
        "demand_multiplier": round(demand_multiplier, 3),
        "combined_multiplier": round(combined_multiplier, 3),
        "raw_price": round(raw_price, 2),
        "min_price": round(min_price, 2),
        "max_price": round(max_price, 2),
    }
    return {"final_price": final_price, "breakdown": breakdown, "demand_level": demand_level}


@app.get("/flights/{flight_id}/price")
def get_dynamic_price(
    flight_id: str,
    travel_date: str | None = None,
    demand_level: float = 0.0,
    base_price: float | None = None,
    total_seats: int | None = None,
): 
    flight = next((f for f in flights_db if f.get("flight_id") == flight_id), None)
    if not flight:
        raise HTTPException(status_code=404, detail=f"Flight {flight_id} not found")
    bp = base_price if base_price is not None else float(flight.get("price") or 0.0)
    seats_avail = int(flight.get("seats_available", 0))
    total = total_seats if total_seats is not None else None
    try:
        result = compute_dynamic_price(bp, seats_avail, total, travel_date, demand_level)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    record_price_change(
        flight_id=flight_id,
        price=result["final_price"],
        base_price=bp,
        demand_level=result["demand_level"],
        seats_available=seats_avail,
        breakdown=result["breakdown"]
    )
    return {"flight_id": flight_id, **result}
def _generate_airline_a_schedules(origin: str | None, destination: str | None, date: str | None, limit: int) -> list:
    items = []
    for i in range(1, limit + 1):
        items.append({
            "flight_no": f"A-A{100 + i}",
            "from": origin or "CityX",
            "to": destination or "CityY",
            "departure_time": f"2025-11-{(i%28)+1:02d}T0{(i%12)+1}:00:00Z",
            "arrival_time": f"2025-11-{(i%28)+1:02d}T1{(i%12)+1}:00:00Z",
            "duration": f"{6 + (i%3)}hours",
            "price": 8000.0 + (i * 100),
            "seats": 50 - i
        })
    return items
def _generate_airline_b_schedules(origin: str | None, destination: str | None, date: str | None, limit: int) -> list:
    items = []
    for i in range(1, limit + 1):
        items.append({
            "id": f"B-B{200 + i}",
            "route": {"src": origin or "CityX", "dst": destination or "CityY"},
            "dept": f"2025-11-{(i%28)+1:02d}T1{(i%12)+0}:30:00Z",
            "arr": f"2025-11-{(i%28)+1:02d}T2{(i%12)+0}:30:00:00Z",
            "duration_mins": 360 + (i * 10),
            "cost": 8200.0 + (i * 120),
            "available": 40 - i
        })
    return items
def _normalize_to_internal_flight(item: dict, source: str) -> dict:
    if source == "A":
        return {
            "flight_id": item.get("flight_no"),
            "origin": item.get("from"),
            "destination": item.get("to"),
            "duration": item.get("duration"),
            "price": float(item.get("price")) if item.get("price") is not None else None,
            "seats_available": int(item.get("seats")) if item.get("seats") is not None else None
        }
    else:  
        duration_mins = item.get("duration_mins")
        duration_str = f"{duration_mins/60:.1f}hours" if duration_mins is not None else ""
        return {
            "flight_id": item.get("id"),
            "origin": item.get("route", {}).get("src"),
            "destination": item.get("route", {}).get("dst"),
            "duration": duration_str,
            "price": float(item.get("cost")) if item.get("cost") is not None else None,
            "seats_available": int(item.get("available")) if item.get("available") is not None else None
        }

@app.get("/external/airline_a/schedules")
async def external_airline_a_schedules(
    origin: str | None = None,
    destination: str | None = None,
    date: str | None = None,
    limit: int = 5,
    simulate_delay_ms: int = 0,
    fail_rate: float = 0.0,
):
    if limit <= 0 or limit > 50:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 50")
    if not (0.0 <= fail_rate <= 1.0):
        raise HTTPException(status_code=400, detail="fail_rate must be between 0.0 and 1.0")
    if random.random() < fail_rate:
        raise HTTPException(status_code=503, detail="Airline A service unavailable (simulated)")
    if simulate_delay_ms > 0:
        await asyncio.sleep(simulate_delay_ms / 1000.0)
    return _generate_airline_a_schedules(origin, destination, date, limit)

@app.get("/external/airline_b/schedules")
async def external_airline_b_schedules(
    origin: str | None = None,
    destination: str | None = None,
    date: str | None = None,
    limit: int = 5,
    simulate_delay_ms: int = 0,
    fail_rate: float = 0.0,
):
    if limit <= 0 or limit > 50:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 50")
    if not (0.0 <= fail_rate <= 1.0):
        raise HTTPException(status_code=400, detail="fail_rate must be between 0.0 and 1.0")
    if random.random() < fail_rate:
        raise HTTPException(status_code=503, detail="Airline B service unavailable (simulated)")
    if simulate_delay_ms > 0:
        await asyncio.sleep(simulate_delay_ms / 1000.0)
    return _generate_airline_b_schedules(origin, destination, date, limit)

@app.get("/external/aggregate_schedules")
async def external_aggregate_schedules(
    origin: str | None = None,
    destination: str | None = None,
    date: str | None = None,
    limit_per_provider: int = 3,
    simulate_delay_ms: int = 0,
    fail_rate_a: float = 0.0,
    fail_rate_b: float = 0.0,
):
    tasks = [
        external_airline_a_schedules(origin=origin, destination=destination, date=date, limit=limit_per_provider, simulate_delay_ms=simulate_delay_ms, fail_rate=fail_rate_a),
        external_airline_b_schedules(origin=origin, destination=destination, date=date, limit=limit_per_provider, simulate_delay_ms=simulate_delay_ms, fail_rate=fail_rate_b),
    ]
    results = []
    done = await asyncio.gather(*tasks, return_exceptions=True)
    for idx, res in enumerate(done):
        source = "A" if idx == 0 else "B"
        if isinstance(res, Exception):
            results.append({"provider": source, "error": str(res)})
            continue
        for item in res:
            norm = _normalize_to_internal_flight(item, source)
            results.append(norm)
    return results


if __name__ == "__main__":
    # Example usage of Booking model
    booking_example = Booking(
        booking_id=1,
        flight_id=123,
        passenger_name="John Doe",
        passenger_email="john.doe@example.com",
        passenger_phone="123-456-7890",
        seats=2,
        status="confirmed",
        price=499.99
    )
    print(booking_example.json(indent=2))

to run this code type fastapi dev main.py

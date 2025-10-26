CREATE TABLE flights(id INT AUTO_INCREMENT PRIMARY KEY,flight_no VARCHAR(10),Origin VARCHAR(100),Destination VARCHAR(100),deapture DATETIME,arrival DATETIME,base_price DECIMAL(10,2),TOTAL_SEATS INT,SEATS_AVAILABLE INT AIRLINES_NAME VARCHAR(20));
INSERT INTO flights (id,flight_no,origin,destination,deapture,arrival,base_price,total_seats,seats_available )
Values(1,'AI-201','NEW YORK','LONDON','2025-10-10 08:00:00','2025-10-11 10:00:00',9000.00,250,125 ),
      (2,'AI-202','LONDON','NEWYORK','2025-10-12 10:00:00','2025-10-12 08:00:00',9000.00,250,200),
      (3,'AI-203','NEWYORK','PARIS','2025-10-14 09:00:00','2025-10-14 11:30:00',9500.00,200,150),
      ( 4, 'AI-204', 'PARIS', 'NEWYORK', '2025-10-15 12:00:00', '2025-10-15 14:30:00', 9500.00,  200, 180);
SELECT * FROM flights;
SELECT id,
       flight_no, 
       origin,
       destination,
       base_price 
FROM flights;
update flights
Set seats_available = 45
where id = 4;
Delete from flights
where id = 1;
SELECT flight_no,
      base_price
FROM flights
ORDER BY base_price ASC;
Select flight_no,
       origin
FROM flights
ORDER BY origin DESC;
Select flight_no,
       deapture
From flights
ORDER BY deapture DESC;
SELECT * FROM flights
WHERE ORIGIN = 'PARIS'
SELECT flight_no,
      base_price
FROM flights
WHERE base_price > 9200.00;
SELECT flight_no,
    base_price
FROM flights
ORDER BY base_price ASC;
LIMIT 2;
AGGREGATE FUNCTIONS
SELECT COUNT(*) AS total_flights
FROM flights
SELECT AVG(base_price) AS average_price
FROM flights
SELECT SUM(seats_available) AS total_available_seats
FROM flights
WHERE origin = 'PARIS'
SELECT origin,
      AVG(base_price) AS average_price
FROM flights
GROUP BY origin SELECTS Origin,
       AVG(base_price) AS average_price
FROM flights
GROUP BY origin 
HAVING AVG(base_price) < 9200.00;
ALTER TABLE flights
ADD airlines_name VARCHAR(20)
ALTER TABLE flights CHANGE dept deapture DATETIME
CREATE TABLE flights(id INT AUTO_INCREMENT PRIMARY KEY,flight_no VARCHAR(10),Origin VARCHAR(100),Destination VARCHAR(100),dept,arrival DATETIME,base_price DECIMAL(10,2),TOTAL_SEATS INT SEATS_AVAILABLE INT AIRLINES_NAME VARCHAR(20)) 
CREATE TABLE bookings(booking_id INT AUTO_INCREMENT PRIMARY KEY,trans_id INT,flight_no INT,origin VARCHAR(100),dest VARCHAR(100),passenger_full name VARCHAR(100),passenger_contact INT,email_id VARCHAR(100),seat_no INT)
Insert into bookings(booking_id,trans_id,flight_no,passenger_full name,passenger_contact,email_id,seat_no)
Values(1,'ICIS4','AI-201','elsan dore',9876543210,'dore@gmail.com',12),
      (2,'HSUS2','AI-202','john smith',8765432109,'john@gmail.com',20),
      (3,'KDJS8','AI-203','maria garcia',7654321098,'maria@gmail.com',15),
      (4,'PLMN6','AI-204','li wei',6543210987,'wei@gmail.com',36);
SELECT b.passenger_full name,f.flight_no,f.origin,f.destination
FROM booking b
         INNER JOIN flights f ON b.flight_id = f.id;
SELECT f.flight_no,f.destination,b.passenger_full name
FROM flights f
         LEFT JOIN bookings b ON f.id = b.flight_no;
SELECT b.passenger_full name,f.flight_no,f.Origin
FROM bookings b
         RIGHT JOIN  flights f ON b.flight_no = f.id;
SELECT f.flight_no,f.origin,b.passenger_full name
FROM flights f  
         FULL JOIN bookings b ON f.id = b.flight_no;
select SEATS_AVAILABLE
from flights
where flight_no='AI-202';
update the seat available after booking
update flights
set SEATS_AVAILABLE=SEATS_AVAILABLE-1
where flight_no='AI-202';
INSERT Booking:
Insert into bookings(flight_id,passenger_full name,passenger_contact,seat_no)
Values (1,'Alice Johnson',9123456780,45) Commit;
rollback;
-- End of db_schema.sql
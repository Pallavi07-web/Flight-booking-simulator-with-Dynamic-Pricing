let selectedFlight = null;
let bookingDetails = null;

document.getElementById("price").addEventListener("input", function () {
  document.getElementById("priceValue").innerText = this.value;
});

document.getElementById("searchForm").addEventListener("submit", function (e) {
  e.preventDefault();
  showLoader();

  setTimeout(() => {
    const from = document.getElementById("from").value;
    const to = document.getElementById("to").value;
    const airline = document.getElementById("airline").value;
    const maxPrice = parseInt(document.getElementById("price").value);
    const timeSlot = document.getElementById("time").value;

    const flights = [
      { airline: "Air India", flight: "AI202", time: "10:00", price: getPrice() },
      { airline: "IndiGo", flight: "6E305", time: "14:30", price: getPrice() },
      { airline: "SpiceJet", flight: "SG101", time: "19:45", price: getPrice() }
    ];

    const filtered = flights.filter(f => {
      const hour = parseInt(f.time.split(":")[0]);
      return (!airline || f.airline === airline) &&
             f.price <= maxPrice &&
             (!timeSlot ||
              (timeSlot === "morning" && hour < 12) ||
              (timeSlot === "afternoon" && hour >= 12 && hour < 18) ||
              (timeSlot === "evening" && hour >= 18));
    });

    displayResults(filtered, from, to);
    hideLoader();
  }, 1000);
});

function getPrice() {
  return Math.floor(Math.random() * 5000) + 4000;
}

function displayResults(flights, from, to) {
  const results = document.getElementById("results");
  results.innerHTML = "";

  flights.forEach(f => {
    const div = document.createElement("div");
    div.className = "flight-card";
    div.innerHTML = `
      <p><strong>${f.airline}</strong> (${f.flight})</p>
      <p>${from} → ${to}</p>
      <p>Departure: ${f.time}</p>
      <p>Price: ₹${f.price}</p>
      <button onclick='openBooking(${JSON.stringify(f)})'>Book</button>
    `;
    results.appendChild(div);
  });
}

function openBooking(flight) {
  selectedFlight = flight;
  document.getElementById("bookingForm").style.display = "block";
}

function confirmBooking() {
  const name = document.getElementById("passengerName").value;
  const email = document.getElementById("email").value;
  const pnr = "PNR" + Math.floor(Math.random() * 1000000);

  bookingDetails = {
    passenger: name,
    email,
    flight: selectedFlight,
    pnr,
    timestamp: new Date().toISOString()
  };

  localStorage.setItem("booking", JSON.stringify(bookingDetails));
  document.getElementById("pnrDisplay").innerText = `Your PNR: ${pnr}`;
  document.getElementById("bookingForm").style.display = "none";
  document.getElementById("receiptSection").style.display = "block";
}

function downloadReceipt() {
  const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(bookingDetails, null, 2));
  const link = document.createElement("a");
  link.setAttribute("href", dataStr);
  link.setAttribute("download", `booking_${bookingDetails.pnr}.json`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function showLoader() {
  document.getElementById("loader").style.display = "block";
}

function hideLoader() {
  document.getElementById("loader").style.display = "none";
}
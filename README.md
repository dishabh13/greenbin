# GREENBIN v2

Smart waste management system built with Flask and SQLite. The app includes resident pickup requests, AI-assisted waste classification, collector route planning, and admin analytics.

## Quick Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Optional: set your Groq API key
Without a key, the app uses the built-in rule-based classifier.

PowerShell:
```powershell
$env:GROQ_API_KEY="your_key_here"
python app.py
```

### 3. Open the app
```text
http://localhost:5000
```

## Demo Accounts

| Role | Username | Password |
|------|----------|----------|
| Admin | `admin` | `admin123` |
| Collector | `ravi` | `ravi123` |
| Collector | `priya` | `priya123` |
| Resident | `amit` | `amit123` |
| Resident | `sonal` | `sonal123` |

## Main Features

### Resident
- Classify waste from a text description.
- Submit pickup requests with waste amount and AI category.
- View live bin fill status and request history.

### Collector
- View an interactive map of assigned collection trips.
- Generate capacity-aware trips using the configured truck capacity.
- Use exact route search for smaller assigned stop sets and a heuristic fallback for larger ones.
- Keep the current map fixed while a trip is in progress; the route refreshes after that trip is fully completed.
- Recalculate the route after changing truck capacity, when no trip is mid-run.
- Collect bins directly from the planned trip cards.
- See pending requests and collection history.

### Admin
- Track system-wide stats, alerts, and area intelligence.
- Add, edit, and delete bins.
- Assign collectors to bins.
- Review request and collection history.

## Route Planning Logic

- Each collector has a truck capacity stored in `collector_settings`.
- For up to 10 serviceable stops, the planner checks every feasible trip combination and stop order to find the best total route.
- Larger workloads fall back to a Clarke-Wright style grouping heuristic and route ordering.
- If a single bin exceeds truck capacity, the dashboard flags it as an over-capacity trip.
- Active trips are persisted in SQLite so the collector map does not change mid-trip.

## Project Structure

```text
greenbin/
|-- app.py
|-- database.py
|-- route_optimizer.py
|-- requirements.txt
|-- greenbin.db
`-- templates/
    |-- base.html
    |-- login.html
    |-- register.html
    |-- resident_dashboard.html
    |-- collector_dashboard.html
    `-- admin_dashboard.html
```

## Notes

- The app uses a simulated auto-fill thread to increase bin levels over time.
- Routing distances are based on the Haversine formula and the configured depot coordinates.
- The database file is created automatically and seeded with demo data on first run.

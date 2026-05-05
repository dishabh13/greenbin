from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, current_app
from prometheus_flask_exporter import PrometheusMetrics
import logging
from logging.handlers import RotatingFileHandler
from database import init_db, get_db, close_db, haversine, hp
from datetime import datetime
import threading, time, os, requests as req_lib, json
from route_optimizer import plan_collector_routes

app = Flask(__name__)
metrics = PrometheusMetrics(app)
metrics.info('app_info', 'GREENBIN app info', version='1.0.0')

app.config["DATABASE"] = "greenbin.db"
app.secret_key = os.environ.get("SECRET_KEY")
app.teardown_appcontext(close_db)

if not os.path.exists("logs"):
    os.mkdir("logs")

file_handler = RotatingFileHandler(
    "logs/greenbin.log",
    maxBytes=10240,
    backupCount=5
)

file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s: %(message)s"
))

file_handler.setLevel(logging.INFO)

app.logger.addHandler(file_handler)
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)

stream_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s: %(message)s"
))

app.logger.addHandler(stream_handler)
app.logger.setLevel(logging.INFO)

app.logger.info("GREENBIN startup")

GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')

# Depot coordinates (municipal office / default origin for route)
DEPOT_LAT = 12.9716
DEPOT_LNG = 77.5946

# ─── HELPERS ────────────────────────────────────────────────────────────────────

def get_fill_status(current, capacity):
    pct = (current / capacity * 100) if capacity > 0 else 0
    if pct >= 100:  return 'Full',    'danger'
    if pct >= 80:   return 'High',    'warning'
    if pct >= 40:   return 'Medium',  'info'
    return 'Low', 'success'

def enrich_bin(b):
    status, color = get_fill_status(b['current_level'], b['capacity'])
    pct = round((b['current_level'] / b['capacity']) * 100, 1) if b['capacity'] > 0 else 0
    dist = haversine(DEPOT_LAT, DEPOT_LNG, b['lat'], b['lng'])
    return dict(b, status=status, color=color, pct=pct, dist=dist)


def get_collector_settings(db, collector_id):
    settings = db.execute(
        'SELECT * FROM collector_settings WHERE collector_id=?',
        (collector_id,),
    ).fetchone()
    if settings:
        return settings

    db.execute(
        '''INSERT INTO collector_settings (collector_id, truck_capacity, start_lat, start_lng)
           VALUES (?, ?, ?, ?)''',
        (collector_id, 350, DEPOT_LAT, DEPOT_LNG),
    )
    db.commit()
    return db.execute(
        'SELECT * FROM collector_settings WHERE collector_id=?',
        (collector_id,),
    ).fetchone()


def get_assigned_bins(db, collector_id):
    raw_bins = db.execute(
        '''SELECT DISTINCT b.* FROM bins b
           JOIN assignments a ON b.id=a.bin_id
           WHERE a.collector_id=?''',
        (collector_id,),
    ).fetchall()
    return [enrich_bin(b) for b in raw_bins]


def get_active_route_plan(db, collector_id):
    return db.execute(
        '''SELECT * FROM route_plans
           WHERE collector_id=? AND status='active'
           ORDER BY id DESC LIMIT 1''',
        (collector_id,),
    ).fetchone()


def get_route_plan_progress(db, plan_id):
    summary = db.execute(
        '''SELECT
               COUNT(*) AS total_stops,
               COALESCE(SUM(completed), 0) AS completed_stops
           FROM route_plan_stops
           WHERE plan_id=?''',
        (plan_id,),
    ).fetchone()
    partial_trip = db.execute(
        '''SELECT trip_number
           FROM route_plan_stops
           WHERE plan_id=?
           GROUP BY trip_number
           HAVING SUM(completed) > 0 AND SUM(completed) < COUNT(*)''',
        (plan_id,),
    ).fetchone()
    return {
        'total_stops': summary['total_stops'] or 0,
        'completed_stops': summary['completed_stops'] or 0,
        'has_partial_trip': partial_trip is not None,
    }


def close_route_plan(db, plan_id, status):
    db.execute(
        '''UPDATE route_plans
           SET status=?, completed_at=?
           WHERE id=?''',
        (status, datetime.now().isoformat(), plan_id),
    )


def create_route_plan(db, collector_id, bins, truck_capacity, depot):
    planned = plan_collector_routes(bins, truck_capacity, depot)
    now = datetime.now().isoformat()
    cursor = db.execute(
        '''INSERT INTO route_plans
           (collector_id, truck_capacity, depot_lat, depot_lng, total_distance, total_load, algorithm, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)''',
        (
            collector_id,
            truck_capacity,
            depot['lat'],
            depot['lng'],
            planned['total_distance'],
            planned['total_load'],
            planned['algorithm'],
            now,
        ),
    )
    plan_id = cursor.lastrowid
    for trip in planned['trips']:
        for stop in trip['stops']:
            db.execute(
                '''INSERT INTO route_plan_stops
                   (plan_id, trip_number, stop_order, bin_id, planned_load, completed)
                   VALUES (?, ?, ?, ?, ?, 0)''',
                (
                    plan_id,
                    trip['trip_number'],
                    stop['route_sequence'],
                    stop['id'],
                    stop['current_level'],
                ),
            )
    db.commit()
    return get_active_route_plan(db, collector_id)


def hydrate_route_plan(db, plan_row, bins, depot):
    bins_by_id = {bin_['id']: bin_ for bin_ in bins}
    stop_rows = db.execute(
        '''SELECT rps.*, b.location, b.area, b.capacity, b.current_level, b.lat, b.lng
           FROM route_plan_stops rps
           JOIN bins b ON b.id=rps.bin_id
           WHERE rps.plan_id=?
           ORDER BY rps.trip_number, rps.stop_order''',
        (plan_row['id'],),
    ).fetchall()

    trips_map = {}
    route_stops = []
    for row in stop_rows:
        current_bin = bins_by_id.get(row['bin_id'], enrich_bin(row))
        planned_level = row['planned_load']
        completed = bool(row['completed'])
        pct = round((planned_level / row['capacity']) * 100, 1) if row['capacity'] > 0 else 0
        status, color = get_fill_status(planned_level, row['capacity'])
        if completed:
            status, color = 'Completed', 'success'
        stop = {
            'id': row['bin_id'],
            'location': row['location'],
            'area': row['area'],
            'capacity': row['capacity'],
            'current_level': current_bin['current_level'],
            'planned_level': planned_level,
            'lat': row['lat'],
            'lng': row['lng'],
            'pct': pct,
            'status': status,
            'color': color,
            'dist': haversine(depot['lat'], depot['lng'], row['lat'], row['lng']),
            'route_sequence': row['stop_order'],
            'trip_number': row['trip_number'],
            'completed': completed,
        }
        route_stops.append(stop)
        trip = trips_map.setdefault(
            row['trip_number'],
            {
                'trip_number': row['trip_number'],
                'name': f"Trip {row['trip_number']}",
                'stops': [],
                'load': 0.0,
                'remaining_capacity': 0.0,
                'remaining_load': 0.0,
                'total_distance': 0.0,
                'total_stops': 0,
                'completed_stops': 0,
                'color': ['#1f7a4f', '#2563eb', '#d97706', '#dc2626', '#0f766e', '#7c3aed'][(row['trip_number'] - 1) % 6],
                'over_capacity': False,
                'route_coordinates': [[depot['lat'], depot['lng']]],
            },
        )
        trip['stops'].append(stop)
        trip['load'] += planned_level
        trip['remaining_load'] += 0 if completed else planned_level
        trip['total_stops'] += 1
        trip['completed_stops'] += 1 if completed else 0
        trip['route_coordinates'].append([row['lat'], row['lng']])

    trips = []
    for trip_number in sorted(trips_map):
        trip = trips_map[trip_number]
        trip['route_coordinates'].append([depot['lat'], depot['lng']])
        route_nodes = [{'lat': lat, 'lng': lng} for lat, lng in trip['route_coordinates']]
        total_distance = 0.0
        for index in range(len(route_nodes) - 1):
            total_distance += haversine(
                route_nodes[index]['lat'],
                route_nodes[index]['lng'],
                route_nodes[index + 1]['lat'],
                route_nodes[index + 1]['lng'],
            )
        trip['total_distance'] = round(total_distance, 2)
        trip['remaining_capacity'] = round(max(plan_row['truck_capacity'] - trip['load'], 0), 2)
        trip['complete'] = trip['completed_stops'] == trip['total_stops']
        trip['in_progress'] = 0 < trip['completed_stops'] < trip['total_stops']
        trip['over_capacity'] = trip['load'] > plan_row['truck_capacity']
        if trip['over_capacity']:
            trip['over_by'] = round(trip['load'] - plan_row['truck_capacity'], 2)
        trips.append(trip)

    active_trip_number = next((trip['trip_number'] for trip in trips if not trip['complete']), None)
    for trip in trips:
        trip['active_trip'] = trip['trip_number'] == active_trip_number

    progress = get_route_plan_progress(db, plan_row['id'])
    return {
        'plan_id': plan_row['id'],
        'truck_capacity': plan_row['truck_capacity'],
        'trips': trips,
        'route_stops': route_stops,
        'unplanned_bins': [dict(bin_) for bin_ in bins if bin_['current_level'] <= 0],
        'total_distance': round(sum(trip['total_distance'] for trip in trips), 2),
        'total_load': round(sum(trip['load'] for trip in trips), 2),
        'remaining_load': round(sum(trip['remaining_load'] for trip in trips), 2),
        'oversized_bins': sum(1 for trip in trips if trip['over_capacity']),
        'actionable_bins': len([bin_ for bin_ in bins if bin_['current_level'] > 0]),
        'algorithm': plan_row['algorithm'],
        'locked': progress['has_partial_trip'],
        'completed_stops': progress['completed_stops'],
        'total_stops': progress['total_stops'],
        'active_trip_number': active_trip_number,
    }


def get_dashboard_route_plan(db, collector_id, bins, truck_capacity, depot):
    actionable_ids = sorted(bin_['id'] for bin_ in bins if bin_['current_level'] > 0)
    active_plan = get_active_route_plan(db, collector_id)
    if active_plan:
        progress = get_route_plan_progress(db, active_plan['id'])
        planned_ids = [
            row['bin_id']
            for row in db.execute(
                'SELECT bin_id FROM route_plan_stops WHERE plan_id=? ORDER BY trip_number, stop_order',
                (active_plan['id'],),
            ).fetchall()
        ]

        if progress['has_partial_trip']:
            return hydrate_route_plan(db, active_plan, bins, depot)

        if (
            progress['completed_stops'] == 0
            and sorted(planned_ids) == actionable_ids
            and round(active_plan['truck_capacity'], 2) == round(truck_capacity, 2)
        ):
            return hydrate_route_plan(db, active_plan, bins, depot)

        close_route_plan(
            db,
            active_plan['id'],
            'completed' if progress['completed_stops'] > 0 else 'cancelled',
        )
        db.commit()

    new_plan = create_route_plan(db, collector_id, bins, truck_capacity, depot)
    return hydrate_route_plan(db, new_plan, bins, depot)

def groq_classify(description):
    """Call Groq llama3 to classify waste and give advice."""
    if not GROQ_API_KEY:
        return _rule_based_classify(description)
    try:
        payload = {
            "model": "llama3-8b-8192",
            "messages": [
                {"role": "system", "content": (
                    "You are a smart waste classification assistant for a city waste management app. "
                    "Given a waste description, respond ONLY in valid JSON with these exact keys:\n"
                    "{\n"
                    '  "category": "<one of: Dry Waste | Wet Waste | Hazardous Waste | E-Waste | Medical Waste | Recyclable | Mixed Waste>",\n'
                    '  "subcategory": "<specific type, e.g. Plastic, Food Scraps, Battery>",\n'
                    '  "disposal_tip": "<1-2 sentence practical disposal advice>",\n'
                    '  "recycle": true/false,\n'
                    '  "priority": "<Low | Medium | High>"\n'
                    "}\n"
                    "Respond with ONLY the JSON object, no explanation, no markdown."
                )},
                {"role": "user", "content": f"Classify this waste: {description}"}
            ],
            "max_tokens": 300,
            "temperature": 0.2
        }
        resp = req_lib.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json=payload, timeout=10
        )
        text = resp.json()['choices'][0]['message']['content'].strip()
        # Strip markdown fences if any
        if text.startswith('```'): text = text.split('```')[1].lstrip('json').strip()
        return json.loads(text)
    except Exception as e:
        print(f"Groq error: {e}")
        return _rule_based_classify(description)

def _rule_based_classify(description):
    """Fallback rule-based classification if no Groq key."""
    desc = description.lower()
    if any(w in desc for w in ['battery','phone','laptop','charger','cable','electronics','circuit','tv','monitor']):
        return {"category":"E-Waste","subcategory":"Electronics","disposal_tip":"Take to the nearest e-waste collection centre. Do not throw in regular bins.","recycle":True,"priority":"High"}
    if any(w in desc for w in ['medicine','syringe','needle','bandage','tablet','capsule','medical','injection']):
        return {"category":"Medical Waste","subcategory":"Pharmaceutical","disposal_tip":"Seal in a bag and drop at a pharmacy or medical waste collection point.","recycle":False,"priority":"High"}
    if any(w in desc for w in ['paint','chemical','pesticide','acid','bleach','solvent','toxic','oil']):
        return {"category":"Hazardous Waste","subcategory":"Chemical","disposal_tip":"Do not pour down drains. Take to a hazardous waste facility.","recycle":False,"priority":"High"}
    if any(w in desc for w in ['food','vegetable','fruit','peel','leaf','organic','kitchen','cooked','raw','rotten']):
        return {"category":"Wet Waste","subcategory":"Food/Organic","disposal_tip":"Can be composted at home. Dispose in the green bin for wet waste collection.","recycle":True,"priority":"Medium"}
    if any(w in desc for w in ['plastic','bottle','bag','wrapper','styrofoam','straw','can','tin','glass','paper','cardboard','metal']):
        return {"category":"Dry Waste","subcategory":"Recyclable","disposal_tip":"Rinse and drop in the blue dry waste bin. Check for local recycling drives.","recycle":True,"priority":"Low"}
    return {"category":"Mixed Waste","subcategory":"General","disposal_tip":"Sort before disposal if possible. Place in the nearest bin.","recycle":False,"priority":"Medium"}

# ─── AUTO-FILL BACKGROUND THREAD ────────────────────────────────────────────────

_auto_fill_running = False

def auto_fill_bins():
    """Every 60s in dev (simulates 1hr IoT increment), raise bins by ~5% of capacity."""
    import sqlite3
    while True:
        time.sleep(60)  # 60 seconds = "1 simulated hour"
        try:
            conn = sqlite3.connect(current_app.config["DATABASE"])
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            bins = c.execute('SELECT id, capacity, current_level FROM bins').fetchall()
            for b in bins:
                if b['current_level'] < b['capacity']:
                    increment = b['capacity'] * 0.05  # 5% per tick
                    new_level = min(b['current_level'] + increment, b['capacity'])
                    c.execute("UPDATE bins SET current_level=?, last_updated=datetime('now') WHERE id=?",
                              (new_level, b['id']))
                    if new_level >= b['capacity']:
                        existing = c.execute('SELECT id FROM alerts WHERE bin_id=? AND resolved=0', (b['id'],)).fetchone()
                        if not existing:
                            loc = c.execute('SELECT location FROM bins WHERE id=?', (b['id'],)).fetchone()['location']
                            c.execute("INSERT INTO alerts (bin_id,message,timestamp,resolved) VALUES (?,?,datetime('now'),0)",
                                      (b['id'], f"Bin at '{loc}' is FULL! (Auto-detected)"))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Auto-fill error: {e}")

def start_auto_fill():
    global _auto_fill_running
    if not _auto_fill_running:
        _auto_fill_running = True
        t = threading.Thread(target=auto_fill_bins, daemon=True)
        t.start()
        print("Auto-fill thread started (5% every 60s)")

# ─── AUTH ────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for(f"{session['role']}_dashboard"))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = hp(request.form['password'])
        db = get_db()
        user = db.execute(
            'SELECT * FROM users WHERE username=? AND password=?',
            (username, password)
        ).fetchone()

        if user:
            # ✅ SUCCESS LOG
            app.logger.info(
                "User %s logged in as %s",
                user['username'],
                user['role']
            )

            session.update({
                'user_id': user['id'],
                'username': user['username'],
                'role': user['role'],
                'name': user['name']
            })
            return redirect(url_for(f"{user['role']}_dashboard"))

        # ⚠️ FAILURE LOG
        app.logger.warning(
            "Invalid login attempt for username: %s",
            username
        )

        flash('Invalid credentials', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name     = request.form['name']
        username = request.form['username']
        password = hp(request.form['password'])
        role     = request.form.get('role','resident')
        db = get_db()
        try:
            db.execute('INSERT INTO users (name,username,password,role) VALUES (?,?,?,?)', (name,username,password,role))
            db.commit()
            flash('Account created! Please login.','success')
            return redirect(url_for('login'))
        except:
            flash('Username already taken.','danger')
    return render_template('register.html')

# ─── RESIDENT ────────────────────────────────────────────────────────────────────

@app.route('/resident/dashboard')
def resident_dashboard():
    if session.get('role') != 'resident': return redirect(url_for('login'))
    db = get_db()
    bins = [enrich_bin(b) for b in db.execute('SELECT * FROM bins').fetchall()]
    requests = db.execute('''SELECT pr.*, b.location FROM pickup_requests pr
                             JOIN bins b ON pr.bin_id=b.id
                             WHERE pr.user_id=? ORDER BY pr.timestamp DESC LIMIT 15''',
                          (session['user_id'],)).fetchall()
    return render_template('resident_dashboard.html', bins=bins, requests=requests)

@app.route('/resident/classify', methods=['POST'])
def classify_waste():
    if session.get('role') != 'resident': return jsonify({'error':'unauthorized'}), 401
    description = request.json.get('description','').strip()
    if not description:
        return jsonify({'error':'Empty description'}), 400
    result = groq_classify(description)
    return jsonify(result)

@app.route('/resident/request', methods=['POST'])
def add_request():
    if session.get('role') != 'resident': return redirect(url_for('login'))
    bin_id      = request.form['bin_id']
    description = request.form.get('waste_description','').strip()
    ai_json     = request.form.get('ai_result','{}')
    amount      = float(request.form.get('amount', 10))

    ai = {}
    try: ai = json.loads(ai_json)
    except Exception as e:
        print(f"Error: {e}")

    db = get_db()
    bin_ = db.execute('SELECT * FROM bins WHERE id=?', (bin_id,)).fetchone()
    if not bin_:
        flash('Bin not found.','danger')
        return redirect(url_for('resident_dashboard'))

    new_level = min(bin_['current_level'] + amount, bin_['capacity'])
    db.execute("UPDATE bins SET current_level=?, last_updated=datetime('now') WHERE id=?", (new_level, bin_id))
    db.execute('''INSERT INTO pickup_requests
                  (user_id,bin_id,waste_description,ai_category,ai_advice,amount,status,timestamp)
                  VALUES (?,?,?,?,?,?,?,?)''',
               (session['user_id'], bin_id, description,
                ai.get('category',''), ai.get('disposal_tip',''),
                amount, 'pending', datetime.now().isoformat()))
    db.execute('INSERT INTO waste_logs (user_id,bin_id,amount,timestamp) VALUES (?,?,?,?)',
               (session['user_id'], bin_id, amount, datetime.now().isoformat()))
    if new_level >= bin_['capacity']:
        existing = db.execute('SELECT id FROM alerts WHERE bin_id=? AND resolved=0', (bin_id,)).fetchone()
        if not existing:
            db.execute("INSERT INTO alerts (bin_id,message,timestamp,resolved) VALUES (?,?,?,0)",
                       (bin_id, f"Bin at '{bin_['location']}' is FULL!", datetime.now().isoformat()))
            
    app.logger.info(
    "Pickup request submitted by user %s for bin %s",
    session['username'],
    bin_id
    )
    db.commit()
    flash(f'Pickup request submitted! Added {amount} units to bin.','success')
    return redirect(url_for('resident_dashboard'))

# ─── COLLECTOR ────────────────────────────────────────────────────────────────────

@app.route('/collector/dashboard')
def collector_dashboard():
    if session.get('role') != 'collector': return redirect(url_for('login'))
    db = get_db()
    settings = get_collector_settings(db, session['user_id'])
    bins = get_assigned_bins(db, session['user_id'])
    depot = {
        'lat': settings['start_lat'],
        'lng': settings['start_lng'],
        'location': 'Collector Depot',
    }
    route_plan = get_dashboard_route_plan(db, session['user_id'], bins, settings['truck_capacity'], depot)

    logs = db.execute('''SELECT cl.*, b.location FROM collection_logs cl
                         JOIN bins b ON cl.bin_id=b.id
                         WHERE cl.collector_id=? ORDER BY cl.timestamp DESC LIMIT 15''',
                      (session['user_id'],)).fetchall()
    alerts = db.execute('''SELECT DISTINCT al.*, b.location FROM alerts al
                           JOIN bins b ON al.bin_id=b.id
                           JOIN assignments a ON b.id=a.bin_id
                           WHERE a.collector_id=? AND al.resolved=0 LIMIT 3''',
                        (session['user_id'],)).fetchall()
    pending_requests = db.execute('''SELECT DISTINCT pr.*, b.location, u.name as resident_name
                                     FROM pickup_requests pr
                                     JOIN bins b ON pr.bin_id=b.id
                                     JOIN users u ON pr.user_id=u.id
                                     JOIN assignments a ON b.id=a.bin_id
                                     WHERE a.collector_id=? AND pr.status='pending'
                                     ORDER BY pr.timestamp DESC''',
                                  (session['user_id'],)).fetchall()
    trip_count = len(route_plan['trips'])
    return render_template('collector_dashboard.html',
                           bins=bins, logs=logs, alerts=alerts, pending_requests=pending_requests,
                           route_plan=route_plan, truck_capacity=settings['truck_capacity'],
                           depot=depot, trip_count=trip_count)


@app.route('/collector/settings', methods=['POST'])
def update_collector_settings():
    if session.get('role') != 'collector': return redirect(url_for('login'))
    truck_capacity = float(request.form.get('truck_capacity', 350))
    if truck_capacity <= 0:
        flash('Truck capacity must be greater than 0.', 'danger')
        return redirect(url_for('collector_dashboard'))

    db = get_db()
    active_plan = get_active_route_plan(db, session['user_id'])
    if active_plan and get_route_plan_progress(db, active_plan['id'])['has_partial_trip']:
        flash('Finish the current trip before changing truck capacity so the map stays locked to that trip.', 'warning')
        return redirect(url_for('collector_dashboard'))

    db.execute(
        '''INSERT INTO collector_settings (collector_id, truck_capacity, start_lat, start_lng)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(collector_id) DO UPDATE SET truck_capacity=excluded.truck_capacity''',
        (session['user_id'], truck_capacity, DEPOT_LAT, DEPOT_LNG),
    )
    if active_plan:
        close_route_plan(db, active_plan['id'], 'cancelled')
    db.commit()
    flash('Truck capacity updated. Route plan recalculated.', 'success')
    return redirect(url_for('collector_dashboard'))

@app.route('/collector/collect/<int:bin_id>', methods=['POST'])
def collect_bin(bin_id):
    if session.get('role') != 'collector': return redirect(url_for('login'))
    db = get_db()
    active_plan = get_active_route_plan(db, session['user_id'])
    stop_row = None
    if active_plan:
        stop_row = db.execute(
            '''SELECT * FROM route_plan_stops
               WHERE plan_id=? AND bin_id=? AND completed=0''',
            (active_plan['id'], bin_id),
        ).fetchone()

    db.execute("UPDATE bins SET current_level=0, last_updated=datetime('now') WHERE id=?", (bin_id,))
    db.execute("INSERT INTO collection_logs (collector_id,bin_id,timestamp) VALUES (?,?,?)",
               (session['user_id'], bin_id, datetime.now().isoformat()))
    db.execute('UPDATE alerts SET resolved=1 WHERE bin_id=? AND resolved=0', (bin_id,))
    db.execute("UPDATE pickup_requests SET status='completed' WHERE bin_id=? AND status='pending'", (bin_id,))
    if stop_row:
        db.execute(
            '''UPDATE route_plan_stops
               SET completed=1, completed_at=?
               WHERE id=?''',
            (datetime.now().isoformat(), stop_row['id']),
        )

        trip_state = db.execute(
            '''SELECT COUNT(*) AS total_stops, COALESCE(SUM(completed), 0) AS completed_stops
               FROM route_plan_stops
               WHERE plan_id=? AND trip_number=?''',
            (active_plan['id'], stop_row['trip_number']),
        ).fetchone()
        all_state = get_route_plan_progress(db, active_plan['id'])

        if all_state['completed_stops'] == all_state['total_stops']:
            close_route_plan(db, active_plan['id'], 'completed')
            flash('Trip complete and all assigned stops in this plan are done. The next dashboard load will build a fresh route.', 'success')
        elif trip_state['completed_stops'] == trip_state['total_stops']:
            flash('Trip completed. The route map will refresh now for the remaining pickups.', 'success')
        else:
            flash('Stop collected. The map stays locked until every stop in this trip is completed.', 'info')
    else:
        flash('Bin collected and reset to empty!', 'success')
    app.logger.info(
    "Collector %s collected bin %s",
    session['username'],
    bin_id
    )
    db.commit()
    return redirect(url_for('collector_dashboard'))

# ─── ADMIN ────────────────────────────────────────────────────────────────────────

@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('role') != 'admin': return redirect(url_for('login'))
    db = get_db()
    bins = [enrich_bin(b) for b in db.execute('SELECT * FROM bins').fetchall()]
    collectors = db.execute("SELECT * FROM users WHERE role='collector'").fetchall()
    residents  = db.execute("SELECT * FROM users WHERE role='resident'").fetchall()

    total_requests   = db.execute("SELECT COUNT(*) as c FROM pickup_requests").fetchone()['c']
    pending_requests = db.execute("SELECT COUNT(*) as c FROM pickup_requests WHERE status='pending'").fetchone()['c']
    total_collections= db.execute("SELECT COUNT(*) as c FROM collection_logs").fetchone()['c']
    total_waste      = db.execute("SELECT COALESCE(SUM(amount),0) as s FROM waste_logs").fetchone()['s']
    full_bins        = sum(1 for b in bins if b['status']=='Full')

    alerts = db.execute('''SELECT al.*, b.location FROM alerts al
                           JOIN bins b ON al.bin_id=b.id
                           WHERE al.resolved=0 ORDER BY al.timestamp DESC LIMIT 5''').fetchall()

    # Area intelligence: count requests per area
    area_stats = db.execute('''SELECT b.area, COUNT(pr.id) as req_count,
                                AVG(b.current_level*100.0/b.capacity) as avg_fill
                               FROM bins b
                               LEFT JOIN pickup_requests pr ON b.id=pr.bin_id
                               GROUP BY b.area ORDER BY req_count DESC''').fetchall()

    # Bin level chart data
    bin_chart = [{'label': b['location'][:20], 'pct': b['pct'], 'color': b['color']} for b in bins]

    recent_requests = db.execute('''SELECT pr.*, u.name as uname, b.location
                                    FROM pickup_requests pr
                                    JOIN users u ON pr.user_id=u.id
                                    JOIN bins b ON pr.bin_id=b.id
                                    ORDER BY pr.timestamp DESC LIMIT 20''').fetchall()
    recent_collections = db.execute('''SELECT cl.*, u.name as uname, b.location
                                       FROM collection_logs cl
                                       JOIN users u ON cl.collector_id=u.id
                                       JOIN bins b ON cl.bin_id=b.id
                                       ORDER BY cl.timestamp DESC LIMIT 15''').fetchall()
    return render_template('admin_dashboard.html',
        bins=bins, collectors=collectors, residents=residents,
        total_requests=total_requests, pending_requests=pending_requests,
        total_collections=total_collections, total_waste=total_waste,
        full_bins=full_bins, alerts=alerts, area_stats=area_stats,
        bin_chart=bin_chart, recent_requests=recent_requests,
        recent_collections=recent_collections)

@app.route('/admin/add_bin', methods=['POST'])
def add_bin():
    if session.get('role') != 'admin': return redirect(url_for('login'))
    db = get_db()
    db.execute("INSERT INTO bins (location,area,capacity,current_level,lat,lng,last_updated) VALUES (?,?,?,0,?,?,datetime('now'))",
               (request.form['location'], request.form['area'],
                float(request.form['capacity']),
                float(request.form.get('lat', DEPOT_LAT)),
                float(request.form.get('lng', DEPOT_LNG))))
    app.logger.info(
    "Admin %s added new bin at %s",
    session['username'],
    request.form['location']
    )
    db.commit()
    flash('New bin added!','success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/edit_bin/<int:bin_id>', methods=['POST'])
def edit_bin(bin_id):
    if session.get('role') != 'admin': return redirect(url_for('login'))
    db = get_db()
    db.execute('UPDATE bins SET location=?,area=?,capacity=? WHERE id=?',
               (request.form['location'], request.form['area'],
                float(request.form['capacity']), bin_id))
    app.logger.info(
    "Admin %s edited bin %s",
    session['username'],
    bin_id
    )
    db.commit()
    flash('Bin updated!','success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_bin/<int:bin_id>', methods=['POST'])
def delete_bin(bin_id):
    if session.get('role') != 'admin': return redirect(url_for('login'))
    db = get_db()
    db.execute('DELETE FROM bins WHERE id=?', (bin_id,))
    db.execute('DELETE FROM assignments WHERE bin_id=?', (bin_id,))
    app.logger.warning(
    "Admin %s deleted bin %s",
    session['username'],
    bin_id
    )
    db.commit()
    flash('Bin deleted.','info')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/assign', methods=['POST'])
def assign_collector():
    if session.get('role') != 'admin': return redirect(url_for('login'))
    collector_id = request.form['collector_id']
    bin_id       = request.form['bin_id']
    db = get_db()
    existing = db.execute('SELECT id FROM assignments WHERE collector_id=? AND bin_id=?',(collector_id,bin_id)).fetchone()
    if not existing:
        db.execute('INSERT INTO assignments (collector_id,bin_id) VALUES (?,?)',(collector_id,bin_id))
        db.commit()
        flash('Collector assigned!','success')
    else:
        flash('Already assigned.','info')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/unassign', methods=['POST'])
def unassign_collector():
    if session.get('role') != 'admin': return redirect(url_for('login'))
    db = get_db()
    db.execute('DELETE FROM assignments WHERE collector_id=? AND bin_id=?',
               (request.form['collector_id'], request.form['bin_id']))
    db.commit()
    flash('Assignment removed.','info')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/resolve_alert/<int:alert_id>', methods=['POST'])
def resolve_alert(alert_id):
    if session.get('role') != 'admin': return redirect(url_for('login'))
    db = get_db()
    db.execute('UPDATE alerts SET resolved=1 WHERE id=?',(alert_id,))
    db.commit()
    return redirect(url_for('admin_dashboard'))

# ─── API ──────────────────────────────────────────────────────────────────────────

@app.route('/api/bins')
def api_bins():
    db = get_db()
    bins = [enrich_bin(b) for b in db.execute('SELECT * FROM bins').fetchall()]
    return jsonify([{
        'id':b['id'],'location':b['location'],'area':b['area'],
        'capacity':b['capacity'],'current_level':b['current_level'],
        'pct':b['pct'],'status':b['status'],'lat':b['lat'],'lng':b['lng']
    } for b in bins])

@app.route('/api/area_stats')
def api_area_stats():
    db = get_db()
    stats = db.execute('''SELECT b.area, COUNT(pr.id) as req_count,
                           AVG(b.current_level*100.0/b.capacity) as avg_fill
                          FROM bins b LEFT JOIN pickup_requests pr ON b.id=pr.bin_id
                          GROUP BY b.area ORDER BY req_count DESC''').fetchall()
    return jsonify([dict(s) for s in stats])

if __name__ == '__main__':
    init_db()

    # Only run auto-fill in real app, not during testing
    if not os.environ.get("TESTING"):
        start_auto_fill()

    debug = os.environ.get("FLASK_DEBUG", "False") == "True"
    
    try:
        app.run(host="0.0.0.0", port=5000, debug=debug)
    except Exception as e:
        app.logger.error("Application failed: %s", str(e))
import sqlite3
from flask import g
import os
import hashlib
import math

DATABASE = os.path.join(os.path.dirname(__file__), 'greenbin.db')

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

def close_db(e=None):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return round(R * 2 * math.asin(math.sqrt(a)), 2)

def hp(p):
    return hashlib.sha256(p.encode()).hexdigest()

def init_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'resident'
        );

        CREATE TABLE IF NOT EXISTS bins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL,
            area TEXT NOT NULL DEFAULT 'Central',
            capacity REAL NOT NULL DEFAULT 100,
            current_level REAL NOT NULL DEFAULT 0,
            lat REAL NOT NULL DEFAULT 12.9716,
            lng REAL NOT NULL DEFAULT 77.5946,
            last_updated TEXT
        );

        CREATE TABLE IF NOT EXISTS pickup_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bin_id INTEGER NOT NULL,
            waste_type TEXT,
            waste_description TEXT,
            ai_category TEXT,
            ai_advice TEXT,
            amount REAL DEFAULT 10,
            status TEXT NOT NULL DEFAULT 'pending',
            timestamp TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        );

        CREATE TABLE IF NOT EXISTS waste_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bin_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        );

        CREATE TABLE IF NOT EXISTS collection_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collector_id INTEGER NOT NULL,
            bin_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (collector_id) REFERENCES users(id),
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collector_id INTEGER NOT NULL,
            bin_id INTEGER NOT NULL,
            FOREIGN KEY (collector_id) REFERENCES users(id),
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        );

        CREATE TABLE IF NOT EXISTS collector_settings (
            collector_id INTEGER PRIMARY KEY,
            truck_capacity REAL NOT NULL DEFAULT 350,
            start_lat REAL NOT NULL DEFAULT 12.9716,
            start_lng REAL NOT NULL DEFAULT 77.5946,
            FOREIGN KEY (collector_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS route_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collector_id INTEGER NOT NULL,
            truck_capacity REAL NOT NULL,
            depot_lat REAL NOT NULL,
            depot_lng REAL NOT NULL,
            total_distance REAL NOT NULL DEFAULT 0,
            total_load REAL NOT NULL DEFAULT 0,
            algorithm TEXT NOT NULL DEFAULT 'heuristic',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (collector_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS route_plan_stops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            trip_number INTEGER NOT NULL,
            stop_order INTEGER NOT NULL,
            bin_id INTEGER NOT NULL,
            planned_load REAL NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT,
            FOREIGN KEY (plan_id) REFERENCES route_plans(id),
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bin_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        );
    ''')

    # Seed users
    users = [
        ('Admin User',   'admin', hp('admin123'), 'admin'),
        ('Ravi Kumar',   'ravi',  hp('ravi123'),  'collector'),
        ('Priya Nair',   'priya', hp('priya123'), 'collector'),
        ('Amit Singh',   'amit',  hp('amit123'),  'resident'),
        ('Sonal Mehta',  'sonal', hp('sonal123'), 'resident'),
    ]
    for u in users:
        try:
            c.execute('INSERT INTO users (name,username,password,role) VALUES (?,?,?,?)', u)
        except: pass

    # Seed bins with real Bengaluru-area coordinates
    bins_data = [
        ('Sector 12 – Main Gate',      'North Zone',  200, 170, 13.0358, 77.5970),
        ('Park Avenue – North End',    'North Zone',  150,  55, 13.0300, 77.6010),
        ('Market Street – Block A',    'Central',     100, 100, 12.9716, 77.5946),
        ('Residential Zone 4',         'East Zone',   180,  40, 12.9784, 77.6408),
        ('School Road – Entry',        'West Zone',   120,  25, 12.9698, 77.5600),
        ('Central Square',             'Central',     250, 245, 12.9762, 77.5929),
        ('Gandhi Nagar – Block C',     'South Zone',  150,  90, 12.9270, 77.5950),
        ('Tech Park – Gate 2',         'East Zone',   200,  60, 12.9352, 77.6245),
    ]
    count = c.execute('SELECT COUNT(*) FROM bins').fetchone()[0]
    if count == 0:
        for b in bins_data:
            c.execute('''INSERT INTO bins (location,area,capacity,current_level,lat,lng,last_updated)
                         VALUES (?,?,?,?,?,?,datetime('now'))''', b)

    # Seed assignments
    collector_ids = [r[0] for r in c.execute("SELECT id FROM users WHERE role='collector'").fetchall()]
    bin_ids = [r[0] for r in c.execute("SELECT id FROM bins").fetchall()]
    if collector_ids and bin_ids:
        half = len(bin_ids)//2
        for bid in bin_ids[:half+1]:
            try: c.execute('INSERT INTO assignments (collector_id,bin_id) VALUES (?,?)', (collector_ids[0], bid))
            except: pass
        for bid in bin_ids[half:]:
            try: c.execute('INSERT INTO assignments (collector_id,bin_id) VALUES (?,?)', (collector_ids[1], bid))
            except: pass

    for collector_id in collector_ids:
        try:
            c.execute(
                '''INSERT INTO collector_settings (collector_id, truck_capacity, start_lat, start_lng)
                   VALUES (?, ?, ?, ?)''',
                (collector_id, 350, 12.9716, 77.5946)
            )
        except:
            pass

    # Seed alerts for full bins
    full_bins = c.execute('SELECT id,location FROM bins WHERE current_level >= capacity').fetchall()
    for b in full_bins:
        try:
            c.execute("INSERT INTO alerts (bin_id,message,timestamp,resolved) VALUES (?,?,datetime('now'),0)",
                      (b['id'], f"Bin at '{b['location']}' is FULL!"))
        except: pass

    c.execute('''DELETE FROM assignments
                 WHERE id NOT IN (
                     SELECT MIN(id)
                     FROM assignments
                     GROUP BY collector_id, bin_id
                 )''')
    c.execute('''DELETE FROM route_plan_stops
                 WHERE id NOT IN (
                     SELECT MIN(id)
                     FROM route_plan_stops
                     GROUP BY plan_id, trip_number, stop_order
                 )''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_assignments_unique ON assignments (collector_id, bin_id)')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_route_plan_stop_unique ON route_plan_stops (plan_id, trip_number, stop_order)')

    conn.commit()
    conn.close()
    print("Database initialized with seed data.")

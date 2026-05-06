import psycopg2
import psycopg2.extras
from flask import g
import os
import hashlib
import math
from flask import current_app

class DBWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return cursor
        
    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        db = g._database = DBWrapper(conn)
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
    db = get_db()

    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'resident'
        );
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS bins (
            id SERIAL PRIMARY KEY,
            location TEXT NOT NULL,
            area TEXT NOT NULL DEFAULT 'Central',
            capacity REAL NOT NULL DEFAULT 100,
            current_level REAL NOT NULL DEFAULT 0,
            lat REAL NOT NULL DEFAULT 12.9716,
            lng REAL NOT NULL DEFAULT 77.5946,
            last_updated TEXT
        );
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS pickup_requests (
            id SERIAL PRIMARY KEY,
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
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS waste_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            bin_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        );
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS collection_logs (
            id SERIAL PRIMARY KEY,
            collector_id INTEGER NOT NULL,
            bin_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (collector_id) REFERENCES users(id),
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        );
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS assignments (
            id SERIAL PRIMARY KEY,
            collector_id INTEGER NOT NULL,
            bin_id INTEGER NOT NULL,
            FOREIGN KEY (collector_id) REFERENCES users(id),
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        );
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS collector_settings (
            collector_id INTEGER PRIMARY KEY,
            truck_capacity REAL NOT NULL DEFAULT 350,
            start_lat REAL NOT NULL DEFAULT 12.9716,
            start_lng REAL NOT NULL DEFAULT 77.5946,
            FOREIGN KEY (collector_id) REFERENCES users(id)
        );
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS route_plans (
            id SERIAL PRIMARY KEY,
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
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS route_plan_stops (
            id SERIAL PRIMARY KEY,
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
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id SERIAL PRIMARY KEY,
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
            db.execute('INSERT INTO users (name,username,password,role) VALUES (%s,%s,%s,%s) ON CONFLICT (username) DO NOTHING', u)
        except Exception:
            db.rollback()

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
    count = db.execute('SELECT COUNT(*) AS count FROM bins').fetchone()['count']
    if count == 0:
        for b in bins_data:
            db.execute('''INSERT INTO bins (location,area,capacity,current_level,lat,lng,last_updated)
                         VALUES (%s,%s,%s,%s,%s,%s,NOW())''', b)

    # Seed assignments
    collector_ids = [r['id'] for r in db.execute("SELECT id FROM users WHERE role='collector'").fetchall()]
    bin_ids = [r['id'] for r in db.execute("SELECT id FROM bins").fetchall()]
    if collector_ids and bin_ids:
        half = len(bin_ids)//2
        for bid in bin_ids[:half+1]:
            try:
                db.execute('INSERT INTO assignments (collector_id,bin_id) VALUES (%s,%s)', (collector_ids[0], bid))
            except Exception:
                db.rollback()
        for bid in bin_ids[half:]:
            try:
                db.execute('INSERT INTO assignments (collector_id,bin_id) VALUES (%s,%s)', (collector_ids[1], bid))
            except Exception:
                db.rollback()

    for collector_id in collector_ids:
        try:
            db.execute(
                '''INSERT INTO collector_settings (collector_id, truck_capacity, start_lat, start_lng)
                   VALUES (%s, %s, %s, %s) ON CONFLICT (collector_id) DO NOTHING''',
                (collector_id, 350, 12.9716, 77.5946)
            )
        except Exception:
            db.rollback()

    # Seed alerts for full bins
    full_bins = db.execute('SELECT id,location FROM bins WHERE current_level >= capacity').fetchall()
    for b in full_bins:
        try:
            db.execute("INSERT INTO alerts (bin_id,message,timestamp,resolved) VALUES (%s,%s,NOW(),0)",
                      (b['id'], f"Bin at '{b['location']}' is FULL!"))
        except Exception:
            db.rollback()

    db.execute('''DELETE FROM assignments
                 WHERE id NOT IN (
                     SELECT MIN(id)
                     FROM assignments
                     GROUP BY collector_id, bin_id
                 )''')
    db.execute('''DELETE FROM route_plan_stops
                 WHERE id NOT IN (
                     SELECT MIN(id)
                     FROM route_plan_stops
                     GROUP BY plan_id, trip_number, stop_order
                 )''')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_assignments_unique ON assignments (collector_id, bin_id)')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_route_plan_stop_unique ON route_plan_stops (plan_id, trip_number, stop_order)')

    db.commit()
    print("Database initialized with seed data.")

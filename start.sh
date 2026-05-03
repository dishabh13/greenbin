#!/bin/sh

# Initialize DB with proper Flask context
python -c "
from app import app
from database import init_db
with app.app_context():
    init_db()
print('Database initialized!')
"

# Start gunicorn
exec gunicorn -b 0.0.0.0:5000 app:app
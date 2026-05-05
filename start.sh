#!/bin/sh

# Initialize DB
python -c "
from app import app
from database import init_db
with app.app_context():
    init_db()
print('Database initialized!')
"

# Start app using Railway port
exec gunicorn -b 0.0.0.0:$PORT app:app
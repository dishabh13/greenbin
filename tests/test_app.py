import sys
import os
from database import init_db  
if os.path.exists("test.db"):
    os.remove("test.db")

# Fix import path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from app import app, init_db

@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["DATABASE"] = "test.db" 

    with app.app_context():
        init_db()  # Create tables NOW

    with app.test_client() as client:
        yield client


def test_home_redirect(client):
    response = client.get('/')
    assert response.status_code == 302


def test_login_page(client):
    response = client.get('/login')
    assert response.status_code == 200

def test_bins_api(client):
    res = client.get('/api/bins')
    assert res.status_code == 200

def test_login_fail(client):
    res = client.post('/login', data={
        "username": "wrong",
        "password": "wrong"
    })
    assert res.status_code in [200, 302]

def test_admin_redirect(client):
    res = client.get('/admin/dashboard')
    assert res.status_code in [302, 401, 403]

@pytest.fixture(scope="session", autouse=True)
def clean_db():
    if os.path.exists("test.db"):
        os.remove("test.db")
import sys
import os
import pytest

# Fix import path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


@pytest.fixture
def client():
    os.environ["TESTING"] = "1"
    from app import app
    app.config["TESTING"] = True

    with app.app_context():
        from database import init_db
        init_db()  

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

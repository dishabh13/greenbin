import sys
import os
import pytest

# Fix import path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app


@pytest.fixture
def client():
    os.environ["TESTING"] = "1"
    app.config["TESTING"] = True

    with app.test_client() as client:
        yield client


def test_home_redirect(client):
    response = client.get('/')
    assert response.status_code == 302


def test_login_page(client):
    response = client.get('/login')
    assert response.status_code == 200
import sys
import os
import json
import pytest
from unittest.mock import patch

# Ensure parent directory (project root) is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from api_server import app

# Pytest fixture for creating the client
@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

# Test for sending email successfully
@patch("api_server.send_email")
def test_send_email_success(mock_send_email, client):
    mock_send_email.return_value = (True, None)
    payload = {
        "client_name": "John",
        "client_email": "john@example.com",
        "recipient_email": "receiver@example.com",
        "subject": "Test Subject",
        "message": "Hello World!",
        "cc": ["cc1@example.com"]
    }

    response = client.post("/emails/send", json=payload)
    assert response.status_code == 200
    assert response.json["status"] == "success"

# Test for missing required fields in the email payload
@patch("api_server.send_email")
def test_send_email_missing_fields(mock_send_email, client):
    incomplete_payload = {
        "client_email": "john@example.com",
        "subject": "Missing Fields"
    }

    response = client.post("/emails/send", json=incomplete_payload)
    assert response.status_code == 400
    assert response.json["status"] == "error"

# Test for successful broadcast email sending
@patch("api_server.get_recipient_emails")
@patch("api_server.send_email")
def test_broadcast_email_success(mock_send_email, mock_get_emails, client):
    mock_get_emails.return_value = ["a@example.com", "b@example.com"]
    mock_send_email.return_value = (True, None)

    payload = {
        "subject": "Broadcast",
        "message": "Announcement!",
        "recipient_type": "RESIDENTS"
    }

    response = client.post("/emails/broadcast", json=payload)
    assert response.status_code == 200
    assert "Broadcast complete" in response.json["message"]

# Test for no recipients found during broadcast
@patch("api_server.get_recipient_emails")
def test_broadcast_email_no_recipients(mock_get_emails, client):
    mock_get_emails.return_value = []

    payload = {
        "subject": "Broadcast",
        "message": "No one here!",
        "recipient_type": "RESIDENTS"
    }

    response = client.post("/emails/broadcast", json=payload)
    assert response.status_code == 404
    assert response.json["message"] == "No recipients found"

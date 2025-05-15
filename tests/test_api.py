import os
import pytest
import smtplib
from email.message import EmailMessage
from unittest.mock import patch, MagicMock

import api_server
from api_server import send_email, generate_email_html, get_recipient_emails, app
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
from datetime import datetime
@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "x"*32)
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASS", "pass")
    monkeypatch.setenv("SMTP_SERVER", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("FROM_EMAIL", "from@example.com")
    monkeypatch.setenv("DATABASE_URL", "postgres://test:test@localhost/test")
    yield

def test_send_email_smtp_error(monkeypatch):
    class DummySSL:
        def __enter__(self): raise smtplib.SMTPException("boom")
        def __exit__(self, *args): pass
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda server, port: DummySSL())
    ok, err = send_email("to@example.com", "subj", "<p>hi</p>")
    assert not ok
    assert "boom" in err

def test_generate_email_html_contains_message():
    message_content = "<p>Test message</p>"
    html = generate_email_html(message_content)
    assert message_content in html
    assert "<!DOCTYPE html>" in html
    assert "iReserve System Notification" in html

@patch("api_server.psycopg2.connect")
def test_get_recipient_emails_branches(mock_connect):
    dummy_conn = MagicMock()
    dummy_cur = MagicMock()
    for typ, rows in [
        ("ALL", [("a@a.com",), ("b@b.com",)]),
        ("RESIDENTS", [("r@r.com",)]),
        ("STAFF", [("s@s.com",)]),
    ]:
        dummy_cur.fetchall.return_value = rows
        dummy_conn.cursor.return_value = dummy_cur
        mock_connect.return_value = dummy_conn

        emails = get_recipient_emails(typ)
        assert emails == [r[0] for r in rows]
        dummy_cur.execute.assert_called()
        dummy_cur.close.assert_called()
        dummy_conn.close.assert_called()
        dummy_cur.reset_mock()
        dummy_conn.reset_mock()

@patch("api_server.psycopg2.connect")
def test_get_recipient_emails_raises(mock_connect):
    mock_connect.side_effect = Exception("dbfail")
    with pytest.raises(Exception):
        get_recipient_emails("ALL")

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_cors_headers_on_send(client):
    data = {
        "client_name": "A", "client_email": "a@a.com",
        "recipient_email": "b@b.com", "subject": "s", "message": "m"
    }
    with patch("api_server.send_email", return_value=(True, None)):
        resp = client.post("/emails/send", json=data)
    assert resp.status_code == 200
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert "Content-Type" in resp.headers["Access-Control-Allow-Headers"]

def test_broadcast_missing_fields(client):
    resp = client.post("/emails/broadcast", json={"subject": "s"})
    assert resp.status_code == 400
    assert resp.json["status"] == "error"

@patch("api_server.get_recipient_emails", return_value=["ok@ok.com"])
@patch("api_server.send_email")
def test_broadcast_partial_fail(mock_send, mock_get, client):
    mock_send.side_effect = [(True, None), (False, "err!")]
    payload = {"subject": "S", "message": "M", "recipient_type": "ALL"}
    mock_get.return_value = ["one@x.com", "two@x.com"]
    resp = client.post("/emails/broadcast", json=payload)
    assert resp.status_code == 200
    body = resp.json
    assert len(body["results"]) == 2
    statuses = {r["status"] for r in body["results"]}
    assert statuses == {"success", "failed"}
    assert "1/2" in body["message"]

def test_broadcast_db_error(client):
    with patch("api_server.get_recipient_emails", side_effect=Exception("oops")):
        resp = client.post("/emails/broadcast", json={
            "subject": "s", "message": "m", "recipient_type": "ALL"
        })
    assert resp.status_code == 500
    assert "Database error" in resp.json["message"]

@patch("api_server.psycopg2.connect")
def test_send_booking_reminders_success(mock_connect, client):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = [
        (1, "test@example.com", "John", 1, datetime.now(), "Court 1")
    ]
    
    with patch("api_server.send_email", return_value=(True, None)):
        resp = client.post("/emails/send-booking-reminders")
    
    assert resp.status_code == 200
    body = resp.json
    assert body["status"] == "success"
    assert any("test@example.com" in msg for msg in body["details"])

@patch("api_server.psycopg2.connect")
def test_send_booking_reminders_failure(mock_connect, client):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = [
        (1, "test@example.com", "John", 1, datetime.now(), "Court 1")
    ]
    
    with patch("api_server.send_email", return_value=(False, "SMTP error")):
        resp = client.post("/emails/send-booking-reminders")
    
    assert resp.status_code == 200
    body = resp.json
    assert any("Failed to send" in msg for msg in body["details"])

def test_send_booking_reminders_no_bookings(client):
    with patch("api_server.psycopg2.connect") as mock_connect:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_connect.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = []
        
        resp = client.post("/emails/send-booking-reminders")
        
    assert resp.status_code == 200
    assert resp.json["messages_sent"] == 0

@patch("api_server.psycopg2.connect", side_effect=Exception("db fail"))
def test_send_booking_reminders_db_error(mock_connect, client):
    resp = client.post("/emails/send-booking-reminders")
    assert resp.status_code == 500
    assert "Failed to process" in resp.json["message"]

def test_decrypt_email_success():
    encrypted = "iv_hex:ciphertext_hex"
    with patch.object(api_server, 'ENCRYPTION_KEY', 'x'*32):
        decrypted = api_server.decrypt_email(encrypted)
    assert '@' in decrypted

def test_decrypt_email_failure():
    with pytest.raises(ValueError), \
         patch.object(api_server, 'ENCRYPTION_KEY', 'x'*32):
        api_server.decrypt_email("invalid_format")

def test_process_email_invalid():
    assert api_server.process_email("invalid_email") == "invalid_email"

@patch("api_server.send_email")
def test_send_email_with_cc(mock_send):
    ok, err = send_email("to@x.com", "subj", "msg", cc=["cc1@x.com", "cc2@x.com"])
    assert mock_send.called

def test_generate_email_html_with_name():
    html = generate_email_html("msg", "John Doe")
    assert "Dear John Doe" in html

@patch("api_server.psycopg2.connect")
def test_get_recipient_emails_empty(mock_connect):
    dummy_conn = MagicMock()
    dummy_cur = MagicMock()
    dummy_cur.fetchall.return_value = []
    dummy_conn.cursor.return_value = dummy_cur
    mock_connect.return_value = dummy_conn
    
    emails = get_recipient_emails("ALL")
    assert emails == []

def test_send_email_invalid_recipient():
    ok, err = send_email("invalid", "subj", "msg")
    assert not ok
    assert "Invalid email" in err

@patch("api_server.psycopg2.connect")
def test_booking_reminders_encrypted_email(mock_connect, client):
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    mock_cur.fetchall.return_value = [
        (1, "encrypted_email", "John", 1, datetime.now(), "Court 1")
    ]
    
    with patch("api_server.process_email", return_value="decrypted@x.com"), \
         patch("api_server.send_email", return_value=(True, None)):
        resp = client.post("/emails/send-booking-reminders")
    
    assert resp.status_code == 200
    assert "decrypted@x.com" in resp.json["details"][0]

def test_model_validations(client):
    resp = client.post("/emails/broadcast", json={
        "subject": "s", 
        "message": "m",
        "recipient_type": "INVALID"
    })
    assert resp.status_code == 400

    resp = client.post("/emails/send", json={
        "client_name": "A",
        "recipient_email": "b@b.com",
        "subject": "s",
        "message": "m"
    })
    assert resp.status_code == 400

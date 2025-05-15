import os
import pytest
import smtplib
from email.message import EmailMessage
from unittest.mock import patch, MagicMock

import api_server
from api_server import send_email, generate_email_html, get_recipient_emails, app
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASS", "pass")
    monkeypatch.setenv("SMTP_SERVER", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("FROM_EMAIL", "from@example.com")
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
    token = "XYZ123"
    html = generate_email_html(token)
    assert f"<strong>{token}</strong>" in html or f"<p>{token}</p>" in html
    assert "<!DOCTYPE html>" in html
    assert "iReserve System Notification" in html

@patch("api_server.psycopg2.connect")
def test_get_recipient_emails_branches(mock_connect):
    dummy_conn = MagicMock()
    dummy_cur = MagicMock()
    for typ, rows in [
        ("ALL", [("a@a.com",),("b@b.com",)]),
        ("RESIDENTS", [("r@r.com",)]),
        ("STAFF", [("s@s.com",)]),
    ]:
        dummy_cur.fetchall.return_value = rows
        dummy_conn.cursor.return_value = dummy_cur
        mock_connect.return_value = dummy_conn

        emails = get_recipient_emails(typ)
        assert emails == [r[0] for r in rows]
        dummy_cur.execute.assert_called()
        dummy_cur.close.assert_called_once()
        dummy_conn.close.assert_called_once()
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

@patch("api_server.get_recipient_emails", return_value=["a@a.com"])
@patch("api_server.send_email", return_value=(True, None))
def test_send_booking_reminders_success(mock_send, mock_get, client):
    data = {
        "subject": "Reminder",
        "message": "Your booking is coming up.",
        "recipient_type": "ALL"
    }
    resp = client.post("/emails/send-booking-reminders", json=data)
    assert resp.status_code == 200
    body = resp.json
    assert body["status"] == "success"
    assert isinstance(body.get("results"), list)
    assert len(body["results"]) == 1
    assert body["results"][0]["recipient"] == "a@a.com"
    assert body["results"][0]["status"] == "success"

@patch("api_server.get_recipient_emails", return_value=["a@a.com"])
@patch("api_server.send_email", return_value=(False, "SMTP error"))
def test_send_booking_reminders_failure(mock_send, mock_get, client):
    data = {
        "subject": "Reminder",
        "message": "Your booking is coming up.",
        "recipient_type": "ALL"
    }
    resp = client.post("/emails/send-booking-reminders", json=data)
    assert resp.status_code == 200
    body = resp.json
    assert body["status"] == "success"
    assert isinstance(body.get("results"), list)
    assert body["results"][0]["status"] == "failed"
    assert "SMTP error" in body["results"][0]["error"]

def test_send_booking_reminders_missing_fields(client):
    resp = client.post("/emails/send-booking-reminders", json={"subject": "Test"})
    assert resp.status_code == 400
    body = resp.json
    assert body["status"] == "error"
    assert "Missing" in body["message"]

@patch("api_server.get_recipient_emails", side_effect=Exception("db fail"))
def test_send_booking_reminders_db_error(mock_get, client):
    data = {
        "subject": "Reminder",
        "message": "Message",
        "recipient_type": "ALL"
    }
    resp = client.post("/emails/send-booking-reminders", json=data)
    assert resp.status_code == 500
    assert resp.json["status"] == "error"
    assert "Database error" in resp.json["message"]

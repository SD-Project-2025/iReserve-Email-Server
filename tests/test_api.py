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
    # ensure env vars for send_email
    monkeypatch.setenv("SMTP_USER", "user")
    monkeypatch.setenv("SMTP_PASS", "pass")
    monkeypatch.setenv("SMTP_SERVER", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("FROM_EMAIL", "from@example.com")
    yield

def test_send_email_smtp_error(monkeypatch):
    # force SMTP_SSL to raise
    class DummySSL:
        def __enter__(self): raise smtplib.SMTPException("boom")
        def __exit__(self, *args): pass
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda server, port: DummySSL())
    ok, err = send_email("to@example.com", "subj", "<p>hi</p>")
    assert not ok
    assert "boom" in err

def test_generate_email_html_contains_message():
    html = generate_email_html("XYZ123")
    assert "<p>XYZ123</p>" in html
    # basic structure
    assert "<!DOCTYPE html>" in html
    assert "iReserve System Notification" in html

@patch("api_server.psycopg2.connect")
def test_get_recipient_emails_branches(mock_connect):
    # Simulate cursor with fetchall
    dummy_conn = MagicMock()
    dummy_cur = MagicMock()
    # define three scenarios
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
        dummy_cur.execute.assert_called()  # query ran
        dummy_cur.close.assert_called_once()
        dummy_conn.close.assert_called_once()
        # reset mocks
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
    # any POST will trigger after_request
    data = {
        "client_name": "A","client_email":"a@a.com",
        "recipient_email":"b@b.com","subject":"s","message":"m"
    }
    with patch("api_server.send_email", return_value=(True,None)):
        resp = client.post("/emails/send", json=data)
    assert resp.status_code == 200
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert "Content-Type" in resp.headers["Access-Control-Allow-Headers"]

def test_broadcast_missing_fields(client):
    resp = client.post("/emails/broadcast", json={"subject":"s"})
    assert resp.status_code == 400
    assert resp.json["status"] == "error"

@patch("api_server.get_recipient_emails", return_value=["ok@ok.com"])
@patch("api_server.send_email")
def test_broadcast_partial_fail(mock_send, mock_get, client):
    # first send succeeds, second fails
    mock_send.side_effect = [(True,None),(False,"err!")]
    payload = {"subject":"S","message":"M","recipient_type":"ALL"}
    # tweak get to return two
    mock_get.return_value = ["one@x.com","two@x.com"]
    resp = client.post("/emails/broadcast", json=payload)
    assert resp.status_code == 200
    body = resp.json
    # ensure results list length
    assert len(body["results"]) == 2
    statuses = {r["status"] for r in body["results"]}
    assert statuses == {"success","failed"}
    assert "1/2" in body["message"]

def test_broadcast_db_error(client):
    with patch("api_server.get_recipient_emails", side_effect=Exception("oops")):
        resp = client.post("/emails/broadcast", json={
            "subject":"s","message":"m","recipient_type":"ALL"
        })
    assert resp.status_code == 500
    assert "Database error" in resp.json["message"]

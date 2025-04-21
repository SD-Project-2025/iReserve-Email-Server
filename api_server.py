import os
from flask import Flask, request
from flask_restx import Api, Resource, fields, Namespace
from email.message import EmailMessage
from dotenv import load_dotenv
import smtplib
import psycopg2
from psycopg2 import sql
from typing import Tuple, List
from flask_cors import CORS


# Load environment variables
load_dotenv()

# Configuration
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_SERVER = os.getenv("SMTP_SERVER", "mail.devs-central.co.za")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
FROM_NAME = os.getenv("FROM_NAME", "iReserve System")
DATABASE_URL = os.getenv("DATABASE_URL")

# Flask app and API setup
app = Flask(__name__)
CORS(app)
@app.after_request
def add_cors_headers(response):
    if request.method == "POST":
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response
api = Api(app,
          version="1.0",
          title="iReserve Email API",
          description="API for sending individual and broadcast emails",
          doc="/swagger/")

ns = Namespace('emails', description='Email operations')

# Models for Swagger
email_request_model = api.model('EmailRequest', {
    'client_name': fields.String(required=True, example="John Doe"),
    'client_email': fields.String(required=True, example="client@example.com"),
    'recipient_email': fields.String(required=True, example="recipient@example.com"),
    'subject': fields.String(required=True, example="Important Message"),
    'message': fields.String(required=True, example="Hello, this is my message"),
    'cc': fields.List(fields.String, required=False, example=["cc1@example.com", "cc2@example.com"])
})

broadcast_model = api.model('BroadcastRequest', {
    'subject': fields.String(required=True, example="Important Update"),
    'message': fields.String(required=True, example="This is a broadcast message"),
    'recipient_type': fields.String(required=True,
                                    enum=['ALL', 'RESIDENTS', 'STAFF'],
                                    example="RESIDENTS")
})

success_response_model = api.model('SuccessResponse', {
    'status': fields.String(example="success"),
    'message': fields.String(example="Email sent successfully")
})

error_response_model = api.model('ErrorResponse', {
    'status': fields.String(example="error"),
    'message': fields.String(example="Failed to send email"),
    'error': fields.String(example="SMTP authentication failed")
})

# Utility functions
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def send_email(to_email: str, subject: str, html_body: str, reply_to=None, cc=None) -> Tuple[bool, str]:
    try:
        msg = EmailMessage()
        msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
        msg["To"] = to_email
        msg["Subject"] = subject

        if reply_to:
            msg["Reply-To"] = reply_to

        if cc:
            msg["Cc"] = ", ".join(cc)

        msg.set_content(html_body, subtype="html")

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        return True, None
    except Exception as e:
        return False, str(e)

def get_recipient_emails(recipient_type: str) -> List[str]:
    conn = get_db_connection()
    cur = conn.cursor()

    if recipient_type == "ALL":
        query = """
            SELECT r.email FROM residents r
            JOIN users u ON r.user_id = u.user_id WHERE u.status = 'active'
            UNION
            SELECT s.email FROM staff s
            JOIN users u ON s.user_id = u.user_id WHERE u.status = 'active'
        """
    elif recipient_type == "RESIDENTS":
        query = """
            SELECT r.email FROM residents r
            JOIN users u ON r.user_id = u.user_id 
            WHERE u.status = 'active' AND u.user_type = 'resident'
        """
    elif recipient_type == "STAFF":
        query = """
            SELECT s.email FROM staff s
            JOIN users u ON s.user_id = u.user_id 
            WHERE u.status = 'active' AND u.user_type = 'staff'
        """

    cur.execute(query)
    emails = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()

    return emails

def generate_email_html(message: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
            .container {{ max-width: 600px; margin: auto; padding: 20px; }}
            .header {{ background-color: #2563eb; color: white; padding: 20px; text-align: center; }}
            .content {{ padding: 20px; background-color: #ffffff; }}
            .footer {{ margin-top: 20px; font-size: 0.8em; color: #666; text-align: center; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2>iReserve System Notification</h2>
            </div>
            <div class="content">
                <p>Dear User,</p>
                <p>{message}</p>
                <p>Best regards,<br>The iReserve Team</p>
            </div>
            <div class="footer">
                <p>&copy; 2025 iReserve Community Sports Facility System</p>
            </div>
        </div>
    </body>
    </html>
    """

# Routes
@ns.route('/send')
class EmailSender(Resource):
    @api.doc('send_email')
    @api.expect(email_request_model)
    @api.response(200, 'Success', success_response_model)
    @api.response(400, 'Validation Error', error_response_model)
    @api.response(500, 'Server Error', error_response_model)
    def post(self):
        """Send an individual email"""
        data = request.json

        required_fields = ['client_name', 'client_email', 'recipient_email', 'subject', 'message']
        if not all(field in data for field in required_fields):
            return {'status': 'error', 'message': 'Missing required fields'}, 400

        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #2E8B57; color: white; padding: 10px; text-align: center; }}
                .content {{ padding: 20px; }}
                .footer {{ margin-top: 20px; font-size: 0.8em; color: #666; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>Message from iReserve System</h2>
                </div>
                <div class="content">
                    <hr>
                    <p>{data['message']}</p>
                </div>
                <div class="footer">
                    <p>This email was sent via the iReserve Community Sports Facility System</p>
                </div>
            </div>
        </body>
        </html>
        """

        success, error = send_email(
            to_email=data['recipient_email'],
            subject=data['subject'],
            html_body=html_content,
            reply_to=data['client_email'],
            cc=data.get('cc')
        )

        if success:
            return {'status': 'success', 'message': 'Email sent successfully'}, 200
        else:
            return {'status': 'error', 'message': 'Failed to send email', 'error': error}, 500

@ns.route('/broadcast')
class BroadcastEmail(Resource):
    @api.expect(broadcast_model)
    def post(self):
        """Send broadcast emails to selected group"""
        data = request.json
        

        if not all(key in data for key in ['subject', 'message', 'recipient_type']):
            return {"status": "error", "message": "Missing required fields"}, 400

        try:
            emails = get_recipient_emails(data['recipient_type'])
            if not emails:
                return {"status": "error", "message": "No recipients found"}, 404
        except Exception as e:
            return {"status": "error", "message": f"Database error: {str(e)}"}, 500

        results = []
        for email in emails:
            html_content = generate_email_html(data['message'])
            success, error = send_email(email, data['subject'], html_content)
            results.append({
                "email": email,
                "status": "success" if success else "failed",
                "error": error
            })

        success_count = sum(1 for r in results if r["status"] == "success")
        return {
            "status": "success",
            "message": f"Broadcast complete. {success_count}/{len(results)} emails sent successfully.",
            "results": results
        }, 200

# Register namespace
api.add_namespace(ns)
Port = os.getenv("PORT")
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=Port)

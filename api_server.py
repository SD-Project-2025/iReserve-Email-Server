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
from datetime import datetime
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

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
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")


if not ENCRYPTION_KEY or len(ENCRYPTION_KEY) != 32:
    raise ValueError("Invalid ENCRYPTION_KEY - must be 32 characters long")


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
          description="API for sending individual, broadcast emails and booking reminders",
          doc="/swagger/")

ns = Namespace('emails', description='Email operations')


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

booking_reminder_response = api.model('BookingReminderResponse', {
    'status': fields.String(example="success"),
    'messages_sent': fields.Integer(example=3),
    'total_bookings': fields.Integer(example=5),
    'details': fields.List(fields.String, example=[
        "Email sent to john@example.com for 2 bookings",
        "No bookings found for mary@example.com"
    ])
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


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def decrypt_email(encrypted_text: str) -> str:
    """Decrypt AES-256-CBC encrypted email addresses"""
    try:
        iv_hex, ciphertext_hex = encrypted_text.split(':', 1)
        iv = bytes.fromhex(iv_hex)
        ciphertext = bytes.fromhex(ciphertext_hex)
        
        cipher = AES.new(ENCRYPTION_KEY.encode('utf-8'), AES.MODE_CBC, iv)
        decrypted_bytes = unpad(cipher.decrypt(ciphertext), AES.block_size)
        return decrypted_bytes.decode('utf-8')
    except Exception as e:
        raise ValueError(f"Decryption failed: {str(e)}")

def process_email(email: str) -> str:
    """Decrypt email if encrypted (no @ present)"""
    if not email:
        return email
    if '@' not in email:
        try:
            return decrypt_email(email)
        except Exception as e:
            print(f"Decryption error for {email}: {str(e)}")
            return email
    return email

def send_email(to_email: str, subject: str, html_body: str, reply_to=None, cc=None) -> Tuple[bool, str]:
    try:
        if not to_email or '@' not in to_email:
            return False, "Invalid email address"

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
    raw_emails = [row[0] for row in cur.fetchall() if row[0]]
    emails = [process_email(email) for email in raw_emails]
    cur.close()
    conn.close()
    return [email for email in emails if '@' in email]  

def generate_email_html(message: str, name: str = "User") -> str:
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
            ul {{ list-style-type: none; padding: 0; }}
            li {{ margin-bottom: 10px; padding: 10px; background-color: #f8f9fa; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2>iReserve System Notification</h2>
            </div>
            <div class="content">
                <p>Dear {name},</p>
                {message}
                <p>Best regards,<br>The iReserve Team</p>
            </div>
            <div class="footer">
                <p>&copy; 2025 iReserve Community Sports Facility System</p>
            </div>
        </div>
    </body>
    </html>
    """


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

        # Process recipient email
        processed_email = process_email(data['recipient_email'])
        if '@' not in processed_email:
            return {'status': 'error', 'message': 'Invalid recipient email'}, 400

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
            to_email=processed_email,
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
    @api.doc('send_broadcast_email')
    @api.expect(broadcast_model)
    @api.response(200, 'Success', success_response_model)
    @api.response(400, 'Validation Error', error_response_model)
    @api.response(500, 'Server Error', error_response_model)
    def post(self):
        """Send broadcast emails to selected group"""
        data = request.json

        if not all(key in data for key in ['subject', 'message', 'recipient_type']):
            return {"status": "error", "message": "Missing required fields"}, 400

        try:
            emails = get_recipient_emails(data['recipient_type'])
            if not emails:
                return {"status": "error", "message": "No valid recipients found"}, 404
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

@ns.route('/send-booking-reminders')
class BookingReminder(Resource):
    @api.doc('send_booking_reminders',
             description='Send reminder emails for bookings starting in the next 24 hours.')
    @api.response(200, 'Success', booking_reminder_response)
    @api.response(500, 'Server Error', error_response_model)
    def post(self):
        """Send reminder emails for bookings starting in the next 24 hours"""
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            
          
            query = """
                SELECT 
                    r.resident_id,
                    r.email,
                    r.name,
                    b.booking_id,
                    b.start_time,
                    f.name as facility_name
                FROM 
                    bookings b
                JOIN 
                    residents r ON b.resident_id = r.resident_id
                JOIN 
                    facilities f ON b.facility_id = f.facility_id
                WHERE 
                   b.start_time BETWEEN NOW()::time AND (NOW() + INTERVAL '24 hours')::time
                ORDER BY 
                    r.email, b.start_time
            """
            
            cur.execute(query)
            bookings_data = cur.fetchall()
            total_bookings = len(bookings_data)
            
            if not bookings_data:
                return {
                    'status': 'success',
                    'messages_sent': 0,
                    'total_bookings': 0,
                    'details': ['No bookings starting in the next 24 hours found']
                }, 200
            
            bookings_by_email = {}
            for booking in bookings_data:
                resident_id, email, name, booking_id, start_time, facility_name = booking
                # Decrypt and validate email
                clean_email = process_email(email)
                if '@' not in clean_email:
                    continue
                
                if clean_email not in bookings_by_email:
                    bookings_by_email[clean_email] = {
                        'name': name,
                        'bookings': []
                    }
                bookings_by_email[clean_email]['bookings'].append({
                    'facility_name': facility_name,
                    'start_time': start_time.strftime('%Y-%m-%d %H:%M')
                })
            
            results = []
            emails_sent = 0
            
            for email, data in bookings_by_email.items():
                name = data['name']
                bookings = data['bookings']
                booking_count = len(bookings)
                
                subject = f"Reminder: You have {booking_count} upcoming booking(s)"
                bookings_html = "<ul>" + "".join(
                    f"<li><strong>{b['facility_name']}</strong> at {b['start_time']}</li>"
                    for b in bookings
                ) + "</ul>"
                
                message = f"""
                <p>This is a reminder for your upcoming booking(s):</p>
                {bookings_html}
                <p>Please arrive on time for your booking(s).</p>
                <p>You can view all your bookings in the iReserve system.</p>
                """
                
                html_content = generate_email_html(message, name)
                success, error = send_email(email, subject, html_content)
                
                if success:
                    emails_sent += 1
                    results.append(f"Reminder sent to {email} for {booking_count} booking(s)")
                else:
                    results.append(f"Failed to send reminder to {email}: {error}")
            
            return {
                'status': 'success',
                'messages_sent': emails_sent,
                'total_bookings': total_bookings,
                'details': results
            }, 200
            
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Failed to process booking reminders: {str(e)}'
            }, 500
        finally:
            if 'cur' in locals():
                cur.close()
            if 'conn' in locals():
                conn.close()



api.add_namespace(ns)

if __name__ == '__main__':
    Port = os.getenv("PORT", 5000)
    app.run(debug=True, host='0.0.0.0', port=Port)
from flask import Flask, jsonify, request, session, make_response
from flask_cors import CORS
from supabase import create_client, Client
import os
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
import jwt
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import secrets
import requests
import schedule
import time
import threading
import pytz

load_dotenv()
app = Flask(__name__)
app.secret_key = os.environ['FLASK_SECRET_KEY']

# Configure session settings
app.config.update(
    SESSION_COOKIE_SECURE=True,      # Must be True for SameSite=None (requires HTTPS)
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='None',  # Must be 'None' for cross-site
    PERMANENT_SESSION_LIFETIME=3600  # 1 hour
)

# CORS configuration with credentials support
CORS(app, supports_credentials=True)

SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

JWT_SECRET = os.environ['FLASK_SECRET_KEY']
JWT_ALGORITHM = 'HS256'
JWT_EXP_DELTA_SECONDS = 3600

# Email configuration - Using Gmail SMTP
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 465
EMAIL_USERNAME = 'dgmts.project@gmail.com'
EMAIL_PASSWORD = 'qaegeeqwsuuwtmwb'

# Microsoft 365 configuration (commented out for now)
# SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.office365.com')
# SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
# EMAIL_USERNAME = os.getenv('EMAIL_USERNAME', 'instrumentation@dullesgeotechnical.com')
# EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', 'DGMTS@14155')

# Store reset tokens (in production, use Redis or database)
reset_tokens = {}

# Sensor API configuration
SENSOR_API_BASE = "https://loadsensing.wocs3.com/30846/dataserver/api/v1/data/nodes"
SENSOR_USERNAME = "admin"
SENSOR_PASSWORD = "oNg9ahy3m"
SENSOR_NODES = [142939, 143969]
# Mapping from node_id to instrument_id for tiltmeters
NODE_TO_INSTRUMENT_ID = {142939: "TILT-142939", 143969: "TILT-143969"}

def send_email(to_email, subject, body):
    """Send email using Microsoft 365 SMTP"""
    try:
        msg = MIMEMultipart()
        # Accept both string and list for to_email
        if isinstance(to_email, str):
            recipients = [email.strip() for email in to_email.split(',') if email.strip()]
        else:
            recipients = to_email

        msg['From'] = EMAIL_USERNAME
        msg['To'] = ", ".join(recipients)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        print(f"Attempting to send email to {recipients}")
        print(f"SMTP Server: {SMTP_SERVER}:{SMTP_PORT}")
        print(f"Username: {EMAIL_USERNAME}")
        
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        print("SMTP SSL connection established")
        
        server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
        print("Login successful")
        
        server.sendmail(EMAIL_USERNAME, recipients, msg.as_string())
        print("Email sent successfully")
        
        server.quit()
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f'SMTP Authentication Error: {e}')
        return False
    except smtplib.SMTPException as e:
        print(f'SMTP Error: {e}')
        return False
    except Exception as e:
        print(f'Failed to send email: {e}')
        return False

# Helper to create JWT
def create_jwt(user):
    payload = {
        'user_id': user['id'],
        'email': user['email'],
        'role': user.get('role', 'user'),
        'permissions': {
            'access_to_site': user.get('access_to_site', False),
            'view_graph': user.get('view_graph', False),
            'view_data': user.get('view_data', False),
            'download_graph': user.get('download_graph', False),
            'download_data': user.get('download_data', False)
        },
        'exp': datetime.utcnow() + timedelta(seconds=JWT_EXP_DELTA_SECONDS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

# Helper to decode JWT
def decode_jwt(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def jwt_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', None)
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid Authorization header'}), 401
        token = auth_header.split(' ')[1]
        payload = decode_jwt(token)
        if not payload:
            return jsonify({'error': 'Invalid or expired token'}), 401
        request.user = payload
        return f(*args, **kwargs)
    return decorated

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    try:
        response = supabase.table('users').select('*').eq('email', email).execute()
        user = response.data[0] if response.data else None

        if not user:
            return jsonify({"error": "Invalid credentials"}), 401

        # Check if password is hashed or plain text (for backward compatibility)
        stored_password = user['password']
        if stored_password.startswith('pbkdf2:'):
            # Password is hashed, use check_password_hash
            if not check_password_hash(stored_password, password):
                return jsonify({"error": "Invalid credentials"}), 401
        else:
            # Password is plain text (legacy), compare directly
            if stored_password != password:
                return jsonify({"error": "Invalid credentials"}), 401

        token = create_jwt(user)
        return jsonify({
            "message": "Login successful",
            "token": token,
            "user": {
                "id": user['id'],
                "email": user['email'],
                "role": user.get('role', 'user'),
                "permissions": {
                    "access_to_site": user.get('access_to_site', False),
                    "view_graph": user.get('view_graph', False),
                    "view_data": user.get('view_data', False),
                    "download_graph": user.get('download_graph', False),
                    "download_data": user.get('download_data', False)
                }
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    resp = make_response(jsonify({"message": "Logged out successfully"}))
    resp.delete_cookie('flask_session')
    return resp

@app.route('/')

@app.route('/api/check-auth', methods=['GET'])
@jwt_required
def check_auth():
    user = request.user
    return jsonify({
        "authenticated": True,
        "user": {
            "email": user['email'],
            "role": user['role'],
            "permissions": user.get('permissions', {})
        }
    })

@app.route('/api/protected-route', methods=['GET'])
def protected_route():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify({
        "message": "Protected data",
        "user_email": session['email']
    })

@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json()
    email = data.get('email')
    
    if not email:
        return jsonify({"error": "Email is required"}), 400
    
    try:
        # Check if user exists
        response = supabase.table('users').select('*').eq('email', email).execute()
        user = response.data[0] if response.data else None
        
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        # Generate reset token
        reset_token = secrets.token_urlsafe(32)
        reset_tokens[reset_token] = {
            'email': email,
            'expires': datetime.utcnow() + timedelta(hours=1)
        }
        
        # Create reset link
        reset_link = f"https://dgmts-imsite.dullesgeotechnical.com/reset-password?token={reset_token}"
        
        # Email content
        subject = "Password Reset Request - DGMTS"
        body = f"""
        <html>
        <body>
            <h2>Password Reset Request</h2>
            <p>Hello,</p>
            <p>You have requested to reset your password for your DGMTS account.</p>
            <p>Click the link below to reset your password:</p>
            <p><a href="{reset_link}" style="background-color: #0056d2; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; display: inline-block;">Reset Password</a></p>
            <p>This link will expire in 1 hour.</p>
            <p>If you didn't request this password reset, please ignore this email.</p>
            <p>Best regards,<br>DGMTS Team</p>
        </body>
        </html>
        """
        
        # Send email
        email_sent = send_email(email, subject, body)
        if email_sent:
            return jsonify({"message": "Password reset email sent successfully"})
        else:
            return jsonify({"error": "Failed to send email. Please check the server logs for details."}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/test-email', methods=['POST'])
def test_email():
    """Test endpoint to verify email functionality"""
    data = request.get_json()
    test_email = data.get('email', 'mahmerraza19@gmail.com')
    
    subject = "Test Email - DGMTS"
    body = """
    <html>
    <body>
        <h2>Test Email</h2>
        <p>This is a test email to verify the email functionality is working.</p>
        <p>If you receive this email, the email configuration is correct.</p>
        <p>Best regards,<br>DGMTS Team</p>
    </body>
    </html>
    """
    
    if send_email(test_email, subject, body):
        return jsonify({"message": "Test email sent successfully"})
    else:
        return jsonify({"error": "Failed to send test email"}), 500

@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json()
    token = data.get('token')
    new_password = data.get('new_password')
    confirm_password = data.get('confirm_password')
    
    if not token or not new_password or not confirm_password:
        return jsonify({"error": "Token, new password, and confirm password are required"}), 400
    
    if new_password != confirm_password:
        return jsonify({"error": "Passwords do not match"}), 400
    
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long"}), 400
    
    # Check if token exists and is valid
    if token not in reset_tokens:
        return jsonify({"error": "Invalid or expired token"}), 400
    
    token_data = reset_tokens[token]
    
    # Check if token is expired
    if datetime.utcnow() > token_data['expires']:
        del reset_tokens[token]
        return jsonify({"error": "Token has expired"}), 400
    
    try:
        # Update password in database (storing as plain text to match current system)
        response = supabase.table('users').update({
            'password': new_password
        }).eq('email', token_data['email']).execute()
        
        # Remove used token
        del reset_tokens[token]
        
        return jsonify({"message": "Password reset successfully"})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/migrate-passwords', methods=['POST'])
def migrate_passwords():
    try:
        users = supabase.table('users').select('*').execute()
        for user in users.data:
            if not user['password'].startswith('pbkdf2:'):
                supabase.table('users').update({
                    'password': generate_password_hash(user['password'])
                }).eq('id', user['id']).execute()
        return jsonify({"message": "Password migration complete"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Sensor data functions
def fetch_sensor_data_from_api(node_id):
    """Fetch sensor data from external API with basic auth"""
    try:
        url = f"{SENSOR_API_BASE}/{node_id}"
        print(f"Fetching data from: {url}")
        response = requests.get(url, auth=(SENSOR_USERNAME, SENSOR_PASSWORD))
        
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Received {len(data)} records for node {node_id}")
            return data
        else:
            print(f"API request failed for node {node_id}: {response.status_code}")
            print(f"Response text: {response.text}")
            return []
    except Exception as e:
        print(f"Error fetching data for node {node_id}: {e}")
        return []

def store_sensor_data(data, node_id):
    """Store simplified sensor data in Supabase"""
    try:
        print(f"Processing {len(data)} records for node {node_id}")
        stored_count = 0
        
        for i, reading in enumerate(data):
            # Only process til90ReadingsV1
            if reading.get('type') != 'til90ReadingsV1':
                print(f"Skipping record {i+1}: type is {reading.get('type')}")
                continue

            value = reading.get('value', {})
            readings = value.get('readings', [])
            timestamp = value.get('readTimestamp')

            if not readings or not timestamp:
                print(f"Skipping record {i+1}: missing readings or timestamp")
                print(f"DEBUG: reading={reading}")
                continue

            # Extract x, y, z values from channels
            x_value = y_value = z_value = None
            for channel_reading in readings:
                channel = channel_reading.get('channel')
                tilt = channel_reading.get('tilt')
                if channel == 0:
                    x_value = tilt
                elif channel == 1:
                    y_value = tilt
                elif channel == 2:
                    z_value = tilt

            print(f"Extracted values - X: {x_value}, Y: {y_value}, Z: {z_value}")

            # Prepare data for insertion
            sensor_data = {
                'node_id': node_id,
                'timestamp': timestamp,
                'x_value': x_value,
                'y_value': y_value,
                'z_value': z_value
            }
            print(f"Inserting data: {sensor_data}")
            response = supabase.table('sensor_readings').insert(sensor_data).execute()
            print(f"Insert response: {response}")
            stored_count += 1
        print(f"Successfully stored {stored_count} records for node {node_id}")
        return True
    except Exception as e:
        print(f"Error storing sensor data: {e}")
        import traceback
        traceback.print_exc()
        return False

def fetch_and_store_all_sensor_data():
    """Fetch and store data for all nodes"""
    print("Starting fetch_and_store_all_sensor_data...")
    for node_id in SENSOR_NODES:
        print(f"\n=== Processing Node {node_id} ===")
        data = fetch_sensor_data_from_api(node_id)
        if data:
            print(f"Data received for node {node_id}, attempting to store...")
            if store_sensor_data(data, node_id):
                print(f"Successfully stored data for node {node_id}")
            else:
                print(f"Failed to store data for node {node_id}")
        else:
            print(f"No data received for node {node_id}")
    print("Completed fetch_and_store_all_sensor_data")

def check_and_send_tiltmeter_alerts():
    print("Checking tiltmeter alerts for both nodes...")
    try:
        node_ids = [142939, 143969]
        node_alerts = {}

        for node_id in node_ids:
            instrument_id = NODE_TO_INSTRUMENT_ID.get(node_id)
            if not instrument_id:
                print(f"No instrument_id mapping for node {node_id}")
                continue
            # 1. Get instrument settings for this node's instrument_id
            instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
            instrument = instrument_resp.data[0] if instrument_resp.data else None
            if not instrument:
                print(f"No instrument found for {instrument_id}")
                continue

            alert_value = instrument.get('alert_value')
            warning_value = instrument.get('warning_value')
            shutdown_value = instrument.get('shutdown_value')
            alert_emails = instrument.get('alert_emails') or []
            warning_emails = instrument.get('warning_emails') or []
            shutdown_emails = instrument.get('shutdown_emails') or []

            one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            readings_resp = supabase.table('sensor_readings') \
                .select('*') \
                .eq('node_id', node_id) \
                .gte('timestamp', one_hour_ago) \
                .order('timestamp', desc=False) \
                .execute()
            readings = readings_resp.data if readings_resp.data else []

            node_messages = []
            for reading in readings:
                timestamp = reading['timestamp']
                x = reading.get('x_value')
                y = reading.get('y_value')
                z = reading.get('z_value')

                # Check if we've already sent for this timestamp (use correct instrument_id)
                already_sent = supabase.table('sent_alerts') \
                    .select('id') \
                    .eq('instrument_id', instrument_id) \
                    .eq('node_id', node_id) \
                    .eq('timestamp', timestamp) \
                    .execute()
                if already_sent.data:
                    print(f"Alert already sent for node {node_id} at {timestamp}, skipping.")
                    continue

                # Format timestamp to EST
                try:
                    dt_utc = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    est = pytz.timezone('US/Eastern')
                    dt_est = dt_utc.astimezone(est)
                    formatted_time = dt_est.strftime('%Y-%m-%d %I:%M %p EST')
                except Exception as e:
                    print(f"Failed to parse/convert timestamp: {timestamp}, error: {e}")
                    formatted_time = timestamp

                messages = []
                for axis, value in [('X', x), ('Y', y), ('Z', z)]:
                    if value is None:
                        continue
                    if shutdown_value and abs(value) >= shutdown_value:
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis:</b> {value} at {formatted_time}")
                for axis, value in [('X', x), ('Y', y), ('Z', z)]:
                    if value is None:
                        continue
                    if warning_value and abs(value) >= warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis:</b> {value} at {formatted_time}")
                for axis, value in [('X', x), ('Y', y), ('Z', z)]:
                    if value is None:
                        continue
                    if alert_value and abs(value) >= alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis:</b> {value} at {formatted_time}")

                if messages:
                    node_messages.append(f"<u><b>Timestamp: {formatted_time}</b></u><br>" + "<br>".join(messages))
                    # Record that we've sent for this timestamp (use correct instrument_id)
                    supabase.table('sent_alerts').insert({
                        'instrument_id': instrument_id,
                        'node_id': node_id,
                        'timestamp': timestamp,
                        'alert_type': 'any'
                    }).execute()

            if node_messages:
                node_alerts[node_id] = node_messages

        if node_alerts:
            body = ""
            for node_id in node_ids:
                if node_id in node_alerts:
                    body += f"<h3>Alerts for Node {node_id}</h3>\n"
                    body += "<br><br>".join(node_alerts[node_id])
                    body += "<br><br>"
            subject = "Tiltmeter Alert(s) for the Last Hour"
            # Collect all emails from all instruments
            all_emails = set()
            for node_id in node_ids:
                instrument_id = NODE_TO_INSTRUMENT_ID.get(node_id)
                if not instrument_id:
                    continue
                instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
                instrument = instrument_resp.data[0] if instrument_resp.data else None
                if not instrument:
                    continue
                all_emails.update(instrument.get('alert_emails') or [])
                all_emails.update(instrument.get('warning_emails') or [])
                all_emails.update(instrument.get('shutdown_emails') or [])
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print("Sent alert email for both nodes")
            else:
                print("No alert/warning/shutdown emails configured for tiltmeters")
        else:
            print("No alerts to send for either node in the last hour.")
    except Exception as e:
        print(f"Error in check_and_send_tiltmeter_alerts: {e}")

def check_and_send_seismograph_alert():
    print("Checking seismograph alerts...")
    try:
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'SMG1').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for SMG-1")
            return

        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')
        alert_emails = instrument.get('alert_emails') or []
        warning_emails = instrument.get('warning_emails') or []
        shutdown_emails = instrument.get('shutdown_emails') or []

        # 2. Fetch latest event from Syscom API
        api_key = os.environ.get('SYSCOM_API_KEY')
        if not api_key:
            print("No SYSCOM_API_KEY set in environment")
            return

        url = "https://scs.syscom-instruments.com/public-api/v1/records/events/latest"
        headers = {"x-scs-api-key": api_key}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Failed to fetch latest event: {response.status_code} {response.text}")
            return

        event = response.json()
        trigger_time = event.get('triggerTime')
        peakX = event.get('peakX')
        peakY = event.get('peakY')
        peakZ = event.get('peakZ')
        event_id = event.get('id')
        node_id = 15092  # Seismograph device

        if not trigger_time or peakX is None or peakY is None or peakZ is None:
            print("Incomplete event data, skipping.")
            return

        # 3. Check if we've already sent for this triggerTime
        already_sent = supabase.table('sent_alerts') \
            .select('id') \
            .eq('instrument_id', 'SMG-1') \
            .eq('node_id', node_id) \
            .eq('timestamp', trigger_time) \
            .execute()
        if already_sent.data:
            print(f"Alert already sent for event at {trigger_time}, skipping.")
            return

        # 4. Compare to thresholds
        messages = []
        for axis, value in [('X', peakX), ('Y', peakY), ('Z', peakZ)]:
            if shutdown_value and abs(value) >= shutdown_value:
                messages.append(f"<b>Shutdown threshold reached on {axis}-axis:</b> {value}")
        for axis, value in [('X', peakX), ('Y', peakY), ('Z', peakZ)]:
            if warning_value and abs(value) >= warning_value:
                messages.append(f"<b>Warning threshold reached on {axis}-axis:</b> {value}")
        for axis, value in [('X', peakX), ('Y', peakY), ('Z', peakZ)]:
            if alert_value and abs(value) >= alert_value:
                messages.append(f"<b>Alert threshold reached on {axis}-axis:</b> {value}")

        if messages:
            # Format trigger_time to EST
            try:
                dt_utc = datetime.fromisoformat(trigger_time.replace('Z', '+00:00'))
                est = pytz.timezone('US/Eastern')
                dt_est = dt_utc.astimezone(est)
                formatted_time = dt_est.strftime('%Y-%m-%d %I:%M %p EST')
            except Exception as e:
                print(f"Failed to parse/convert trigger_time: {trigger_time}, error: {e}")
                formatted_time = trigger_time

            subject = f"Seismograph Alert(s) at {formatted_time}"
            body = f"<u><b>Event ID: {event_id}</b></u><br><b>Timestamp:</b> {formatted_time}<br>" + "<br>".join(messages)
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print(f"Sent seismograph alert email for event at {trigger_time}")
                # Record that we've sent for this event
                supabase.table('sent_alerts').insert({
                    'instrument_id': 'SMG-1',
                    'node_id': node_id,
                    'timestamp': trigger_time,
                    'alert_type': 'any'
                }).execute()
            else:
                print("No alert/warning/shutdown emails configured for SMG-1")
        else:
            print("No thresholds crossed for latest event.")
    except Exception as e:
        print(f"Error in check_and_send_seismograph_alert: {e}")

# API endpoints for sensor data
@app.route('/api/sensor-data/<int:node_id>', methods=['GET'])
def api_get_sensor_data(node_id):
    """API endpoint to get sensor data"""
    try:
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')
        limit = int(request.args.get('limit', 1000))
        
        query = supabase.table('sensor_readings').select('*').eq('node_id', node_id)
        
        if start_time:
            query = query.gte('timestamp', start_time)
        if end_time:
            query = query.lte('timestamp', end_time)
            
        query = query.order('timestamp', desc=True).limit(limit)
        response = query.execute()
        
        return jsonify(response.data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/fetch-sensor-data', methods=['POST'])
def api_fetch_sensor_data():
    """Manually trigger sensor data fetch"""
    try:
        fetch_and_store_all_sensor_data()
        return jsonify({"message": "Sensor data fetch completed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Schedule sensor data collection
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(60)

# Schedule to run every hour
schedule.every().hour.do(fetch_and_store_all_sensor_data)
schedule.every().hour.do(check_and_send_tiltmeter_alerts)
schedule.every().hour.do(check_and_send_seismograph_alert)

# Start scheduler in background
scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
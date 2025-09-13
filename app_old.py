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

@app.route('/api/test-tiltmeter-alert', methods=['POST'])
def test_tiltmeter_alert():
    """Test endpoint to send a sample tiltmeter alert email using actual data"""
    try:
        # Get email addresses from request body
        data = request.get_json() or {}
        test_emails = data.get('emails', ['mahmerraza19@gmail.com'])
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = ['mahmerraza19@gmail.com']
        # Get latest sensor readings for both nodes
        node_ids = [142939, 143969]
        actual_alerts = {}
        
        for node_id in node_ids:
            instrument_id = NODE_TO_INSTRUMENT_ID.get(node_id)
            if not instrument_id:
                continue
                
            # Get instrument settings
            instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
            instrument = instrument_resp.data[0] if instrument_resp.data else None
            if not instrument:
                continue
            
            # Get reference values
            reference_resp = supabase.table('reference_values').select('*').eq('instrument_id', instrument_id).execute()
            reference_values = reference_resp.data[0] if reference_resp.data else None
            
            # Get latest sensor reading for this node
            latest_resp = supabase.table('sensor_readings') \
                .select('*') \
                .eq('node_id', node_id) \
                .order('timestamp', desc=True) \
                .limit(1) \
                .execute()
            latest_reading = latest_resp.data[0] if latest_resp.data else None
            
            # Get threshold values
            xyz_alert_values = instrument.get('x_y_z_alert_values')
            xyz_warning_values = instrument.get('x_y_z_warning_values')
            xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
            
            print(f"DEBUG TEST {node_id}: Latest reading found: {latest_reading is not None}")
            print(f"DEBUG TEST {node_id}: xyz_alert_values={xyz_alert_values}")
            print(f"DEBUG TEST {node_id}: reference_values enabled={reference_values.get('enabled', False) if reference_values else False}")
            
            if not latest_reading:
                continue
            
            # Process the latest reading
            timestamp = latest_reading['timestamp']
            x = latest_reading.get('x_value')
            y = latest_reading.get('y_value')
            z = latest_reading.get('z_value')
            
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
            
            # Calculate calibrated values when reference values are enabled
            if reference_values and reference_values.get('enabled', False):
                ref_x = reference_values.get('reference_x_value', 0)
                ref_y = reference_values.get('reference_y_value', 0)
                ref_z = reference_values.get('reference_z_value', 0)
                
                # Convert to float to ensure proper calculation
                ref_x = float(ref_x) if ref_x is not None else 0.0
                ref_y = float(ref_y) if ref_y is not None else 0.0
                ref_z = float(ref_z) if ref_z is not None else 0.0
                
                # Calculate calibrated values (raw - reference)
                calibrated_x = float(x) - ref_x if x is not None else None
                calibrated_y = float(y) - ref_y if y is not None else None
                calibrated_z = float(z) - ref_z if z is not None else None
                
                print(f"DEBUG TEST {node_id}: Calibrated values - x={calibrated_x}, y={calibrated_y}, z={calibrated_z}")
                print(f"DEBUG TEST {node_id}: Reference values - ref_x={ref_x}, ref_y={ref_y}, ref_z={ref_z}")
                
                # Use original (unadjusted) thresholds for comparison
                base_xyz_alert_values = instrument.get('x_y_z_alert_values')
                base_xyz_warning_values = instrument.get('x_y_z_warning_values')
                base_xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
                
                # Check shutdown thresholds using calibrated values (X and Z only, no Y)
                for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                    if calibrated_value is None:
                        continue
                    axis_shutdown_value = base_xyz_shutdown_values.get(axis_key) if base_xyz_shutdown_values else None
                    if axis_shutdown_value and abs(calibrated_value) >= axis_shutdown_value:
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}")
                
                # Check warning thresholds using calibrated values (X and Z only, no Y)
                for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                    if calibrated_value is None:
                        continue
                    axis_warning_value = base_xyz_warning_values.get(axis_key) if base_xyz_warning_values else None
                    if axis_warning_value and abs(calibrated_value) >= axis_warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}")
                
                # Check alert thresholds using calibrated values (X and Z only, no Y)
                for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                    if calibrated_value is None:
                        continue
                    axis_alert_value = base_xyz_alert_values.get(axis_key) if base_xyz_alert_values else None
                    print(f"DEBUG TEST {node_id} {axis}: calibrated_value={calibrated_value}, axis_alert_value={axis_alert_value}, abs(calibrated_value)={abs(calibrated_value)}, threshold_check={abs(calibrated_value) >= axis_alert_value if axis_alert_value else False}")
                    if axis_alert_value and abs(calibrated_value) >= axis_alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}")
            else:
                print(f"DEBUG TEST {node_id}: Reference values not enabled, using raw values")
                print(f"DEBUG TEST {node_id}: Raw values - x={x}, y={y}, z={z}")
                print(f"DEBUG TEST {node_id}: Threshold values - alert={xyz_alert_values}, warning={xyz_warning_values}, shutdown={xyz_shutdown_values}")
                
                # Use original logic when reference values are not enabled (X and Z only, no Y)
                # Check shutdown thresholds
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    axis_shutdown_value = xyz_shutdown_values.get(axis_key) if xyz_shutdown_values else None
                    if axis_shutdown_value and abs(value) >= axis_shutdown_value:
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {value:.6f} at {formatted_time}")
                
                # Check warning thresholds
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    axis_warning_value = xyz_warning_values.get(axis_key) if xyz_warning_values else None
                    if axis_warning_value and abs(value) >= axis_warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {value:.6f} at {formatted_time}")
                
                # Check alert thresholds
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    axis_alert_value = xyz_alert_values.get(axis_key) if xyz_alert_values else None
                    if axis_alert_value and abs(value) >= axis_alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {value:.6f} at {formatted_time}")
            
            if messages:
                node_messages = [f"<u><b>Timestamp: {formatted_time}</b></u><br>" + "<br>".join(messages)]
                actual_alerts[node_id] = node_messages
        
        # If no actual alerts found, return empty response
        if not actual_alerts:
            return jsonify({
                "message": "No tiltmeter alerts found in latest readings. No email sent.",
                "note": "Only sends emails when actual thresholds are exceeded"
            })
        
        # Create email body with professional styling
        body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }}
                .header {{ background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 20px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 24px; font-weight: bold; }}
                .header p {{ margin: 5px 0 0 0; opacity: 0.9; }}
                .content {{ padding: 30px; }}
                .alert-section {{ margin-bottom: 25px; }}
                .alert-section h3 {{ color: #0056d2; border-bottom: 2px solid #0056d2; padding-bottom: 10px; margin-bottom: 15px; }}
                .alert-item {{ background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }}
                .alert-item.warning {{ border-left-color: #ffc107; }}
                .alert-item.alert {{ border-left-color: #fd7e14; }}
                .alert-item.shutdown {{ border-left-color: #dc3545; }}
                .timestamp {{ font-weight: bold; color: #495057; margin-bottom: 10px; }}
                .alert-message {{ color: #212529; line-height: 1.5; }}
                .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }}
                .footer p {{ margin: 0; }}
                .company-info {{ font-weight: bold; color: #0056d2; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🚨 TILTMETER ALERT NOTIFICATION</h1>
                    <p>Dulles Geotechnical Monitoring System</p>
                </div>
                
                <div class="content">
                    <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                        This is an automated alert notification from the DGMTS monitoring system. 
                        The following tiltmeter thresholds have been exceeded in the latest readings:
                    </p>
        """
        
        # Add alerts for each node
        for node_id, alerts in actual_alerts.items():
            body += f"""
                    <div class="alert-section">
                        <h3>📊 Node {node_id} - Tiltmeter Alerts</h3>
            """
            
            for alert in alerts:
                # Determine alert type for styling
                alert_class = "alert-item"
                if "Shutdown" in alert:
                    alert_class += " shutdown"
                elif "Warning" in alert:
                    alert_class += " warning"
                elif "Alert" in alert:
                    alert_class += " alert"
                elif "Test Alert" in alert:
                    alert_class += " alert"  # Use alert styling for test alerts
                
                # Extract timestamp and message
                alert_parts = alert.split('<br>')
                timestamp = alert_parts[0].replace('<u><b>', '').replace('</b></u>', '')
                message = '<br>'.join(alert_parts[1:]) if len(alert_parts) > 1 else alert
                
                body += f"""
                        <div class="{alert_class}">
                            <div class="timestamp">{timestamp}</div>
                            <div class="alert-message">{message}</div>
                        </div>
                """
            
            body += """
                    </div>
            """
        
        body += f"""
                    <div style="background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-top: 20px;">
                        <p style="margin: 0; color: #0056d2; font-weight: bold;">⚠️ Action Required:</p>
                        <p style="margin: 5px 0 0 0; color: #495057;">
                            Please review the tiltmeter data and take appropriate action if necessary.                    
                        </p>
                    </div>
                </div>
                
                <div class="footer">
                    <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                    <p style="font-size: 12px; margin-top: 5px;">
                        This is an automated message. Please do not reply to this email.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        current_time = datetime.now(timezone.utc)
        est = pytz.timezone('US/Eastern')
        current_time_est = current_time.astimezone(est)
        formatted_current_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
        
        subject = f"🚨 Tiltmeter Alert Notification - {formatted_current_time}"
        
        # Send to test emails
        if send_email(test_emails, subject, body):
            return jsonify({
                "message": f"Tiltmeter alert email sent successfully to {', '.join(test_emails)}",
                "subject": subject,
                "note": "This shows actual threshold breaches from latest readings",
                "emails_sent_to": test_emails
            })
        else:
            return jsonify({"error": "Failed to send tiltmeter alert email"}), 500
            
    except Exception as e:
        return jsonify({"error": f"Failed to send tiltmeter alert: {str(e)}"}), 500

@app.route('/api/test-seismograph-alert', methods=['POST'])
def test_seismograph_alert():
    """Test endpoint to send a sample seismograph alert email"""
    try:
        # Get email addresses from request body
        data = request.get_json() or {}
        test_emails = data.get('emails', ['mahmerraza19@gmail.com'])
        seismograph_type = data.get('type', 'SMG1')  # SMG1 or SMG-3
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = ['mahmerraza19@gmail.com']
        
        # Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', seismograph_type).execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            return jsonify({"error": f"No instrument found for {seismograph_type}"}), 404
        
        # Create test alert data
        test_alerts = {
            'test_hour': {
                'messages': [
                    "<b>Test Alert threshold reached on X-axis:</b> 0.001234",
                    "<b>Test Warning threshold reached on Y-axis:</b> 0.002345",
                    "<b>Test Shutdown threshold reached on Z-axis:</b> 0.003456"
                ],
                'timestamp': datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d %I:%M %p EST'),
                'max_values': {'X': 0.001234, 'Y': 0.002345, 'Z': 0.003456}
            }
        }
        
        # Create email body
        seismograph_name = "ANC DAR-BC Seismograph" if seismograph_type == "SMG-3" else "Seismograph"
        body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }}
                .header {{ background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 20px; text-align: center; }}
                .header h1 {{ margin: 0; font-size: 24px; font-weight: bold; }}
                .header p {{ margin: 5px 0 0 0; opacity: 0.9; }}
                .content {{ padding: 30px; }}
                .alert-section {{ margin-bottom: 25px; }}
                .alert-section h3 {{ color: #0056d2; border-bottom: 2px solid #0056d2; padding-bottom: 10px; margin-bottom: 15px; }}
                .alert-item {{ background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }}
                .alert-item.warning {{ border-left-color: #ffc107; }}
                .alert-item.alert {{ border-left-color: #fd7e14; }}
                .alert-item.shutdown {{ border-left-color: #dc3545; }}
                .timestamp {{ font-weight: bold; color: #495057; margin-bottom: 10px; }}
                .alert-message {{ color: #212529; line-height: 1.5; }}
                .max-values {{ background-color: #e9ecef; padding: 10px; border-radius: 4px; margin-top: 10px; }}
                .max-values table {{ width: 100%; border-collapse: collapse; }}
                .max-values th, .max-values td {{ padding: 8px; text-align: center; border: 1px solid #dee2e6; }}
                .max-values th {{ background-color: #f8f9fa; font-weight: bold; }}
                .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }}
                .footer p {{ margin: 0; }}
                .company-info {{ font-weight: bold; color: #0056d2; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🌊 {seismograph_name.upper()} ALERT NOTIFICATION</h1>
                    <p>Dulles Geotechnical Monitoring System</p>
                </div>
                
                <div class="content">
                    <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                        This is a <strong>TEST</strong> alert notification from the DGMTS monitoring system. 
                        The following {seismograph_name} ({seismograph_type}) thresholds have been exceeded:
                    </p>
        """
        
        # Add alerts for each hour
        for hour_key, alert_data in test_alerts.items():
            body += f"""
                    <div class="alert-section">
                        <h3>📊 Hour: {hour_key.replace('_', ' ').title()} - {seismograph_name} Alerts</h3>
            """
            
            for message in alert_data['messages']:
                # Determine alert type for styling
                alert_class = "alert-item"
                if "Shutdown" in message:
                    alert_class += " shutdown"
                elif "Warning" in message:
                    alert_class += " warning"
                elif "Alert" in message:
                    alert_class += " alert"
                
                body += f"""
                        <div class="{alert_class}">
                            <div class="timestamp">{alert_data['timestamp']}</div>
                            <div class="alert-message">{message}</div>
                            <div class="max-values">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Axis</th>
                                            <th>Max Value (in/s)</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        <tr>
                                            <td>X (Longitudinal)</td>
                                            <td>{alert_data['max_values']['X']:.6f}</td>
                                        </tr>
                                        <tr>
                                            <td>Y (Vertical)</td>
                                            <td>{alert_data['max_values']['Y']:.6f}</td>
                                        </tr>
                                        <tr>
                                            <td>Z (Transverse)</td>
                                            <td>{alert_data['max_values']['Z']:.6f}</td>
                                        </tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                """
            
            body += """
                    </div>
            """
        
        body += f"""
                    <div style="background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-top: 20px;">
                        <p style="margin: 0; color: #0056d2; font-weight: bold;">⚠️ Action Required:</p>
                        <p style="margin: 5px 0 0 0; color: #495057;">
                            Please review the {seismograph_name} data and take appropriate action if necessary. 
                            This is a test email to verify the alert system is working correctly.
                        </p>
                    </div>
                </div>
                
                <div class="footer">
                    <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                    <p style="font-size: 12px; margin-top: 5px;">
                        This is a test message. Please do not reply to this email.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        current_time = datetime.now(timezone.utc)
        current_time_est = current_time.astimezone(pytz.timezone('US/Eastern'))
        formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
        subject = f"🌊 {seismograph_name} Test Alert Notification - {formatted_time}"
        
        if send_email(test_emails, subject, body):
            return jsonify({"message": f"Test {seismograph_name} alert email sent successfully"})
        else:
            return jsonify({"error": f"Failed to send test {seismograph_name} alert email"}), 500
            
    except Exception as e:
        print(f"Error in test_seismograph_alert: {e}")
        return jsonify({"error": str(e)}), 500

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
            
            # 1. First check reference_values table for this instrument
            reference_resp = supabase.table('reference_values').select('*').eq('instrument_id', instrument_id).execute()
            reference_values = reference_resp.data[0] if reference_resp.data else None
            
            # 2. Get instrument settings for this node's instrument_id
            instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
            instrument = instrument_resp.data[0] if instrument_resp.data else None
            if not instrument:
                print(f"No instrument found for {instrument_id}")
                continue

            # 3. Determine which threshold values to use
            if reference_values and reference_values.get('enabled', False):
                # Use reference values when enabled
                print(f"Using reference values for {instrument_id}")
                # When reference values are enabled, we'll use calibrated values (raw - reference)
                # and compare against original thresholds in the comparison logic
                # For tiltmeters, use ONLY XYZ values
                if instrument_id in ['TILT-142939', 'TILT-143969']:
                    xyz_alert_values = instrument.get('x_y_z_alert_values')
                    xyz_warning_values = instrument.get('x_y_z_warning_values')
                    xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
                    # Tiltmeters should not use single values
                    alert_value = None
                    warning_value = None
                    shutdown_value = None
                else:
                    # For non-tiltmeters, use ONLY single values
                    xyz_alert_values = None
                    xyz_warning_values = None
                    xyz_shutdown_values = None
                    alert_value = instrument.get('alert_value')
                    warning_value = instrument.get('warning_value')
                    shutdown_value = instrument.get('shutdown_value')
            else:
                # Use original instrument values when reference values are not enabled
                print(f"Using original instrument values for {instrument_id}")
                # For tiltmeters, use ONLY XYZ values
                if instrument_id in ['TILT-142939', 'TILT-143969']:
                    xyz_alert_values = instrument.get('x_y_z_alert_values')
                    xyz_warning_values = instrument.get('x_y_z_warning_values')
                    xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
                    # Tiltmeters should not use single values
                    alert_value = None
                    warning_value = None
                    shutdown_value = None
                else:
                    # For non-tiltmeters, use ONLY single values
                    xyz_alert_values = None
                    xyz_warning_values = None
                    xyz_shutdown_values = None
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
                
                # Calculate calibrated values when reference values are enabled
                if reference_values and reference_values.get('enabled', False):
                    ref_x = reference_values.get('reference_x_value', 0)
                    ref_y = reference_values.get('reference_y_value', 0)
                    ref_z = reference_values.get('reference_z_value', 0)
                    
                    # Calculate calibrated values (raw - reference) to match frontend logic
                    calibrated_x = x - ref_x if x is not None else None
                    calibrated_y = y - ref_y if y is not None else None
                    calibrated_z = z - ref_z if z is not None else None
                    
                    print(f"Reference values enabled for {instrument_id}: X={ref_x}, Y={ref_y}, Z={ref_z}")
                    print(f"Raw values: X={x}, Y={y}, Z={z}")
                    print(f"Calibrated values: X={calibrated_x}, Y={calibrated_y}, Z={calibrated_z}")
                    
                    # Use original (unadjusted) thresholds for comparison
                    base_xyz_alert_values = instrument.get('x_y_z_alert_values')
                    base_xyz_warning_values = instrument.get('x_y_z_warning_values')
                    base_xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
                    
                    # Check shutdown thresholds using calibrated values (X and Z only, no Y)
                    for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                        if calibrated_value is None:
                            continue
                        axis_shutdown_value = base_xyz_shutdown_values.get(axis_key) if base_xyz_shutdown_values else None
                        if axis_shutdown_value and abs(calibrated_value) >= axis_shutdown_value:
                            messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
                    
                    # Check warning thresholds using calibrated values (X and Z only, no Y)
                    for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                        if calibrated_value is None:
                            continue
                        axis_warning_value = base_xyz_warning_values.get(axis_key) if base_xyz_warning_values else None
                        if axis_warning_value and abs(calibrated_value) >= axis_warning_value:
                            messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
                    
                    # Check alert thresholds using calibrated values (X and Z only, no Y)
                    for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                        if calibrated_value is None:
                            continue
                        axis_alert_value = base_xyz_alert_values.get(axis_key) if base_xyz_alert_values else None
                        if axis_alert_value and abs(calibrated_value) >= axis_alert_value:
                            messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
                else:
                    # Use original logic when reference values are not enabled (X and Z only, no Y)
                    # Check shutdown thresholds
                    for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                        if value is None:
                            continue
                        # For tiltmeters, use ONLY XYZ-specific values
                        axis_shutdown_value = xyz_shutdown_values.get(axis_key) if xyz_shutdown_values else None
                        if axis_shutdown_value and abs(value) >= axis_shutdown_value:
                            messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {value:.6f} at {formatted_time}</b>")
                    
                    # Check warning thresholds
                    for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                        if value is None:
                            continue
                        # For tiltmeters, use ONLY XYZ-specific values
                        axis_warning_value = xyz_warning_values.get(axis_key) if xyz_warning_values else None
                        if axis_warning_value and abs(value) >= axis_warning_value:
                            messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {value:.6f} at {formatted_time}</b>")
                    
                    # Check alert thresholds
                    for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                        if value is None:
                            continue
                        # For tiltmeters, use ONLY XYZ-specific values
                        axis_alert_value = xyz_alert_values.get(axis_key) if xyz_alert_values else None
                        if axis_alert_value and abs(value) >= axis_alert_value:
                            messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {value:.6f} at {formatted_time}</b>")

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
            # Create email body with professional styling
            body = """
            <html>
            <head>
                <style>
                    body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }
                    .container { max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }
                    .header { background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 20px; text-align: center; }
                    .header h1 { margin: 0; font-size: 24px; font-weight: bold; }
                    .header p { margin: 5px 0 0 0; opacity: 0.9; }
                    .content { padding: 30px; }
                    .alert-section { margin-bottom: 25px; }
                    .alert-section h3 { color: #0056d2; border-bottom: 2px solid #0056d2; padding-bottom: 10px; margin-bottom: 15px; }
                    .alert-item { background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }
                    .alert-item.warning { border-left-color: #ffc107; }
                    .alert-item.alert { border-left-color: #fd7e14; }
                    .alert-item.shutdown { border-left-color: #dc3545; }
                    .timestamp { font-weight: bold; color: #495057; margin-bottom: 10px; }
                    .alert-message { color: #212529; line-height: 1.5; }
                    .max-values { background-color: #e9ecef; padding: 10px; border-radius: 4px; margin-top: 10px; }
                    .max-values table { width: 100%; border-collapse: collapse; }
                    .max-values th, .max-values td { padding: 8px; text-align: center; border: 1px solid #dee2e6; }
                    .max-values th { background-color: #f8f9fa; font-weight: bold; }
                    .footer { background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }
                    .footer p { margin: 0; }
                    .company-info { font-weight: bold; color: #0056d2; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>🚨 TILTMETER ALERT NOTIFICATION</h1>
                        <p>Dulles Geotechnical Monitoring System</p>
                    </div>
                    
                    <div class="content">
                        <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                            This is an automated alert notification from the DGMTS monitoring system. 
                            The following tiltmeter thresholds have been exceeded in the last hour:
                        </p>
            """
            
            # Add alerts for each node
            for node_id in node_ids:
                if node_id in node_alerts:
                    body += f"""
                        <div class="alert-section">
                            <h3>📊 Node {node_id} - Tiltmeter Alerts</h3>
                    """
                    
                    for alert in node_alerts[node_id]:
                        # Determine alert type for styling
                        alert_class = "alert-item"
                        if "Shutdown" in alert:
                            alert_class += " shutdown"
                        elif "Warning" in alert:
                            alert_class += " warning"
                        elif "Alert" in alert:
                            alert_class += " alert"
                        
                        # Extract timestamp and message
                        alert_parts = alert.split('<br>')
                        timestamp = alert_parts[0].replace('<u><b>', '').replace('</b></u>', '')
                        message = '<br>'.join(alert_parts[1:]) if len(alert_parts) > 1 else alert
                        
                        body += f"""
                            <div class="{alert_class}">
                                <div class="timestamp">{timestamp}</div>
                                <div class="alert-message">{message}</div>
                            </div>
                        """
                    
                    body += """
                        </div>
                    """
            
            body += """
                        <div style="background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-top: 20px;">
                            <p style="margin: 0; color: #0056d2; font-weight: bold;">⚠️ Action Required:</p>
                            <p style="margin: 5px 0 0 0; color: #495057;">
                                Please review the tiltmeter data and take appropriate action if necessary. 
                            </p>
                        </div>
                    </div>
                    
                    <div class="footer">
                        <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                        <p style="font-size: 12px; margin-top: 5px;">
                            This is an automated message. Please do not reply to this email.
                        </p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            current_time = datetime.now(timezone.utc)
            est = pytz.timezone('US/Eastern')
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"🚨 Tiltmeter Alert Notification - {formatted_time}"
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
    print("Checking seismograph alerts using background API...")
    try:
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'SMG1').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for SMG1")
            return

        # For seismograph, use ONLY single values (not a tiltmeter)
        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')
        
        alert_emails = instrument.get('alert_emails') or []
        warning_emails = instrument.get('warning_emails') or []
        shutdown_emails = instrument.get('shutdown_emails') or []

        # 2. Calculate time range for the last hour in EST
        est = pytz.timezone('US/Eastern')
        now_est = datetime.now(est)
        one_hour_ago_est = now_est - timedelta(hours=1)
        
        # Format dates for API
        start_time = one_hour_ago_est.strftime('%Y-%m-%dT%H:%M:%S')
        end_time = now_est.strftime('%Y-%m-%dT%H:%M:%S')
        
        print(f"Fetching seismograph data from {start_time} to {end_time} EST")

        # 3. Fetch background data from Syscom API
        api_key = os.environ.get('SYSCOM_API_KEY')
        if not api_key:
            print("No SYSCOM_API_KEY set in environment")
            return

        url = f"https://scs.syscom-instruments.com/public-api/v1/records/background/15092/data?start={start_time}&end={end_time}"
        headers = {"x-scs-api-key": api_key}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Failed to fetch background data: {response.status_code} {response.text}")
            return

        data = response.json()
        background_data = data.get('data', [])
        
        if not background_data:
            print("No background data received for the last hour")
            return

        print(f"Received {len(background_data)} data points")

        # 4. Group data by hour and find highest values for each axis
        hourly_data = {}
        for entry in background_data:
            timestamp = entry[0]  # Format: "2025-08-01T15:40:37.741-04:00"
            x_value = float(entry[1])
            y_value = float(entry[2])
            z_value = float(entry[3])
            
            # Extract hour key (YYYY-MM-DD-HH)
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                dt_est = dt.astimezone(est)
                hour_key = dt_est.strftime('%Y-%m-%d-%H')
            except Exception as e:
                print(f"Failed to parse timestamp {timestamp}: {e}")
                continue
            
            if hour_key not in hourly_data:
                hourly_data[hour_key] = {
                    'max_x': x_value,
                    'max_y': y_value,
                    'max_z': z_value,
                    'timestamp': timestamp
                }
            else:
                hourly_data[hour_key]['max_x'] = max(hourly_data[hour_key]['max_x'], abs(x_value))
                hourly_data[hour_key]['max_y'] = max(hourly_data[hour_key]['max_y'], abs(y_value))
                hourly_data[hour_key]['max_z'] = max(hourly_data[hour_key]['max_z'], abs(z_value))

        # 5. Check thresholds for each hour
        alerts_by_hour = {}
        for hour_key, hour_data in hourly_data.items():
            max_x = hour_data['max_x']
            max_y = hour_data['max_y']
            max_z = hour_data['max_z']
            timestamp = hour_data['timestamp']
            
            # Check if we've already sent for this hour
            already_sent = supabase.table('sent_alerts') \
                .select('id') \
                .eq('instrument_id', 'SMG1') \
                .eq('node_id', 15092) \
                .eq('timestamp', timestamp) \
                .execute()
            if already_sent.data:
                print(f"Alert already sent for hour {hour_key}, skipping.")
                continue

            messages = []
            
            # Check shutdown thresholds
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if shutdown_value and value >= shutdown_value:
                    messages.append(f"<b>Shutdown threshold reached on {axis}-axis:</b> {value:.6f}")
            
            # Check warning thresholds
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if warning_value and value >= warning_value:
                    messages.append(f"<b>Warning threshold reached on {axis}-axis:</b> {value:.6f}")
            
            # Check alert thresholds
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if alert_value and value >= alert_value:
                    messages.append(f"<b>Alert threshold reached on {axis}-axis:</b> {value:.6f}")

            if messages:
                alerts_by_hour[hour_key] = {
                    'messages': messages,
                    'timestamp': timestamp,
                    'max_values': {'X': max_x, 'Y': max_y, 'Z': max_z}
                }

        # 6. Send email if there are alerts
        if alerts_by_hour:
            # Create email body with professional styling similar to tiltmeter
            body = """
            <html>
            <head>
                <style>
                    body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }
                    .container { max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }
                    .header { background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 20px; text-align: center; }
                    .header h1 { margin: 0; font-size: 24px; font-weight: bold; }
                    .header p { margin: 5px 0 0 0; opacity: 0.9; }
                    .content { padding: 30px; }
                    .alert-section { margin-bottom: 25px; }
                    .alert-section h3 { color: #0056d2; border-bottom: 2px solid #0056d2; padding-bottom: 10px; margin-bottom: 15px; }
                    .alert-item { background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }
                    .alert-item.warning { border-left-color: #ffc107; }
                    .alert-item.alert { border-left-color: #fd7e14; }
                    .alert-item.shutdown { border-left-color: #dc3545; }
                    .timestamp { font-weight: bold; color: #495057; margin-bottom: 10px; }
                    .alert-message { color: #212529; line-height: 1.5; }
                    .max-values { background-color: #e9ecef; padding: 10px; border-radius: 4px; margin-top: 10px; }
                    .max-values table { width: 100%; border-collapse: collapse; }
                    .max-values th, .max-values td { padding: 8px; text-align: center; border: 1px solid #dee2e6; }
                    .max-values th { background-color: #f8f9fa; font-weight: bold; }
                    .footer { background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }
                    .footer p { margin: 0; }
                    .company-info { font-weight: bold; color: #0056d2; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>🌊 SEISMOGRAPH ALERT NOTIFICATION</h1>
                        <p>Dulles Geotechnical Monitoring System</p>
                    </div>
                    
                    <div class="content">
                        <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                            This is an automated alert notification from the DGMTS monitoring system. 
                            The following seismograph thresholds have been exceeded in the last hour:
                        </p>
            """
            
            # Add alerts for each hour
            for hour_key, alert_data in alerts_by_hour.items():
                # Format timestamp to EST
                try:
                    dt_utc = datetime.fromisoformat(alert_data['timestamp'].replace('Z', '+00:00'))
                    dt_est = dt_utc.astimezone(est)
                    formatted_time = dt_est.strftime('%Y-%m-%d %I:%M %p EST')
                except Exception as e:
                    print(f"Failed to parse/convert timestamp: {alert_data['timestamp']}, error: {e}")
                    formatted_time = alert_data['timestamp']
                
                body += f"""
                        <div class="alert-section">
                            <h3>📊 Hour: {hour_key.replace('-', ' ')} - Seismograph Alerts</h3>
                """
                
                for message in alert_data['messages']:
                    # Determine alert type for styling
                    alert_class = "alert-item"
                    if "Shutdown" in message:
                        alert_class += " shutdown"
                    elif "Warning" in message:
                        alert_class += " warning"
                    elif "Alert" in message:
                        alert_class += " alert"
                    
                    body += f"""
                            <div class="{alert_class}">
                                <div class="timestamp">{formatted_time}</div>
                                <div class="alert-message">{message}</div>
                                <div class="max-values">
                                    <table>
                                        <thead>
                                            <tr>
                                                <th>Axis</th>
                                                <th>Max Value (in/s)</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            <tr>
                                                <td>X (Longitudinal)</td>
                                                <td>{alert_data['max_values']['X']:.6f}</td>
                                            </tr>
                                            <tr>
                                                <td>Y (Vertical)</td>
                                                <td>{alert_data['max_values']['Y']:.6f}</td>
                                            </tr>
                                            <tr>
                                                <td>Z (Transverse)</td>
                                                <td>{alert_data['max_values']['Z']:.6f}</td>
                                            </tr>
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                    """
                
                body += """
                        </div>
                """
            
            body += """
                        <div style="background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-top: 20px;">
                            <p style="margin: 0; color: #0056d2; font-weight: bold;">⚠️ Action Required:</p>
                            <p style="margin: 5px 0 0 0; color: #495057;">
                                Please review the seismograph data and take appropriate action if necessary. 
                                Values shown are the maximum readings for each axis during the specified hour.
                            </p>
                        </div>
                    </div>
                    
                    <div class="footer">
                        <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                        <p style="font-size: 12px; margin-top: 5px;">
                            This is an automated message. Please do not reply to this email.
                        </p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            current_time = datetime.now(timezone.utc)
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"🌊 Seismograph Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print(f"Sent seismograph alert email for {len(alerts_by_hour)} hours with alerts")
                
                # Record that we've sent for each hour
                for hour_key, alert_data in alerts_by_hour.items():
                    supabase.table('sent_alerts').insert({
                        'instrument_id': 'SMG1',
                        'node_id': 15092,
                        'timestamp': alert_data['timestamp'],
                        'alert_type': 'any'
                    }).execute()
            else:
                print("No alert/warning/shutdown emails configured for SMG1")
        else:
            print("No thresholds crossed for any hour in the last hour.")
    except Exception as e:
        print(f"Error in check_and_send_seismograph_alert: {e}")

def check_and_send_smg3_seismograph_alert():
    print("Checking SMG-3 seismograph alerts using background API...")
    try:
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'SMG-3').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for SMG-3")
            return

        # For seismograph, use ONLY single values (not a tiltmeter)
        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')
        
        alert_emails = instrument.get('alert_emails') or []
        warning_emails = instrument.get('warning_emails') or []
        shutdown_emails = instrument.get('shutdown_emails') or []

        # 2. Calculate time range for the last hour in EST
        est = pytz.timezone('US/Eastern')
        now_est = datetime.now(est)
        one_hour_ago_est = now_est - timedelta(hours=1)
        
        # Format dates for API
        start_time = one_hour_ago_est.strftime('%Y-%m-%dT%H:%M:%S')
        end_time = now_est.strftime('%Y-%m-%dT%H:%M:%S')
        
        print(f"Fetching SMG-3 seismograph data from {start_time} to {end_time} EST")

        # 3. Fetch background data from Syscom API
        api_key = os.environ.get('SYSCOM_API_KEY')
        if not api_key:
            print("No SYSCOM_API_KEY set in environment")
            return

        url = f"https://scs.syscom-instruments.com/public-api/v1/records/background/13453/data?start={start_time}&end={end_time}"
        headers = {"x-scs-api-key": api_key}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Failed to fetch SMG-3 background data: {response.status_code} {response.text}")
            return

        data = response.json()
        background_data = data.get('data', [])
        
        if not background_data:
            print("No SMG-3 background data received for the last hour")
            return

        print(f"Received {len(background_data)} SMG-3 data points")

        # 4. Group data by hour and find highest values for each axis
        hourly_data = {}
        for entry in background_data:
            timestamp = entry[0]  # Format: "2025-08-01T15:40:37.741-04:00"
            x_value = float(entry[1])
            y_value = float(entry[2])
            z_value = float(entry[3])
            
            # Extract hour key (YYYY-MM-DD-HH)
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                dt_est = dt.astimezone(est)
                hour_key = dt_est.strftime('%Y-%m-%d-%H')
            except Exception as e:
                print(f"Failed to parse timestamp {timestamp}: {e}")
                continue
            
            if hour_key not in hourly_data:
                hourly_data[hour_key] = {
                    'max_x': x_value,
                    'max_y': y_value,
                    'max_z': z_value,
                    'timestamp': timestamp
                }
            else:
                hourly_data[hour_key]['max_x'] = max(hourly_data[hour_key]['max_x'], abs(x_value))
                hourly_data[hour_key]['max_y'] = max(hourly_data[hour_key]['max_y'], abs(y_value))
                hourly_data[hour_key]['max_z'] = max(hourly_data[hour_key]['max_z'], abs(z_value))

        # 5. Check thresholds for each hour
        alerts_by_hour = {}
        for hour_key, hour_data in hourly_data.items():
            max_x = hour_data['max_x']
            max_y = hour_data['max_y']
            max_z = hour_data['max_z']
            timestamp = hour_data['timestamp']
            
            # Check if we've already sent for this hour
            already_sent = supabase.table('sent_alerts') \
                .select('id') \
                .eq('instrument_id', 'SMG-3') \
                .eq('node_id', 13453) \
                .eq('timestamp', timestamp) \
                .execute()
            if already_sent.data:
                print(f"SMG-3 alert already sent for hour {hour_key}, skipping.")
                continue

            messages = []
            
            # Check shutdown thresholds
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if shutdown_value and value >= shutdown_value:
                    messages.append(f"<b>Shutdown threshold reached on {axis}-axis:</b> {value:.6f}")
            
            # Check warning thresholds
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if warning_value and value >= warning_value:
                    messages.append(f"<b>Warning threshold reached on {axis}-axis:</b> {value:.6f}")
            
            # Check alert thresholds
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if alert_value and value >= alert_value:
                    messages.append(f"<b>Alert threshold reached on {axis}-axis:</b> {value:.6f}")

            if messages:
                alerts_by_hour[hour_key] = {
                    'messages': messages,
                    'timestamp': timestamp,
                    'max_values': {'X': max_x, 'Y': max_y, 'Z': max_z}
                }

        # 6. Send email if there are alerts
        if alerts_by_hour:
            # Create email body with professional styling similar to tiltmeter
            body = """
            <html>
            <head>
                <style>
                    body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }
                    .container { max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }
                    .header { background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 20px; text-align: center; }
                    .header h1 { margin: 0; font-size: 24px; font-weight: bold; }
                    .header p { margin: 5px 0 0 0; opacity: 0.9; }
                    .content { padding: 30px; }
                    .alert-section { margin-bottom: 25px; }
                    .alert-section h3 { color: #0056d2; border-bottom: 2px solid #0056d2; padding-bottom: 10px; margin-bottom: 15px; }
                    .alert-item { background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }
                    .alert-item.warning { border-left-color: #ffc107; }
                    .alert-item.alert { border-left-color: #fd7e14; }
                    .alert-item.shutdown { border-left-color: #dc3545; }
                    .timestamp { font-weight: bold; color: #495057; margin-bottom: 10px; }
                    .alert-message { color: #212529; line-height: 1.5; }
                    .max-values { background-color: #e9ecef; padding: 10px; border-radius: 4px; margin-top: 10px; }
                    .max-values table { width: 100%; border-collapse: collapse; }
                    .max-values th, .max-values td { padding: 8px; text-align: center; border: 1px solid #dee2e6; }
                    .max-values th { background-color: #f8f9fa; font-weight: bold; }
                    .footer { background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }
                    .footer p { margin: 0; }
                    .company-info { font-weight: bold; color: #0056d2; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>🌊 ANC DAR-BC SEISMOGRAPH ALERT NOTIFICATION</h1>
                        <p>Dulles Geotechnical Monitoring System</p>
                    </div>
                    
                    <div class="content">
                        <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                            This is an automated alert notification from the DGMTS monitoring system. 
                            The following ANC DAR-BC Seismograph (SMG-3) thresholds have been exceeded in the last hour:
                        </p>
            """
            
            # Add alerts for each hour
            for hour_key, alert_data in alerts_by_hour.items():
                # Format timestamp to EST
                try:
                    dt_utc = datetime.fromisoformat(alert_data['timestamp'].replace('Z', '+00:00'))
                    dt_est = dt_utc.astimezone(est)
                    formatted_time = dt_est.strftime('%Y-%m-%d %I:%M %p EST')
                except Exception as e:
                    print(f"Failed to parse/convert timestamp: {alert_data['timestamp']}, error: {e}")
                    formatted_time = alert_data['timestamp']
                
                body += f"""
                        <div class="alert-section">
                            <h3>📊 Hour: {hour_key.replace('-', ' ')} - ANC DAR-BC Seismograph Alerts</h3>
                """
                
                for message in alert_data['messages']:
                    # Determine alert type for styling
                    alert_class = "alert-item"
                    if "Shutdown" in message:
                        alert_class += " shutdown"
                    elif "Warning" in message:
                        alert_class += " warning"
                    elif "Alert" in message:
                        alert_class += " alert"
                    
                    body += f"""
                            <div class="{alert_class}">
                                <div class="timestamp">{formatted_time}</div>
                                <div class="alert-message">{message}</div>
                                <div class="max-values">
                                    <table>
                                        <thead>
                                            <tr>
                                                <th>Axis</th>
                                                <th>Max Value (in/s)</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            <tr>
                                                <td>X (Longitudinal)</td>
                                                <td>{alert_data['max_values']['X']:.6f}</td>
                                            </tr>
                                            <tr>
                                                <td>Y (Vertical)</td>
                                                <td>{alert_data['max_values']['Y']:.6f}</td>
                                            </tr>
                                            <tr>
                                                <td>Z (Transverse)</td>
                                                <td>{alert_data['max_values']['Z']:.6f}</td>
                                            </tr>
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                    """
                
                body += """
                        </div>
                """
            
            body += """
                        <div style="background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-top: 20px;">
                            <p style="margin: 0; color: #0056d2; font-weight: bold;">⚠️ Action Required:</p>
                            <p style="margin: 5px 0 0 0; color: #495057;">
                                Please review the ANC DAR-BC Seismograph data and take appropriate action if necessary. 
                                Values shown are the maximum readings for each axis during the specified hour.
                            </p>
                        </div>
                    </div>
                    
                    <div class="footer">
                        <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                        <p style="font-size: 12px; margin-top: 5px;">
                            This is an automated message. Please do not reply to this email.
                        </p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            current_time = datetime.now(timezone.utc)
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"🌊 ANC DAR-BC Seismograph Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print(f"Sent SMG-3 seismograph alert email for {len(alerts_by_hour)} hours with alerts")
                
                # Record that we've sent for each hour
                for hour_key, alert_data in alerts_by_hour.items():
                    supabase.table('sent_alerts').insert({
                        'instrument_id': 'SMG-3',
                        'node_id': 13453,
                        'timestamp': alert_data['timestamp'],
                        'alert_type': 'any'
                    }).execute()
            else:
                print("No alert/warning/shutdown emails configured for SMG-3")
        else:
            print("No thresholds crossed for any hour in the last hour for SMG-3.")
    except Exception as e:
        print(f"Error in check_and_send_smg3_seismograph_alert: {e}")

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
        
        # Apply reference values if enabled
        instrument_id = NODE_TO_INSTRUMENT_ID.get(node_id)
        if instrument_id:
            reference_resp = supabase.table('reference_values').select('*').eq('instrument_id', instrument_id).execute()
            reference_values = reference_resp.data[0] if reference_resp.data else None
            
            if reference_values and reference_values.get('enabled', False):
                # Apply reference values to sensor data
                ref_x = reference_values.get('reference_x_value', 0) or 0
                ref_y = reference_values.get('reference_y_value', 0) or 0
                ref_z = reference_values.get('reference_z_value', 0) or 0
                
                calibrated_data = []
                for reading in response.data:
                    calibrated_reading = reading.copy()
                    if reading.get('x_value') is not None:
                        calibrated_reading['x_value'] = reading['x_value'] - ref_x
                    if reading.get('y_value') is not None:
                        calibrated_reading['y_value'] = reading['y_value'] - ref_y
                    if reading.get('z_value') is not None:
                        calibrated_reading['z_value'] = reading['z_value'] - ref_z
                    calibrated_data.append(calibrated_reading)
                
                return jsonify(calibrated_data)
        
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
schedule.every().hour.do(check_and_send_smg3_seismograph_alert)

# Start scheduler in background
scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
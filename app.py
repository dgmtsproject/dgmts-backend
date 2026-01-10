from flask import Flask, jsonify, request
from flask_cors import CORS
from config import Config
from utils.scheduler import start_scheduler

# Import route blueprints
from routes.auth_routes import auth_bp
from routes.sensor_routes import sensor_bp
from routes.email_routes import email_bp
from routes.micromate_routes import micromate_bp
from routes.payment_routes import payment_bp

# Create Flask app instance
app = Flask(__name__)

# Configure app
app.config.from_object(Config)

# Configure CORS
CORS(app, 
     supports_credentials=True,
     allow_headers=['Content-Type', 'Authorization'],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(sensor_bp)
app.register_blueprint(email_bp)
app.register_blueprint(micromate_bp)
app.register_blueprint(payment_bp)

# Root route
@app.route('/')
def index():
    return jsonify({
        "message": "DGMTS Backend API",
        "status": "running",
        "version": "2.0.0"
    })

# Endpoint for sending missed Rock Seismograph alerts
@app.route('/send-missed-rock-seismograph-alerts', methods=['POST'])
def send_missed_alerts():
    """
    Send emails for missed Rock Seismograph alerts by checking historical data.
    
    Accepts:
    - emails: List of email addresses or comma-separated string (optional, uses configured emails if not provided)
    - instrument_id: Instrument ID (default: 'ROCKSMG-1')
    - days_back: Number of days to search back (default: 3)
    
    Example POST body:
    {
        "emails": ["email1@example.com", "email2@example.com"],
        "instrument_id": "ROCKSMG-1",
        "days_back": 5
    }
    """
    try:
        from send_missed_rock_seismograph_alerts import send_missed_rock_seismograph_alerts
        
        data = request.get_json() or {}
        instrument_id = data.get('instrument_id', 'ROCKSMG-1')
        days_back = data.get('days_back', 3)  # Default to 3 days as requested
        emails = data.get('emails', [])  # Accept emails parameter
        
        # Handle emails - can be list, comma-separated string, or single string
        custom_emails = None
        if emails:
            if isinstance(emails, str):
                # Handle comma-separated string
                custom_emails = [email.strip() for email in emails.split(',') if email.strip()]
            elif isinstance(emails, list):
                # Handle list of emails
                custom_emails = [email.strip() if isinstance(email, str) else str(email) for email in emails if email]
            else:
                custom_emails = [str(emails)]
        
        print(f"🚀 Starting missed alerts check for {instrument_id} (last {days_back} days)")
        if custom_emails:
            print(f"📧 Sending to custom emails: {custom_emails}")
        
        # Import traceback for better error reporting
        import traceback
        
        try:
            result = send_missed_rock_seismograph_alerts(instrument_id, days_back, custom_emails=custom_emails)
            
            # Handle both old format (bool) and new format (tuple)
            if isinstance(result, tuple):
                success, error_message = result
            else:
                # Backward compatibility with old return format
                success = result
                error_message = None
            
            if success:
                return jsonify({
                    "message": f"✅ Missed alerts check completed for {instrument_id}",
                    "instrument_id": instrument_id,
                    "days_back": days_back,
                    "emails_used": custom_emails if custom_emails else "configured emails",
                    "status": "success"
                }), 200
            else:
                # If function returned False, there was an error but it was caught internally
                return jsonify({
                    "error": f"❌ Failed to process missed alerts for {instrument_id}",
                    "instrument_id": instrument_id,
                    "days_back": days_back,
                    "emails_used": custom_emails if custom_emails else "configured emails",
                    "status": "failed",
                    "error_message": error_message or "Unknown error - check server logs for details",
                    "hint": "Common issues: Missing instrument config, missing syscom_device_id, API key not set, or no data available"
                }), 500
        except Exception as func_error:
            # If the function itself raised an exception
            error_trace = traceback.format_exc()
            print(f"❌ Exception in send_missed_rock_seismograph_alerts: {error_trace}")
            return jsonify({
                "error": f"❌ Failed to process missed alerts for {instrument_id}",
                "instrument_id": instrument_id,
                "days_back": days_back,
                "emails_used": custom_emails if custom_emails else "configured emails",
                "status": "failed",
                "error_message": str(func_error),
                "error_type": type(func_error).__name__
            }), 500
            
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ Exception in endpoint: {error_trace}")
        return jsonify({
            "error": "Failed to process missed Rock Seismograph alerts",
            "message": str(e),
            "error_type": type(e).__name__,
            "traceback": error_trace
        }), 500

# Test endpoint for sending Rock Seismograph test email to specific address
@app.route('/test-rock-seismograph-email', methods=['POST'])
def test_rock_seismograph_email():
    """Send missed Rock Seismograph alerts to a specific test email address"""
    try:
        from send_missed_rock_seismograph_alerts import send_missed_rock_seismograph_alerts
        
        data = request.get_json() or {}
        instrument_id = data.get('instrument_id', 'ROCKSMG-1')
        test_email = data.get('test_email')
        test_emails = data.get('test_emails', [])  # Support multiple emails
        days_back = data.get('days_back', 1)  # Default to 1 day (today)
        
        # Use test_emails if provided, otherwise use single test_email
        if test_emails:
            emails_to_use = test_emails
        elif test_email:
            emails_to_use = [test_email]
        else:
            return jsonify({
                "error": "Either test_email or test_emails is required"
            }), 400
        
        print(f"🧪 Sending missed alerts for {instrument_id} to {emails_to_use} (last {days_back} days)")
        
        # Run the missed alerts function with custom emails
        result = send_missed_rock_seismograph_alerts(instrument_id, days_back, custom_emails=emails_to_use)
        
        # Handle both old format (bool) and new format (tuple)
        if isinstance(result, tuple):
            success, error_message = result
        else:
            success = result
            error_message = None
        
        if success:
            return jsonify({
                "message": f"✅ Missed alerts sent to {emails_to_use} for {instrument_id}",
                "instrument_id": instrument_id,
                "test_emails": emails_to_use,
                "days_back": days_back,
                "status": "success"
            }), 200
        else:
            return jsonify({
                "error": f"❌ Failed to send missed alerts to {emails_to_use}",
                "instrument_id": instrument_id,
                "test_emails": emails_to_use,
                "days_back": days_back,
                "status": "failed"
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to send missed Rock Seismograph alerts to test email",
            "message": str(e)
        }), 500

# Endpoint for sending missed SMG-1 Seismograph alerts
@app.route('/send-missed-smg1-alerts', methods=['POST'])
def send_missed_smg1_alerts_endpoint():
    """
    Send emails for missed SMG-1 Seismograph alerts by checking historical data.
    
    Accepts:
    - emails: List of email addresses or comma-separated string (optional, uses configured emails if not provided)
    - instrument_id: Instrument ID (default: 'SMG-1')
    - days_back: Number of days to search back (default: 3)
    
    Example POST body:
    {
        "emails": ["email1@example.com", "email2@example.com"],
        "instrument_id": "SMG-1",
        "days_back": 5
    }
    """
    try:
        from send_missed_smg1_alerts import send_missed_smg1_alerts as send_missed_alerts_func
        
        data = request.get_json() or {}
        instrument_id = data.get('instrument_id', 'SMG-1')
        # Convert days_back to int, default to 3 if not provided or invalid
        try:
            days_back = int(data.get('days_back', 3))
        except (ValueError, TypeError):
            days_back = 3
        emails = data.get('emails', [])  # Accept emails parameter
        
        # Handle emails - can be list, comma-separated string, or single string
        custom_emails = None
        if emails:
            if isinstance(emails, str):
                # Handle comma-separated string
                custom_emails = [email.strip() for email in emails.split(',') if email.strip()]
            elif isinstance(emails, list):
                # Handle list of emails
                custom_emails = [email.strip() if isinstance(email, str) else str(email) for email in emails if email]
            else:
                custom_emails = [str(emails)]
        
        print(f"🚀 Starting missed alerts check for {instrument_id} (last {days_back} days)")
        if custom_emails:
            print(f"📧 Sending to custom emails: {custom_emails}")
        
        # Import traceback for better error reporting
        import traceback
        
        try:
            result = send_missed_alerts_func(instrument_id, days_back, custom_emails=custom_emails)
            
            # Handle both old format (bool) and new format (tuple)
            if isinstance(result, tuple):
                success, error_message = result
            else:
                # Backward compatibility with old return format
                success = result
                error_message = None
            
            if success:
                return jsonify({
                    "message": f"✅ Missed alerts check completed for {instrument_id}",
                    "instrument_id": instrument_id,
                    "days_back": days_back,
                    "emails_used": custom_emails if custom_emails else "configured emails",
                    "status": "success"
                }), 200
            else:
                # If function returned False, there was an error but it was caught internally
                return jsonify({
                    "error": f"❌ Failed to process missed alerts for {instrument_id}",
                    "instrument_id": instrument_id,
                    "days_back": days_back,
                    "emails_used": custom_emails if custom_emails else "configured emails",
                    "status": "failed",
                    "error_message": error_message or "Unknown error - check server logs for details",
                    "hint": "Common issues: Missing instrument config, missing device_id, API key not set, or no data available"
                }), 500
        except Exception as func_error:
            # If the function itself raised an exception
            error_trace = traceback.format_exc()
            print(f"❌ Exception in send_missed_smg1_alerts: {error_trace}")
            return jsonify({
                "error": f"❌ Failed to process missed alerts for {instrument_id}",
                "instrument_id": instrument_id,
                "days_back": days_back,
                "emails_used": custom_emails if custom_emails else "configured emails",
                "status": "failed",
                "error_message": str(func_error),
                "error_type": type(func_error).__name__
            }), 500
            
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ Exception in endpoint: {error_trace}")
        return jsonify({
            "error": "Failed to process missed SMG-1 Seismograph alerts",
            "message": str(e),
            "error_type": type(e).__name__,
            "traceback": error_trace
        }), 500

# Start scheduler when the module is imported
start_scheduler()

def main():
    """Main function for development server"""
    app.run(host='0.0.0.0', port=5000, debug=True)

if __name__ == "__main__":
    main()

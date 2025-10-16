from flask import Flask, jsonify, request
from flask_cors import CORS
from config import Config
from utils.scheduler import start_scheduler

# Import route blueprints
from routes.auth_routes import auth_bp
from routes.sensor_routes import sensor_bp
from routes.email_routes import email_bp
from routes.micromate_routes import micromate_bp

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

# Root route
@app.route('/')
def index():
    return jsonify({
        "message": "DGMTS Backend API",
        "status": "running",
        "version": "2.0.0"
    })

# Test endpoint for sending missed Rock Seismograph alerts
@app.route('/send-missed-rock-seismograph-alerts', methods=['POST'])
def send_missed_alerts():
    """Send emails for missed Rock Seismograph alerts by checking historical data"""
    try:
        from send_missed_rock_seismograph_alerts import send_missed_rock_seismograph_alerts
        
        data = request.get_json() or {}
        instrument_id = data.get('instrument_id', 'ROCKSMG-1')
        days_back = data.get('days_back', 30)
        
        print(f"🚀 Starting missed alerts check for {instrument_id} (last {days_back} days)")
        
        success = send_missed_rock_seismograph_alerts(instrument_id, days_back)
        
        if success:
            return jsonify({
                "message": f"✅ Missed alerts check completed for {instrument_id}",
                "instrument_id": instrument_id,
                "days_back": days_back,
                "status": "success"
            }), 200
        else:
            return jsonify({
                "error": f"❌ Failed to process missed alerts for {instrument_id}",
                "instrument_id": instrument_id,
                "days_back": days_back,
                "status": "failed"
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to process missed Rock Seismograph alerts",
            "message": str(e)
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
        days_back = data.get('days_back', 30)
        
        if not test_email:
            return jsonify({
                "error": "test_email is required"
            }), 400
        
        print(f"🧪 Sending missed alerts for {instrument_id} to {test_email} (last {days_back} days)")
        
        # Run the missed alerts function with custom email
        success = send_missed_rock_seismograph_alerts(instrument_id, days_back, custom_emails=[test_email])
        
        if success:
            return jsonify({
                "message": f"✅ Missed alerts sent to {test_email} for {instrument_id}",
                "instrument_id": instrument_id,
                "test_email": test_email,
                "days_back": days_back,
                "status": "success"
            }), 200
        else:
            return jsonify({
                "error": f"❌ Failed to send missed alerts to {test_email}",
                "instrument_id": instrument_id,
                "test_email": test_email,
                "days_back": days_back,
                "status": "failed"
            }), 500
            
    except Exception as e:
        return jsonify({
            "error": "Failed to send missed Rock Seismograph alerts to test email",
            "message": str(e)
        }), 500

# Start scheduler when the module is imported
start_scheduler()

def main():
    """Main function for development server"""
    app.run(host='0.0.0.0', port=5000, debug=True)

if __name__ == "__main__":
    main()

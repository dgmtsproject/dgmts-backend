"""
Test Flask app - runs without schedulers for testing endpoints
Usage: python test_app.py
"""
from flask import Flask, jsonify
from flask_cors import CORS
from config import Config

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
        "message": "DGMTS Backend API - TEST MODE (No Schedulers)",
        "status": "running",
        "version": "2.0.0",
        "mode": "test"
    })

# NOTE: Scheduler is NOT started in test mode
# This allows testing endpoints without interfering with production

def main():
    """Main function for test server - runs without schedulers"""
    print("=" * 60)
    print("TEST MODE - Running without schedulers")
    print("=" * 60)
    print("This instance will NOT run:")
    print("  - Scheduled sensor data fetching")
    print("  - Scheduled alert checks")
    print("  - Any background tasks")
    print("=" * 60)
    print("Use this for testing endpoints only")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=True)

if __name__ == "__main__":
    main()


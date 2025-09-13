from flask import Flask, jsonify
from flask_cors import CORS
from config import Config
from utils.scheduler import start_scheduler

# Import route blueprints
from routes.auth_routes import auth_bp
from routes.sensor_routes import sensor_bp
from routes.email_routes import email_bp

# Create Flask app instance
app = Flask(__name__)

# Configure app
app.config.from_object(Config)

# Configure CORS
CORS(app, 
     supports_credentials=True,
     origins=['https://dgmts-imsite.dullesgeotechnical.com', 'https://imsite.dullesgeotechnical.com'],
     allow_headers=['Content-Type', 'Authorization'],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(sensor_bp)
app.register_blueprint(email_bp)

# Root route
@app.route('/')
def index():
    return jsonify({
        "message": "DGMTS Backend API",
        "status": "running",
        "version": "2.0.0"
    })

# Start scheduler when the module is imported
start_scheduler()

def main():
    """Main function for development server"""
    app.run(host='0.0.0.0', port=5000, debug=True)

if __name__ == "__main__":
    main()

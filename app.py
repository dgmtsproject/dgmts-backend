from flask import Flask, jsonify
from flask_cors import CORS
from config import Config
from utils.scheduler import start_scheduler

# Import route blueprints
from routes.auth_routes import auth_bp
from routes.sensor_routes import sensor_bp
from routes.email_routes import email_bp

def create_app():
    """Create and configure Flask application"""
    app = Flask(__name__)
    
    # Configure app
    app.config.from_object(Config)
    
    # Configure CORS
    CORS(app, supports_credentials=True)
    
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
    
    return app

def main():
    app = create_app()
    start_scheduler()
    app.run(host='0.0.0.0', port=5000, debug=True)

if __name__ == "__main__":
    main()

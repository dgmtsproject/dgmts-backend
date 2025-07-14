from flask import Flask, jsonify, request, session, make_response
from flask_cors import CORS
from supabase import create_client, Client
import os
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
import uuid
import jwt
from datetime import datetime, timedelta
from functools import wraps
from flask import request

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

# Helper to create JWT
def create_jwt(user):
    payload = {
        'user_id': user['id'],
        'email': user['email'],
        'role': user.get('role', 'user'),
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
        response = supabase.table('users').select('*').eq('email', email).eq('password', password).execute()
        user = response.data[0] if response.data else None

        if not user:
            return jsonify({"error": "Invalid credentials"}), 401

        token = create_jwt(user)
        return jsonify({
            "message": "Login successful",
            "token": token,
            "user": {
                "id": user['id'],
                "email": user['email'],
                "role": user.get('role', 'user')
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

@app.route('/api/check-auth', methods=['GET'])
@jwt_required
def check_auth():
    user = request.user
    return jsonify({
        "authenticated": True,
        "user": {
            "email": user['email'],
            "role": user['role']
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

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
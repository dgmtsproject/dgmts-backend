from flask import Flask, jsonify, request, session, make_response
from flask_cors import CORS
from supabase import create_client, Client
import os
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash
import uuid

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

        # Clear existing session and create new one
        session.clear()
        session.permanent = True
        session['user_id'] = user['id']
        session['email'] = user['email']
        session['role'] = user.get('role', 'user')
        session['session_id'] = str(uuid.uuid4())

        response = jsonify({
            "message": "Login successful",
            "user": {
                "id": user['id'],
                "email": user['email'],
                "role": user.get('role', 'user')
            }
        })

        # Set cookie in response
        response.set_cookie(
            'flask_session',
            value=session['session_id'],
            max_age=3600,
            httponly=True,
            secure=True,         # Must be True for SameSite=None (requires HTTPS)
            samesite='None',     # Must be 'None' for cross-site
            path='/'
        )

        return response

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    resp = make_response(jsonify({"message": "Logged out successfully"}))
    resp.delete_cookie('flask_session')
    return resp

@app.route('/api/check-auth', methods=['GET'])
def check_auth():
    if 'user_id' not in session:
        return jsonify({"authenticated": False}), 401
    
    return jsonify({
        "authenticated": True,
        "user": {
            "email": session['email'],
            "role": session['role']
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
from flask import Blueprint, request, jsonify, session, make_response
from supabase import create_client, Client
from config import Config
from auth.jwt_handler import create_jwt, jwt_required
from auth.password_handler import verify_password
from services.email_service import send_email
import secrets
from datetime import datetime, timedelta

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

# Create Blueprint
auth_bp = Blueprint('auth', __name__, url_prefix='/api')

@auth_bp.route('/login', methods=['POST'])
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
        if not verify_password(user, password):
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

@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    resp = make_response(jsonify({"message": "Logged out successfully"}))
    resp.delete_cookie('flask_session')
    return resp

@auth_bp.route('/check-auth', methods=['GET'])
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

@auth_bp.route('/protected-route', methods=['GET'])
def protected_route():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    return jsonify({
        "message": "Protected data",
        "user_email": session['email']
    })

@auth_bp.route('/forgot-password', methods=['POST'])
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
        Config.RESET_TOKENS[reset_token] = {
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

@auth_bp.route('/reset-password', methods=['POST'])
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
    if token not in Config.RESET_TOKENS:
        return jsonify({"error": "Invalid or expired token"}), 400
    
    token_data = Config.RESET_TOKENS[token]
    
    # Check if token is expired
    if datetime.utcnow() > token_data['expires']:
        del Config.RESET_TOKENS[token]
        return jsonify({"error": "Token has expired"}), 400
    
    try:
        # Update password in database (storing as plain text to match current system)
        response = supabase.table('users').update({
            'password': new_password
        }).eq('email', token_data['email']).execute()
        
        # Remove used token
        del Config.RESET_TOKENS[token]
        
        return jsonify({"message": "Password reset successfully"})
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@auth_bp.route('/migrate-passwords', methods=['POST'])
def migrate_passwords():
    from auth.password_handler import migrate_passwords
    try:
        if migrate_passwords():
            return jsonify({"message": "Password migration complete"})
        else:
            return jsonify({"error": "Password migration failed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

import jwt
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify
from config import Config

def create_jwt(user):
    """Create JWT token for user"""
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
        'exp': datetime.utcnow() + timedelta(seconds=Config.JWT_EXP_DELTA_SECONDS)
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)

def decode_jwt(token):
    """Decode and validate JWT token"""
    try:
        return jwt.decode(token, Config.JWT_SECRET, algorithms=[Config.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def jwt_required(f):
    """Decorator to require JWT authentication"""
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

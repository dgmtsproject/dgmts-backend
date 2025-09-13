from werkzeug.security import generate_password_hash, check_password_hash
from supabase import create_client, Client
from config import Config

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

def verify_password(user, password):
    """Verify user password"""
    stored_password = user['password']
    if stored_password.startswith('pbkdf2:'):
        # Password is hashed, use check_password_hash
        return check_password_hash(stored_password, password)
    else:
        # Password is plain text (legacy), compare directly
        return stored_password == password

def migrate_passwords():
    """Migrate plain text passwords to hashed passwords"""
    try:
        users = supabase.table('users').select('*').execute()
        for user in users.data:
            if not user['password'].startswith('pbkdf2:'):
                supabase.table('users').update({
                    'password': generate_password_hash(user['password'])
                }).eq('id', user['id']).execute()
        return True
    except Exception as e:
        print(f"Error migrating passwords: {e}")
        return False

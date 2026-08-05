import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask Configuration
    SECRET_KEY = os.environ['FLASK_SECRET_KEY']
    
    # Session Configuration
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'None'
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hour
    
    # Supabase Configuration (Main)
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_KEY')
    
    # DGMTS Static Supabase — still used by /api/dgmts-static/send-mail (unchanged).
    DGMTS_STATIC_SUPABASE_URL = os.getenv('DGMTS_STATIC_SUPABASE_URL')
    DGMTS_STATIC_SUPABASE_KEY = os.getenv('DGMTS_STATIC_SUPABASE_KEY') or os.getenv(
        'DGMTS_STATIC_SUPABASE_PASSWORD'
    )  # some .env files used the wrong name; value is the anon/service key

    # Migration only: local Postgres for /api/dgmts-static/data, /media, /functions/notify-subscribers
    STATIC_DB_HOST = os.getenv('STATIC_DB_HOST', '127.0.0.1')
    STATIC_DB_PORT = int(os.getenv('STATIC_DB_PORT', '5432'))
    STATIC_DB_NAME = os.getenv('STATIC_DB_NAME', 'dgmts_static_db')
    STATIC_DB_USER = os.getenv('STATIC_DB_USER', 'dgmts_static_user')
    STATIC_DB_PASSWORD = os.getenv('STATIC_DB_PASSWORD')

    # Public base URL of this Flask app (used for public media URLs, no trailing slash)
    STATIC_APP_PUBLIC_BASE = os.getenv('STATIC_APP_PUBLIC_BASE', 'https://imsite.dullesgeotechnical.com')

    # Imsite instrumentation DB — local copy of Supabase xmhiocoinswgxvqokuzd (dgmts_db).
    # Used by /api/imsite/* CRUD. App services may still use SUPABASE_* until full cutover.
    IMSITE_DB_HOST = os.getenv('IMSITE_DB_HOST', '127.0.0.1')
    IMSITE_DB_PORT = int(os.getenv('IMSITE_DB_PORT', '5432'))
    IMSITE_DB_NAME = os.getenv('IMSITE_DB_NAME', 'dgmts_db')
    IMSITE_DB_USER = os.getenv('IMSITE_DB_USER', 'dgmts_user')
    IMSITE_DB_PASSWORD = os.getenv('IMSITE_DB_PASSWORD')


    # On-disk store for public uploaded images (replaces Supabase Storage buckets)
    STATIC_MEDIA_DIR = os.getenv('STATIC_MEDIA_DIR', 'static_media')

    BLOG_BASE_URL = os.getenv('BLOG_BASE_URL', 'https://dullesgeotechnical.com/blog')
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'info@dullesgeotechnical.com')
    
    # JWT Configuration
    JWT_SECRET = os.environ['FLASK_SECRET_KEY']
    JWT_ALGORITHM = 'HS256'
    JWT_EXP_DELTA_SECONDS = 3600
    
    # Email Configuration - Using Gmail SMTP
    SMTP_SERVER = 'smtp.gmail.com'
    SMTP_PORT = 465
    EMAIL_USERNAME = os.environ['EMAIL_USERNAME']
    EMAIL_PASSWORD = os.environ['EMAIL_PASSWORD']
    
    # Microsoft 365 configuration (commented out for now)
    # SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.office365.com')
    # SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    # EMAIL_USERNAME = os.getenv('EMAIL_USERNAME', 'instrumentation@dullesgeotechnical.com')
    # EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', 'DGMTS@14155')
    
    # Sensor API Configuration
    SENSOR_API_BASE = "https://loadsensing.wocs3.com/30846/dataserver/api/v1/data/nodes"
    SENSOR_USERNAME = "admin"
    SENSOR_PASSWORD = "oNg9ahy3m"
    SENSOR_NODES = [142939, 143969]
    
    # Mapping from node_id to instrument_id for tiltmeters
    NODE_TO_INSTRUMENT_ID = {142939: "TILT-142939", 143969: "TILT-143969"}
    
    # Rock Seismograph Configuration (keeping for backward compatibility)
    ROCK_SEISMOGRAPH_INSTRUMENTS = {
        'ROCKSMG-1': {
            'name': 'Rock Seismograph',
            'project_id': 25304,
            'project_name': 'Yellow Line ANC'
        },
        'ROCKSMG-2': {
            'name': 'Rock Seismograph',
            'project_id': 25304,
            'project_name': 'Yellow Line ANC'
        }
    }
    
    # Syscom API Configuration
    SYSCOM_API_KEY = os.getenv('SYSCOM_API_KEY')
    
    # Authorize.net Configuration
    AUTHORIZE_NET_API_LOGIN_ID = os.getenv('AUTHORIZE_NET_API_LOGIN_ID')
    AUTHORIZE_NET_TRANSACTION_KEY = os.getenv('AUTHORIZE_NET_TRANSACTION_KEY')
    AUTHORIZE_NET_SANDBOX = os.getenv('AUTHORIZE_NET_SANDBOX', 'false').lower() == 'true'  # Default to production
    
    # ------------------------------------------------------------------
    # Inventory & Purchase Management module (fully isolated, additive).
    # Nothing above this block references these fields. Safe to remove the
    # whole block + the inventory blueprint registration to disable the module.
    # ------------------------------------------------------------------

    # Toggle the entire Inventory module without a code change.
    INVENTORY_MODULE_ENABLED = os.getenv('INVENTORY_MODULE_ENABLED', 'true').lower() == 'true'

    # Inventory DB: lives in the SAME Postgres instance as the static DB, but in
    # a dedicated `inventory` schema reached through its own role + pool.
    INVENTORY_DB_HOST = os.getenv('INVENTORY_DB_HOST', os.getenv('STATIC_DB_HOST', '127.0.0.1'))
    INVENTORY_DB_PORT = int(os.getenv('INVENTORY_DB_PORT', os.getenv('STATIC_DB_PORT', '5432')))
    INVENTORY_DB_NAME = os.getenv('INVENTORY_DB_NAME', os.getenv('STATIC_DB_NAME', 'dgmts_static_db'))
    INVENTORY_DB_USER = os.getenv('INVENTORY_DB_USER', 'dgmts_inventory_user')
    INVENTORY_DB_PASSWORD = os.getenv('INVENTORY_DB_PASSWORD')
    INVENTORY_DB_SCHEMA = os.getenv('INVENTORY_DB_SCHEMA', 'inventory')

    # Microsoft SSO directory API — used to validate the frontend's bearer token
    # (GET {base}/user/me) and to sync the employee/department directory cache.
    INVENTORY_MS_API_BASE_URL = os.getenv(
        'INVENTORY_MS_API_BASE_URL', 'https://ms.dullesgeotechnical.com/api/v2'
    )
    # Optional fallback bearer for server-side directory sync when no user token.
    INVENTORY_MS_FALLBACK_BEARER = os.getenv('INVENTORY_MS_FALLBACK_BEARER')

    # Emails allowed full inventory-admin access (Roles tab + admin-only APIs).
    # Comma-separated; mirrors the frontend allowlist.
    INVENTORY_ADMIN_EMAILS = [
        e.strip().lower()
        for e in os.getenv(
            'INVENTORY_ADMIN_EMAILS',
            'admin@gmail.com,iaziz@dullesgeotechnical.com,qhaider@dullesgeotechnical.com',
        ).split(',')
        if e.strip()
    ]

    # Signed token secret for PO email-action links (approve/reject via email).
    INVENTORY_PO_EMAIL_ACTION_SECRET = os.getenv('INVENTORY_PO_EMAIL_ACTION_SECRET')

    # Shared secret for trusted server-to-server calls from the Next.js backend
    # (email-action routes have no user MS token). Sent as X-Inventory-Internal-Secret.
    INVENTORY_INTERNAL_SECRET = (
        os.getenv('INVENTORY_INTERNAL_SECRET')
        or os.getenv('INVENTORY_PO_EMAIL_ACTION_SECRET')
    )

    # Public base URL of the Inventory frontend (for links inside emails).
    INVENTORY_APP_PUBLIC_BASE = os.getenv(
        'INVENTORY_APP_PUBLIC_BASE', 'https://dgmts-imsite.dullesgeotechnical.com'
    )

    # Inventory SMTP (custom mail system) — isolated from the module above.
    INVENTORY_SMTP_HOST = os.getenv('INVENTORY_SMTP_HOST')
    INVENTORY_SMTP_PORT = int(os.getenv('INVENTORY_SMTP_PORT', '465'))
    INVENTORY_SMTP_USE_SSL = os.getenv('INVENTORY_SMTP_USE_SSL', 'true').lower() == 'true'
    INVENTORY_SMTP_USERNAME = os.getenv('INVENTORY_SMTP_USERNAME')
    INVENTORY_SMTP_PASSWORD = os.getenv('INVENTORY_SMTP_PASSWORD')
    INVENTORY_SMTP_FROM = os.getenv('INVENTORY_SMTP_FROM') or os.getenv('INVENTORY_SMTP_USERNAME')

    # Reset tokens storage (in production, use Redis or database)
    RESET_TOKENS = {}
    
    # FTP Server Files Configuration
    FTP_SERVER_FILES_PATH = os.getenv('FTP_SERVER_FILES_PATH', 'ftp-server-files')

    # Access-control software (iCCard3000.mdb) exported JSON files.
    # The RDP-side script uploads t_d_SwipeRecord.json and t_a_Attendence.json here via SFTP.
    ACCESS_SOFTWARE_FILES_PATH = os.getenv('ACCESS_SOFTWARE_FILES_PATH', 'access-software-files')
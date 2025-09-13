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
    
    # Supabase Configuration
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_KEY')
    
    # JWT Configuration
    JWT_SECRET = os.environ['FLASK_SECRET_KEY']
    JWT_ALGORITHM = 'HS256'
    JWT_EXP_DELTA_SECONDS = 3600
    
    # Email Configuration - Using Gmail SMTP
    SMTP_SERVER = 'smtp.gmail.com'
    SMTP_PORT = 465
    EMAIL_USERNAME = 'dgmts.project@gmail.com'
    EMAIL_PASSWORD = 'qaegeeqwsuuwtmwb'
    
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
    
    # Rock Seismograph Configuration
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
    
    # Reset tokens storage (in production, use Redis or database)
    RESET_TOKENS = {}

# DGMTS Backend - Restructured

This Flask application has been restructured for better organization and maintainability.

## Project Structure

```
dgmts-backend/
├── app.py                 # Main Flask application entry point
├── app_old.py            # Original monolithic app.py (backup)
├── config.py             # Configuration settings
├── requirements.txt      # Python dependencies
├── README.md            # This file
├── auth/                # Authentication modules
│   ├── __init__.py
│   ├── jwt_handler.py   # JWT token creation and validation
│   └── password_handler.py # Password verification and migration
├── services/            # Business logic services
│   ├── __init__.py
│   ├── alert_service.py # Alert checking and email sending
│   ├── email_service.py # Email sending functionality
│   └── sensor_service.py # Sensor data fetching and processing
├── routes/              # API route handlers
│   ├── __init__.py
│   ├── auth_routes.py   # Authentication endpoints
│   ├── email_routes.py  # Email testing endpoints
│   └── sensor_routes.py # Sensor data endpoints
├── utils/               # Utility functions
│   ├── __init__.py
│   └── scheduler.py     # Background task scheduling
└── models/              # Database models
    ├── __init__.py
    └── database.py      # Database connection
```

## Key Improvements

1. **Separation of Concerns**: Code is now organized by functionality rather than being in one large file
2. **Modular Design**: Each module has a specific responsibility
3. **Better Maintainability**: Easier to find and modify specific functionality
4. **Cleaner Imports**: Dependencies are clearly defined
5. **Scalability**: Easy to add new features without cluttering the main file

## Modules Overview

### Authentication (`auth/`)
- JWT token handling
- Password verification and migration
- Authentication decorators

### Services (`services/`)
- Email service for sending notifications
- Sensor data service for API integration
- Alert service for threshold monitoring

### Routes (`routes/`)
- API endpoint definitions
- Request/response handling
- Route-specific business logic

### Utils (`utils/`)
- Background task scheduling
- Helper functions

### Models (`models/`)
- Database connection management
- Data model definitions

## Running the Application

```bash
python app.py
```

The application will start on `http://localhost:5000` with all the same functionality as before, but now with a much cleaner and more maintainable codebase.

## API Endpoints

All existing API endpoints remain the same:
- `/api/login` - User authentication
- `/api/logout` - User logout
- `/api/check-auth` - Authentication verification
- `/api/forgot-password` - Password reset
- `/api/reset-password` - Password reset completion
- `/api/sensor-data/<node_id>` - Get sensor data
- `/api/fetch-sensor-data` - Manual sensor data fetch
- `/api/test-email` - Test email functionality
- `/api/test-tiltmeter-alert` - Test tiltmeter alerts
- `/api/test-seismograph-alert` - Test seismograph alerts

## Configuration

All configuration is centralized in `config.py` and can be easily modified without touching the main application code.

"""
Connection Monitor Service for DGMS Backend

This service monitors connection errors in sent_alert_logs and sends email notifications
when instrument connections are lost.
"""

import os
from datetime import datetime, timedelta, timezone
import pytz
from supabase import create_client, Client
from config import Config
from .email_service import send_email

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

def log_alert_event(log_type, log_text, instrument_id, log_reference_alert=None):
    """Log alert events to sent_alert_logs table"""
    try:
        log_data = {
            'log_type': log_type,
            'log': log_text,
            'for_instrument': instrument_id,
            'log_time': datetime.now(timezone.utc).isoformat(),
            'log_reference_alert': log_reference_alert
        }
        supabase.table('sent_alert_logs').insert(log_data).execute()
        print(f"Logged: {log_type} - {log_text}")
    except Exception as e:
        print(f"Failed to log alert event: {e}")

def check_and_send_connection_lost_alerts():
    """Check for connection lost errors in sent_alert_logs and send email notifications"""
    print("Checking for connection lost errors...")
    
    # Define connection error patterns
    connection_error_patterns = [
        "Failed to fetch background data",
        "Failed to fetch SMG-3 background data", 
        "No device_id available",
        "connection",
        "timeout",
        "unreachable",
        "network",
        "api",
        "request failed",
        "status_code"
    ]
    
    # Email addresses for connection lost notifications
    connection_lost_emails = [
        "dgmts.project@gmail.com",
        "qhaider@dullesgeotechnical.com", 
         "iaziz@dullesgeotechnical.com"
    ]
    
    try:
        # Get recent error logs from the last 24 hours
        one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        
        # Query for ERROR type logs in the last 24 hours
        response = supabase.table('sent_alert_logs') \
            .select('*') \
            .eq('log_type', 'ERROR') \
            .gte('log_time', one_day_ago) \
            .order('log_time', desc=True) \
            .execute()
        
        if not response.data:
            print("No error logs found in the last 24 hours")
            return
        
        # Filter for connection-related errors
        connection_errors = []
        for log_entry in response.data:
            log_text = log_entry.get('log', '').lower()
            instrument_id = log_entry.get('for_instrument', 'Unknown')
            log_time = log_entry.get('log_time', '')
            
            # Check if this log matches any connection error pattern
            is_connection_error = any(pattern.lower() in log_text for pattern in connection_error_patterns)
            
            if is_connection_error:
                connection_errors.append({
                    'instrument_id': instrument_id,
                    'log_text': log_entry.get('log', ''),
                    'log_time': log_time,
                    'log_type': log_entry.get('log_type', 'ERROR')
                })
        
        if not connection_errors:
            print("No connection lost errors found in the last 24 hours")
            return
        
        # Check if we've already sent a connection lost email recently (within last 6 hours)
        six_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        recent_notification = supabase.table('sent_alert_logs') \
            .select('id') \
            .eq('log_type', 'CONNECTION_LOST_NOTIFICATION') \
            .gte('log_time', six_hours_ago) \
            .execute()
        
        if recent_notification.data:
            print(f"Connection lost notification already sent recently (within last 6 hours)")
            return
        
        # Send connection lost email
        print(f"Found {len(connection_errors)} connection errors, sending notification email...")
        
        # Create email body
        body = _create_connection_lost_email_body(connection_errors)
        
        # Create subject
        current_time = datetime.now(timezone.utc)
        est = pytz.timezone('US/Eastern')
        current_time_est = current_time.astimezone(est)
        formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
        subject = f"DGMTS Internal Company Connection Retrieval Error - {formatted_time}"
        
        # Send email
        send_email(",".join(connection_lost_emails), subject, body)
        
        # Log that we sent the notification
        log_alert_event("CONNECTION_LOST_NOTIFICATION", 
                       f"Sent connection lost notification for {len(connection_errors)} errors", 
                       "SYSTEM")
        
        print(f"Connection lost notification sent to {len(connection_lost_emails)} recipients")
        
    except Exception as e:
        print(f"Error in check_and_send_connection_lost_alerts: {e}")
        log_alert_event("ERROR", f"Error checking connection lost alerts: {e}", "SYSTEM")

def _create_connection_lost_email_body(connection_errors):
    """Create HTML email body for connection lost notifications"""
    body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
            .container {{ max-width: 700px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }}
            .header {{ background: linear-gradient(135deg, #dc3545 0%, #c82333 100%); color: white; padding: 20px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 24px; font-weight: bold; }}
            .header p {{ margin: 5px 0 0 0; opacity: 0.9; }}
            .content {{ padding: 30px; }}
            .error-section {{ margin-bottom: 25px; }}
            .error-section h3 {{ color: #dc3545; border-bottom: 2px solid #dc3545; padding-bottom: 10px; margin-bottom: 15px; }}
            .error-item {{ background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }}
            .timestamp {{ font-weight: bold; color: #495057; margin-bottom: 10px; }}
            .error-message {{ color: #212529; line-height: 1.5; }}
            .instrument-id {{ color: #dc3545; font-weight: bold; }}
            .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }}
            .footer p {{ margin: 0; }}
            .company-info {{ font-weight: bold; color: #dc3545; }}
            .summary {{ background-color: #fff3cd; border: 1px solid #ffeaa7; border-radius: 4px; padding: 15px; margin-bottom: 20px; }}
            .summary p {{ margin: 0; color: #856404; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                 <h1>DGMTS Internal Company Connection Retrieval Error</h1>
                <p>Dulles Geotechnical Monitoring System - Critical Alert</p>
            </div>
            
            <div class="content">
                <div class="summary">
                    <p>⚠️ {len(connection_errors)} connection error(s) detected in the last 24 hours</p>
                </div>
                
                <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                    This is an automated notification from the DGMTS monitoring system. 
                    The following instrument connection errors have been detected:
                </p>
    """
    
    # Add error details
    for i, error in enumerate(connection_errors, 1):
        # Format timestamp to EST
        try:
            dt_utc = datetime.fromisoformat(error['log_time'].replace('Z', '+00:00'))
            est = pytz.timezone('US/Eastern')
            dt_est = dt_utc.astimezone(est)
            formatted_time = dt_est.strftime('%Y-%m-%d %I:%M:%S %p EST')
        except Exception as e:
            print(f"Failed to parse timestamp: {error['log_time']}, error: {e}")
            formatted_time = error['log_time']
        
        body += f"""
                <div class="error-item">
                    <div class="timestamp">Error #{i} - {formatted_time}</div>
                    <div class="error-message">
                        <strong>Instrument:</strong> <span class="instrument-id">{error['instrument_id']}</span><br>
                        <strong>Error:</strong> {error['log_text']}
                    </div>
                </div>
        """
    
    body += f"""
             </div>
            
            <div class="footer">
                <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                <p style="font-size: 12px; margin-top: 5px;">
                    This is an automated message. Please do not reply to this email.
                </p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return body

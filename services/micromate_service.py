import os
import requests
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

def get_project_info(instrument_id):
    """Get project information and instrument details for an instrument from the database"""
    try:
        # Get the instrument with all details including project_id
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
        if not instrument_resp.data:
            print(f"No instrument found for {instrument_id}")
            return None
            
        instrument = instrument_resp.data[0]
        project_id = instrument.get('project_id')
        
        # Initialize with instrument details
        instrument_info = {
            'project_id': project_id,
            'project_name': 'Unknown Project',
            'project_description': '',
            'instrument_id': instrument_id,
            'instrument_name': instrument.get('instrument_name', 'Unknown Instrument'),
            'serial_number': instrument.get('sno', 'N/A'),
            'instrument_location': instrument.get('instrument_location', 'N/A')
        }
        
        # Try to get project information if project_id exists
        if project_id:
            try:
                project_resp = supabase.table('Projects').select('*').eq('id', project_id).execute()
                if project_resp.data:
                    project = project_resp.data[0]
                    instrument_info['project_name'] = project.get('name', 'Unknown Project')
                    instrument_info['project_description'] = project.get('description', '')
                else:
                    print(f"No project found with id {project_id}")
            except Exception as project_error:
                print(f"Projects table not accessible for {instrument_id}: {project_error}")
                # Use fallback project names based on instrument type
                if 'ROCKSMG' in instrument_id:
                    instrument_info['project_name'] = 'Yellow Line ANC'
                elif 'SMG' in instrument_id:
                    instrument_info['project_name'] = 'Dulles Airport Monitoring'
                elif 'TILT' in instrument_id:
                    instrument_info['project_name'] = 'Dulles Airport Monitoring'
                elif 'INSTANTEL' in instrument_id:
                    instrument_info['project_name'] = 'Dulles Airport Monitoring'
        else:
            print(f"No project_id found for instrument {instrument_id}")
            
        return instrument_info
    except Exception as e:
        print(f"Error fetching project info for {instrument_id}: {e}")
        return None

def check_and_send_micromate_alert():
    """Check Instantel Micromate alerts and send emails if thresholds are exceeded"""
    print("Checking Instantel Micromate alerts...")
    try:
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'Instantel 1').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for Instantel 1")
            return

        # For micromate, use single values for each axis
        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')
        
        alert_emails = instrument.get('alert_emails') or []
        warning_emails = instrument.get('warning_emails') or []
        shutdown_emails = instrument.get('shutdown_emails') or []

        # 2. Calculate time range for the last minute using UTC and convert to EST properly
        # Account for instrument clock being 1 hour behind EST
        utc_now = datetime.now(timezone.utc)
        est_tz = pytz.timezone('US/Eastern')
        now_est = utc_now.astimezone(est_tz)
        # Subtract 1 hour to account for instrument clock being behind
        now_instrument_time = now_est - timedelta(hours=1)
        one_minute_ago_instrument_time = now_instrument_time - timedelta(minutes=1)
        
        print(f"Fetching Micromate data from {one_minute_ago_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')} to {now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')} EST")
        print(f"UTC time: {utc_now.strftime('%Y-%m-%dT%H:%M:%S')} UTC")
        print(f"EST time: {now_est.strftime('%Y-%m-%dT%H:%M:%S')} EST")
        print(f"Instrument time (1hr behind): {now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')} EST")

        # 3. Fetch data from Micromate API
        url = "https://imsite.dullesgeotechnical.com/api/micromate/readings"
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Failed to fetch Micromate data: {response.status_code} {response.text}")
            log_alert_event("ERROR", f"Failed to fetch Micromate data: {response.status_code} {response.text}", 'Instantel 1')
            return

        data = response.json()
        micromate_readings = data.get('MicromateReadings', [])
        
        if not micromate_readings:
            print("No Micromate data received")
            log_alert_event("ERROR", "No Micromate data received", 'Instantel 1')
            return

        print(f"Received {len(micromate_readings)} Micromate data points")

        # 4. Check thresholds for the most recent reading (Micromate reads every 5 minutes)
        alerts_by_timestamp = {}
        
        # Find the most recent reading
        most_recent_reading = None
        most_recent_time = None
        
        for reading in micromate_readings:
            try:
                timestamp_str = reading['Time']
                dt_utc = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                dt_est = dt_utc.astimezone(est_tz)
                
                if most_recent_time is None or dt_est > most_recent_time:
                    most_recent_time = dt_est
                    most_recent_reading = reading
                    
            except Exception as e:
                print(f"Failed to parse timestamp: {e}")
                continue
        
        if not most_recent_reading:
            print("No valid Micromate readings found")
            return
            
        print(f"Most recent Micromate reading: {most_recent_reading['Time']} ({most_recent_time.strftime('%Y-%m-%d %H:%M:%S')} EST)")
        
        # Check thresholds for the most recent reading
        timestamp_str = most_recent_reading['Time']
        longitudinal = abs(float(most_recent_reading['Longitudinal']))
        transverse = abs(float(most_recent_reading['Transverse']))
        vertical = abs(float(most_recent_reading['Vertical']))
        
        # Check if we've already sent for this timestamp
        already_sent = supabase.table('sent_alerts') \
            .select('id') \
            .eq('instrument_id', 'Instantel 1') \
            .eq('node_id', 24252) \
            .eq('timestamp', timestamp_str) \
            .execute()
        if already_sent.data:
            print(f"Micromate alert already sent for timestamp {timestamp_str}, skipping.")
            return

        messages = []
        
        # Check shutdown thresholds
        for axis, value, axis_desc in [('Longitudinal', longitudinal, 'Longitudinal'), ('Transverse', transverse, 'Transverse'), ('Vertical', vertical, 'Vertical')]:
            if shutdown_value and value >= shutdown_value:
                messages.append(f"<b>Shutdown threshold reached on {axis_desc} axis:</b> {value:.6f}")
        
        # Check warning thresholds
        for axis, value, axis_desc in [('Longitudinal', longitudinal, 'Longitudinal'), ('Transverse', transverse, 'Transverse'), ('Vertical', vertical, 'Vertical')]:
            if warning_value and value >= warning_value:
                messages.append(f"<b>Warning threshold reached on {axis_desc} axis:</b> {value:.6f}")
        
        # Check alert thresholds
        for axis, value, axis_desc in [('Longitudinal', longitudinal, 'Longitudinal'), ('Transverse', transverse, 'Transverse'), ('Vertical', vertical, 'Vertical')]:
            if alert_value and value >= alert_value:
                messages.append(f"<b>Alert threshold reached on {axis_desc} axis:</b> {value:.6f}")

        if messages:
            alerts_by_timestamp[timestamp_str] = {
                'messages': messages,
                'timestamp': timestamp_str,
                'values': {
                    'Longitudinal': longitudinal, 
                    'Transverse': transverse, 
                    'Vertical': vertical
                }
            }

        # 6. Send email if there are alerts
        if alerts_by_timestamp:
            # Get project information and instrument details for micromate from database
            project_name = "Lincoln Lewis Fairfax"  # Default fallback
            instrument_details = []
            
            try:
                instrument_info = get_project_info('Instantel 1')
                if instrument_info:
                    instrument_details.append(instrument_info)
                    project_name = instrument_info['project_name']
            except Exception as e:
                print(f"Error getting project info for Instantel 1: {e}")
                log_alert_event("ERROR", f"Error getting project info for Instantel 1: {e}", 'Instantel 1')
                
            body = _create_micromate_email_body(alerts_by_timestamp, project_name, instrument_details)
            
            current_time = datetime.now(timezone.utc)
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"📊 Micromate Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                email_sent = send_email(",".join(all_emails), subject, body)
                if email_sent:
                    print(f"Sent Micromate alert email for {len(alerts_by_timestamp)} timestamps with alerts")
                    
                    # Record that we've sent for each timestamp
                    for timestamp, alert_data in alerts_by_timestamp.items():
                        supabase.table('sent_alerts').insert({
                            'instrument_id': 'Instantel 1',
                            'node_id': 24252,
                            'timestamp': alert_data['timestamp'],
                            'alert_type': 'any'
                        }).execute()
                else:
                    log_alert_event("SEND EMAIL_FAILED", f"Failed to send alert email for Instantel 1", 'Instantel 1')
            else:
                print("No alert/warning/shutdown emails configured for Instantel 1")
        else:
            print("No thresholds crossed for any reading in the last minute for Micromate.")
    except Exception as e:
        print(f"Error in check_and_send_micromate_alert: {e}")
        log_alert_event("ERROR", f"Error in check_and_send_micromate_alert: {e}", 'Instantel 1')

def _create_micromate_email_body(alerts_by_timestamp, project_name, instrument_details):
    """Create HTML email body for Micromate alerts"""
    body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
            .container {{ max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }}
            .header {{ background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 20px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 24px; font-weight: bold; }}
            .header p {{ margin: 5px 0 0 0; opacity: 0.9; }}
            .content {{ padding: 30px; }}
            .alert-section {{ margin-bottom: 25px; }}
            .alert-section h3 {{ color: #0056d2; border-bottom: 2px solid #0056d2; padding-bottom: 10px; margin-bottom: 15px; }}
            .alert-item {{ background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }}
            .alert-item.warning {{ border-left-color: #ffc107; }}
            .alert-item.alert {{ border-left-color: #fd7e14; }}
            .alert-item.shutdown {{ border-left-color: #dc3545; }}
            .timestamp {{ font-weight: bold; color: #495057; margin-bottom: 10px; }}
            .alert-message {{ color: #212529; line-height: 1.5; }}
            .max-values {{ background-color: #e9ecef; padding: 10px; border-radius: 4px; margin-top: 10px; }}
            .max-values table {{ width: 100%; border-collapse: collapse; }}
            .max-values th, .max-values td {{ padding: 8px; text-align: center; border: 1px solid #dee2e6; }}
            .max-values th {{ background-color: #f8f9fa; font-weight: bold; }}
            .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }}
            .footer p {{ margin: 0; }}
            .company-info {{ font-weight: bold; color: #0056d2; }}
            .project-info {{ background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-bottom: 20px; }}
            .project-info p {{ margin: 0; color: #0056d2; font-weight: bold; }}
            .instrument-info {{ background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; padding: 15px; margin-bottom: 20px; }}
            .instrument-info h4 {{ margin: 0 0 10px 0; color: #0056d2; }}
            .instrument-info table {{ width: 100%; border-collapse: collapse; }}
            .instrument-info th, .instrument-info td {{ padding: 8px; text-align: left; border: 1px solid #dee2e6; }}
            .instrument-info th {{ background-color: #e9ecef; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📊 INSTANTEL MICROMATE ALERT NOTIFICATION</h1>
                <p>Dulles Geotechnical Monitoring System - {project_name}</p>
            </div>
            
            <div class="content">
                <div class="project-info">
                    <p>📋 Project: {project_name}</p>
                </div>
                
                <div class="instrument-info">
                    <h4>📊 Instrument Details</h4>
                    <table>
                        <thead>
                            <tr>
                                <th>Instrument ID</th>
                                <th>Instrument Name</th>
                                <th>Serial Number</th>
                                <th>Location</th>
                            </tr>
                        </thead>
                        <tbody>
    """
    
    # Add instrument details
    for instrument in instrument_details:
        body += f"""
                            <tr>
                                <td>{instrument['instrument_id']}</td>
                                <td>{instrument['instrument_name']}</td>
                                <td>{instrument['serial_number']}</td>
                                <td>{instrument['instrument_location']}</td>
                            </tr>
        """
    
    body += f"""
                        </tbody>
                    </table>
                </div>
                
                <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                    This is an automated alert notification from the DGMTS monitoring system. 
                    The following Instantel Micromate thresholds have been exceeded in real-time:
                </p>
    """
    
    # Add alerts for each timestamp
    for timestamp, alert_data in alerts_by_timestamp.items():
        # Format timestamp to EST
        try:
            dt_utc = datetime.fromisoformat(alert_data['timestamp'].replace('Z', '+00:00'))
            est = pytz.timezone('US/Eastern')
            dt_est = dt_utc.astimezone(est)
            formatted_time = dt_est.strftime('%Y-%m-%d %I:%M:%S %p EST')
        except Exception as e:
            print(f"Failed to parse/convert timestamp: {alert_data['timestamp']}, error: {e}")
            formatted_time = alert_data['timestamp']
        
        body += f"""
                <div class="alert-section">
                    <h3>📊 Real-time Alert - Instantel Micromate</h3>
        """
        
        for message in alert_data['messages']:
            # Determine alert type for styling
            alert_class = "alert-item"
            if "Shutdown" in message:
                alert_class += " shutdown"
            elif "Warning" in message:
                alert_class += " warning"
            elif "Alert" in message:
                alert_class += " alert"
            
            body += f"""
                    <div class="{alert_class}">
                        <div class="timestamp">{formatted_time}</div>
                        <div class="alert-message">{message}</div>
                        <div class="max-values">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Axis</th>
                                        <th>Peak Value</th>
                                    </tr>
                                </thead>
                                <tbody>
                                           <tr>
                                               <td>Longitudinal</td>
                                               <td>{alert_data['values']['Longitudinal']:.6f}</td>
                                           </tr>
                                           <tr>
                                               <td>Transverse</td>
                                               <td>{alert_data['values']['Transverse']:.6f}</td>
                                           </tr>
                                           <tr>
                                               <td>Vertical</td>
                                               <td>{alert_data['values']['Vertical']:.6f}</td>
                                           </tr>
                                </tbody>
                            </table>
                            <p style="margin: 10px 0 0 0; font-size: 12px; color: #6c757d;">
                                Real-time reading that exceeded thresholds
                            </p>
                        </div>
                    </div>
            """
        
        body += """
                </div>
        """
    
    body += """
                <div style="background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-top: 20px;">
                    <p style="margin: 0; color: #0056d2; font-weight: bold;">⚠️ Action Required:</p>
                    <p style="margin: 5px 0 0 0; color: #495057;">
                        Please review the Instantel Micromate data and take appropriate action if necessary. 
                        Values shown are the actual readings that exceeded thresholds.
                        <br><br>
                        <strong>Project ID:</strong> 24252<br>
                        <strong>Instrument:</strong> Instantel 1
                    </p>
                </div>
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

import os
import requests
from datetime import datetime, timedelta, timezone
import pytz
from supabase import create_client, Client
from config import Config
from .email_service import send_email

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

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
                    instrument_info['project_name'] = 'ANC DAR-BC'
                elif 'TILT' in instrument_id:
                    instrument_info['project_name'] = 'ANC DAR-BC'
                elif 'INSTANTEL' in instrument_id:
                    instrument_info['project_name'] = 'Lincoln Lewis Fairfax'
        else:
            print(f"No project_id found for instrument {instrument_id}")
            
        return instrument_info
    except Exception as e:
        print(f"Error fetching project info for {instrument_id}: {e}")
        return None

def check_and_send_rock_seismograph_alert(instrument_id):
    """Check Rock Seismograph alerts and send emails if thresholds are exceeded"""
    print(f"Checking {instrument_id} Rock Seismograph alerts using background API...")
    try:
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print(f"No instrument found for {instrument_id}")
            return

        # For seismograph, use ONLY single values (not a tiltmeter)
        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')
        
        alert_emails = instrument.get('alert_emails') or []
        warning_emails = instrument.get('warning_emails') or []
        shutdown_emails = instrument.get('shutdown_emails') or []

        # 2. Calculate time range for the last hour in EST
        est = pytz.timezone('US/Eastern')
        now_est = datetime.now(est)
        one_hour_ago_est = now_est - timedelta(hours=1)
        
        # Format dates for API
        start_time = one_hour_ago_est.strftime('%Y-%m-%dT%H:%M:%S')
        end_time = now_est.strftime('%Y-%m-%dT%H:%M:%S')
        
        print(f"Fetching {instrument_id} Rock Seismograph data from {start_time} to {end_time} EST")

        # 3. Fetch background data from Syscom API
        api_key = os.environ.get('SYSCOM_API_KEY')
        if not api_key:
            print("No SYSCOM_API_KEY set in environment")
            return

        # Get project ID from database
        project_id = None
        try:
            project_info = get_project_info(instrument_id)
            if project_info:
                project_id = project_info['project_id']
            else:
                # Fallback to config if database lookup fails
                project_id = Config.ROCK_SEISMOGRAPH_INSTRUMENTS[instrument_id]['project_id']
        except Exception as e:
            print(f"Error getting project info for {instrument_id}: {e}")
            # Fallback to config if database lookup fails
            project_id = Config.ROCK_SEISMOGRAPH_INSTRUMENTS[instrument_id]['project_id']
            
        url = f"https://scs.syscom-instruments.com/public-api/v1/records/background/{project_id}/data?start={start_time}&end={end_time}"
        headers = {"x-scs-api-key": api_key}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Failed to fetch {instrument_id} background data: {response.status_code} {response.text}")
            if response.status_code == 404:
                print(f"Device ID {project_id} not found in Syscom API. This device may not exist or be accessible.")
            return

        data = response.json()
        background_data = data.get('data', [])
        
        if not background_data:
            print(f"No background data received for {instrument_id} in the last hour")
            return

        print(f"Received {len(background_data)} data points for {instrument_id}")

        # 4. Group data by hour and find highest values for each axis
        hourly_data = {}
        for entry in background_data:
            timestamp = entry[0]  # Format: "2025-08-01T15:40:37.741-04:00"
            x_value = float(entry[1])
            y_value = float(entry[2])
            z_value = float(entry[3])
            
            # Extract hour key (YYYY-MM-DD-HH)
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                dt_est = dt.astimezone(est)
                hour_key = dt_est.strftime('%Y-%m-%d-%H')
            except Exception as e:
                print(f"Failed to parse timestamp {timestamp}: {e}")
                continue
            
            if hour_key not in hourly_data:
                hourly_data[hour_key] = {
                    'max_x': x_value,
                    'max_y': y_value,
                    'max_z': z_value,
                    'timestamp': timestamp
                }
            else:
                hourly_data[hour_key]['max_x'] = max(hourly_data[hour_key]['max_x'], abs(x_value))
                hourly_data[hour_key]['max_y'] = max(hourly_data[hour_key]['max_y'], abs(y_value))
                hourly_data[hour_key]['max_z'] = max(hourly_data[hour_key]['max_z'], abs(z_value))

        # 5. Check thresholds for each hour
        alerts_by_hour = {}
        for hour_key, hour_data in hourly_data.items():
            max_x = hour_data['max_x']
            max_y = hour_data['max_y']
            max_z = hour_data['max_z']
            timestamp = hour_data['timestamp']
            
            # Check if we've already sent for this hour
            already_sent = supabase.table('sent_alerts') \
                .select('id') \
                .eq('instrument_id', instrument_id) \
                .eq('node_id', project_id) \
                .eq('timestamp', timestamp) \
                .execute()
            if already_sent.data:
                print(f"{instrument_id} alert already sent for hour {hour_key}, skipping.")
                continue

            messages = []
            
            # Check shutdown thresholds
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if shutdown_value and value >= shutdown_value:
                    messages.append(f"<b>Shutdown threshold reached on {axis}-axis:</b> {value:.6f}")
            
            # Check warning thresholds
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if warning_value and value >= warning_value:
                    messages.append(f"<b>Warning threshold reached on {axis}-axis:</b> {value:.6f}")
            
            # Check alert thresholds
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if alert_value and value >= alert_value:
                    messages.append(f"<b>Alert threshold reached on {axis}-axis:</b> {value:.6f}")

            if messages:
                alerts_by_hour[hour_key] = {
                    'messages': messages,
                    'timestamp': timestamp,
                    'max_values': {'X': max_x, 'Y': max_y, 'Z': max_z}
                }

        # 6. Send email if there are alerts
        if alerts_by_hour:
            seismograph_name = Config.ROCK_SEISMOGRAPH_INSTRUMENTS[instrument_id]['name']
            
            # Get project information and instrument details from database
            project_name = "Yellow Line ANC"  # Default fallback
            instrument_details = []
            
            try:
                instrument_info = get_project_info(instrument_id)
                if instrument_info:
                    instrument_details.append(instrument_info)
                    project_name = instrument_info['project_name']
            except Exception as e:
                print(f"Error getting project info for {instrument_id}: {e}")
                
            body = _create_rock_seismograph_email_body(alerts_by_hour, seismograph_name, project_name, instrument_id, instrument_details)
            
            current_time = datetime.now(timezone.utc)
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"🌊 {seismograph_name} Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print(f"Sent {instrument_id} Rock Seismograph alert email for {len(alerts_by_hour)} hours with alerts")
                
                # Record that we've sent for each hour
                for hour_key, alert_data in alerts_by_hour.items():
                    supabase.table('sent_alerts').insert({
                        'instrument_id': instrument_id,
                        'node_id': project_id,
                        'timestamp': alert_data['timestamp'],
                        'alert_type': 'any'
                    }).execute()
            else:
                print(f"No alert/warning/shutdown emails configured for {instrument_id}")
        else:
            print(f"No thresholds crossed for any hour in the last hour for {instrument_id}.")
    except Exception as e:
        print(f"Error in check_and_send_rock_seismograph_alert for {instrument_id}: {e}")

def _create_rock_seismograph_email_body(alerts_by_hour, seismograph_name, project_name, instrument_id, instrument_details):
    """Create HTML email body for Rock Seismograph alerts"""
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
                <h1>🌊 {seismograph_name.upper()} ALERT NOTIFICATION</h1>
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
                    The following {seismograph_name} ({instrument_id}) thresholds have been exceeded in the last hour:
                </p>
    """
    
    # Add alerts for each hour
    for hour_key, alert_data in alerts_by_hour.items():
        # Format timestamp to EST
        try:
            dt_utc = datetime.fromisoformat(alert_data['timestamp'].replace('Z', '+00:00'))
            est = pytz.timezone('US/Eastern')
            dt_est = dt_utc.astimezone(est)
            formatted_time = dt_est.strftime('%Y-%m-%d %I:%M %p EST')
        except Exception as e:
            print(f"Failed to parse/convert timestamp: {alert_data['timestamp']}, error: {e}")
            formatted_time = alert_data['timestamp']
        
        body += f"""
                <div class="alert-section">
                    <h3>📊 Hour: {hour_key.replace('-', ' ')} - {seismograph_name} Alerts ({instrument_id})</h3>
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
                                        <th>Max Value (in/s)</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    <tr>
                                        <td>X (Longitudinal)</td>
                                        <td>{alert_data['max_values']['X']:.6f}</td>
                                    </tr>
                                    <tr>
                                        <td>Y (Vertical)</td>
                                        <td>{alert_data['max_values']['Y']:.6f}</td>
                                    </tr>
                                    <tr>
                                        <td>Z (Transverse)</td>
                                        <td>{alert_data['max_values']['Z']:.6f}</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
            """
        
        body += """
                </div>
        """
    
    body += f"""
                <div style="background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-top: 20px;">
                    <p style="margin: 0; color: #0056d2; font-weight: bold;">⚠️ Action Required:</p>
                    <p style="margin: 5px 0 0 0; color: #495057;">
                        Please review the {seismograph_name} data and take appropriate action if necessary. 
                        Values shown are the maximum readings for each axis during the specified hour.
                        <br><br>
                        <strong>Project:</strong> {project_name}<br>
                        <strong>Instrument:</strong> {instrument_id}
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

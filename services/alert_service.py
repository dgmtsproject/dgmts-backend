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
            log_alert_event("ERROR", f"No instrument found for {instrument_id}", instrument_id)
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
        log_alert_event("ERROR", f"Error fetching project info for {instrument_id}: {e}", instrument_id)
        return None

def check_and_send_tiltmeter_alerts():
    """Check tiltmeter alerts and send emails if thresholds are exceeded"""
    print("Checking tiltmeter alerts for both nodes...")
    try:
        node_ids = [142939, 143969]
        node_alerts = {}

        for node_id in node_ids:
            instrument_id = Config.NODE_TO_INSTRUMENT_ID.get(node_id)
            if not instrument_id:
                print(f"No instrument_id mapping for node {node_id}")
                continue
            
            # 1. First check reference_values table for this instrument
            reference_resp = supabase.table('reference_values').select('*').eq('instrument_id', instrument_id).execute()
            reference_values = reference_resp.data[0] if reference_resp.data else None
            
            # 2. Get instrument settings for this node's instrument_id
            instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
            instrument = instrument_resp.data[0] if instrument_resp.data else None
            if not instrument:
                print(f"No instrument found for {instrument_id}")
                continue

            # 3. Determine which threshold values to use
            if reference_values and reference_values.get('enabled', False):
                # Use reference values when enabled
                print(f"Using reference values for {instrument_id}")
                # For tiltmeters, use ONLY XYZ values
                if instrument_id in ['TILT-142939', 'TILT-143969']:
                    xyz_alert_values = instrument.get('x_y_z_alert_values')
                    xyz_warning_values = instrument.get('x_y_z_warning_values')
                    xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
                else:
                    # For non-tiltmeters, use ONLY single values
                    xyz_alert_values = None
                    xyz_warning_values = None
                    xyz_shutdown_values = None
                    alert_value = instrument.get('alert_value')
                    warning_value = instrument.get('warning_value')
                    shutdown_value = instrument.get('shutdown_value')
            else:
                # Use original instrument values when reference values are not enabled
                print(f"Using original instrument values for {instrument_id}")
                # For tiltmeters, use ONLY XYZ values
                if instrument_id in ['TILT-142939', 'TILT-143969']:
                    xyz_alert_values = instrument.get('x_y_z_alert_values')
                    xyz_warning_values = instrument.get('x_y_z_warning_values')
                    xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
                else:
                    # For non-tiltmeters, use ONLY single values
                    xyz_alert_values = None
                    xyz_warning_values = None
                    xyz_shutdown_values = None
                    alert_value = instrument.get('alert_value')
                    warning_value = instrument.get('warning_value')
                    shutdown_value = instrument.get('shutdown_value')
            
            alert_emails = instrument.get('alert_emails') or []
            warning_emails = instrument.get('warning_emails') or []
            shutdown_emails = instrument.get('shutdown_emails') or []

            # Check last hour for threshold checking (tiltmeters read every hour)
            one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            readings_resp = supabase.table('sensor_readings') \
                .select('*') \
                .eq('node_id', node_id) \
                .gte('timestamp', one_hour_ago) \
                .order('timestamp', desc=False) \
                .execute()
            readings = readings_resp.data if readings_resp.data else []

            node_messages = []
            for reading in readings:
                timestamp = reading['timestamp']
                x = reading.get('x_value')
                y = reading.get('y_value')
                z = reading.get('z_value')

                # Check if we've already sent for this timestamp
                already_sent = supabase.table('sent_alerts') \
                    .select('id') \
                    .eq('instrument_id', instrument_id) \
                    .eq('node_id', node_id) \
                    .eq('timestamp', timestamp) \
                    .execute()
                if already_sent.data:
                    print(f"DEBUG: Alert already sent for node {node_id} at {timestamp}, skipping.")
                    continue

                # Format timestamp to EST
                try:
                    dt_utc = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    est = pytz.timezone('US/Eastern')
                    dt_est = dt_utc.astimezone(est)
                    formatted_time = dt_est.strftime('%Y-%m-%d %I:%M %p EST')
                except Exception as e:
                    print(f"Failed to parse/convert timestamp: {timestamp}, error: {e}")
                    formatted_time = timestamp

                messages = []
                
                # Calculate calibrated values when reference values are enabled
                if reference_values and reference_values.get('enabled', False):
                    ref_x = reference_values.get('reference_x_value') or 0
                    ref_y = reference_values.get('reference_y_value') or 0
                    ref_z = reference_values.get('reference_z_value') or 0
                    
                    # Calculate calibrated values (raw - reference) to match frontend logic
                    calibrated_x = x - ref_x if x is not None else None
                    calibrated_y = y - ref_y if y is not None else None
                    calibrated_z = z - ref_z if z is not None else None
                    
                    print(f"Reference values enabled for {instrument_id}: X={ref_x}, Y={ref_y}, Z={ref_z}")
                    print(f"Raw values: X={x}, Y={y}, Z={z}")
                    print(f"Calibrated values: X={calibrated_x}, Y={calibrated_y}, Z={calibrated_z}")
                    
                    # Use original (unadjusted) thresholds for comparison
                    base_xyz_alert_values = instrument.get('x_y_z_alert_values')
                    base_xyz_warning_values = instrument.get('x_y_z_warning_values')
                    base_xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
                    
                    # Check shutdown thresholds using calibrated values (X and Z only, no Y)
                    for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                        if calibrated_value is None:
                            continue
                        axis_shutdown_value = base_xyz_shutdown_values.get(axis_key) if base_xyz_shutdown_values else None
                        if axis_shutdown_value and abs(calibrated_value) >= axis_shutdown_value:
                            messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
                    
                    # Check warning thresholds using calibrated values (X and Z only, no Y)
                    for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                        if calibrated_value is None:
                            continue
                        axis_warning_value = base_xyz_warning_values.get(axis_key) if base_xyz_warning_values else None
                        if axis_warning_value and abs(calibrated_value) >= axis_warning_value:
                            messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
                    
                    # Check alert thresholds using calibrated values (X and Z only, no Y)
                    for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                        if calibrated_value is None:
                            continue
                        axis_alert_value = base_xyz_alert_values.get(axis_key) if base_xyz_alert_values else None
                        if axis_alert_value and abs(calibrated_value) >= axis_alert_value:
                            messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
                else:
                    # Use original logic when reference values are not enabled (X and Z only, no Y)
                    # Check shutdown thresholds
                    for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                        if value is None:
                            continue
                        # For tiltmeters, use ONLY XYZ-specific values
                        axis_shutdown_value = xyz_shutdown_values.get(axis_key) if xyz_shutdown_values else None
                        if axis_shutdown_value and abs(value) >= axis_shutdown_value:
                            messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {value:.6f} at {formatted_time}</b>")
                    
                    # Check warning thresholds
                    for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                        if value is None:
                            continue
                        # For tiltmeters, use ONLY XYZ-specific values
                        axis_warning_value = xyz_warning_values.get(axis_key) if xyz_warning_values else None
                        if axis_warning_value and abs(value) >= axis_warning_value:
                            messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {value:.6f} at {formatted_time}</b>")
                    
                    # Check alert thresholds
                    for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                        if value is None:
                            continue
                        # For tiltmeters, use ONLY XYZ-specific values
                        axis_alert_value = xyz_alert_values.get(axis_key) if xyz_alert_values else None
                        if axis_alert_value and abs(value) >= axis_alert_value:
                            messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {value:.6f} at {formatted_time}</b>")

                if messages:
                    print(f"DEBUG: Node {node_id} has {len(messages)} threshold violations at {formatted_time}")
                    node_messages.append(f"<u><b>Timestamp: {formatted_time}</b></u><br>" + "<br>".join(messages))
                    # Record that we've sent for this timestamp (use correct instrument_id)
                    supabase.table('sent_alerts').insert({
                        'instrument_id': instrument_id,
                        'node_id': node_id,
                        'timestamp': timestamp,
                        'alert_type': 'any',
                        'created_at': datetime.now(timezone.utc).isoformat()
                    }).execute()
                    print(f"DEBUG: Recorded alert sent for node {node_id} at {timestamp}")

            if node_messages:
                node_alerts[node_id] = node_messages

        if node_alerts:
            # Get project information and instrument details for tiltmeters from database
            project_name = "ANC DAR-BC"  # Default fallback
            instrument_details = []
            
            try:
                # Get instrument details for all tiltmeter instruments
                for node_id in node_ids:
                    instrument_id = Config.NODE_TO_INSTRUMENT_ID.get(node_id)
                    if instrument_id:
                        instrument_info = get_project_info(instrument_id)
                        if instrument_info:
                            instrument_details.append(instrument_info)
                            if not project_name or project_name == "ANC DAR-BC":
                                project_name = instrument_info['project_name']
            except Exception as e:
                print(f"Error getting project info for tiltmeters: {e}")
            
            # Create email body with professional styling
            body = _create_tiltmeter_email_body(node_alerts, node_ids, project_name, instrument_details)
            
            current_time = datetime.now(timezone.utc)
            est = pytz.timezone('US/Eastern')
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"🚨 Tiltmeter Alert Notification - {formatted_time}"
            
            # Collect all emails from all instruments
            all_emails = set()
            for node_id in node_ids:
                instrument_id = Config.NODE_TO_INSTRUMENT_ID.get(node_id)
                if not instrument_id:
                    continue
                instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
                instrument = instrument_resp.data[0] if instrument_resp.data else None
                if not instrument:
                    log_alert_event("ERROR", f"No instrument found for {instrument_id}", instrument_id)
                    continue
                all_emails.update(instrument.get('alert_emails') or [])
                all_emails.update(instrument.get('warning_emails') or [])
                all_emails.update(instrument.get('shutdown_emails') or [])
            
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print("Sent alert email for both nodes")
            else:
                print("No alert/warning/shutdown emails configured for tiltmeters")
        else:
            print("No alerts to send for either node in the last hour.")
    except Exception as e:
        print(f"Error in check_and_send_tiltmeter_alerts: {e}")

def _create_tiltmeter_email_body(node_alerts, node_ids, project_name, instrument_details):
    """Create HTML email body for tiltmeter alerts"""
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
                <h1>🚨 TILTMETER ALERT NOTIFICATION</h1>
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
    
    body += """
                        </tbody>
                    </table>
                </div>
                
                <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                    This is an automated alert notification from the DGMTS monitoring system. 
                    The following tiltmeter thresholds have been exceeded in real-time:
                </p>
    """
    
    # Add alerts for each node
    for node_id in node_ids:
        if node_id in node_alerts:
            body += f"""
                <div class="alert-section">
                    <h3>📊 Node {node_id} - Tiltmeter Alerts</h3>
            """
            
            for alert in node_alerts[node_id]:
                # Determine alert type for styling
                alert_class = "alert-item"
                if "Shutdown" in alert:
                    alert_class += " shutdown"
                elif "Warning" in alert:
                    alert_class += " warning"
                elif "Alert" in alert:
                    alert_class += " alert"
                
                # Extract timestamp and message
                alert_parts = alert.split('<br>')
                timestamp = alert_parts[0].replace('<u><b>', '').replace('</b></u>', '')
                message = '<br>'.join(alert_parts[1:]) if len(alert_parts) > 1 else alert
                
                body += f"""
                    <div class="{alert_class}">
                        <div class="timestamp">{timestamp}</div>
                        <div class="alert-message">{message}</div>
                    </div>
                """
            
            body += """
                </div>
            """
    
    body += """
                <div style="background-color: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 4px; padding: 15px; margin-top: 20px;">
                    <p style="margin: 0; color: #0056d2; font-weight: bold;">⚠️ Action Required:</p>
                    <p style="margin: 5px 0 0 0; color: #495057;">
                        Please review the tiltmeter data and take appropriate action if necessary. 
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

def check_and_send_seismograph_alert():
    """Check seismograph alerts and send emails if thresholds are exceeded"""
    print("Checking seismograph alerts using background API...")
    try:
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'SMG-1').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for SMG1")
            log_alert_event("ERROR", f"In check_and_send_seismograph_alert: No instrument found for SMG1", 'SMG1')
            return

        # For seismograph, use ONLY single values (not a tiltmeter)
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
        
        # Format dates for API (using instrument time which is 1 hour behind EST)
        start_time = one_minute_ago_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')
        end_time = now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')
        
        print(f"Fetching seismograph data from {start_time} to {end_time} EST")
        print(f"UTC time: {utc_now.strftime('%Y-%m-%dT%H:%M:%S')} UTC")
        print(f"EST time: {now_est.strftime('%Y-%m-%dT%H:%M:%S')} EST")
        print(f"Instrument time (1hr behind): {now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')} EST")

        # 3. Fetch background data from Syscom API
        api_key = os.environ.get('SYSCOM_API_KEY')
        if not api_key:
            print("No SYSCOM_API_KEY set in environment")
            return

        url = f"https://scs.syscom-instruments.com/public-api/v1/records/background/15092/data?start={start_time}&end={end_time}"
        headers = {"x-scs-api-key": api_key}
        response = requests.get(url, headers=headers)
        if response.status_code not in [200, 204]:
            print(f"Failed to fetch background data: {response.status_code} {response.text}")
            log_alert_event("ERROR", f"Failed to fetch background data: {response.status_code} {response.text}", 'SMG1')
            return
        
        # Handle 204 No Content response
        if response.status_code == 204:
            print("No data available for SMG1 in the last minute (204 No Content)")
            return

        data = response.json()
        background_data = data.get('data', [])
        
        if not background_data:
            print("No background data received for the last minute")
            return

        # 4. Check thresholds for each reading
        alerts_by_timestamp = {}
        for entry in background_data:
            timestamp = entry[0]  # Format: "2025-08-01T15:40:37.741-04:00"
            x_value = abs(float(entry[1]))
            y_value = abs(float(entry[2]))
            z_value = abs(float(entry[3]))
            
            # Check if we've already sent for this timestamp
            already_sent = supabase.table('sent_alerts') \
                .select('id') \
                .eq('instrument_id', 'SMG1') \
                .eq('node_id', 15092) \
                .eq('timestamp', timestamp) \
                .execute()
            if already_sent.data:
                print(f"Alert already sent for timestamp {timestamp}, skipping.")
                continue

            messages = []
            
            # Check shutdown thresholds
            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if shutdown_value and value >= shutdown_value:
                    messages.append(f"<b>Shutdown threshold reached on {axis}-axis:</b> {value:.6f}")
            
            # Check warning thresholds
            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if warning_value and value >= warning_value:
                    messages.append(f"<b>Warning threshold reached on {axis}-axis:</b> {value:.6f}")
            
            # Check alert thresholds
            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if alert_value and value >= alert_value:
                    messages.append(f"<b>Alert threshold reached on {axis}-axis:</b> {value:.6f}")

            if messages:
                alerts_by_timestamp[timestamp] = {
                    'messages': messages,
                    'timestamp': timestamp,
                    'values': {'X': x_value, 'Y': y_value, 'Z': z_value}
                }

        # 6. Send email if there are alerts
        if alerts_by_timestamp:
            # Get project information and instrument details for both SMG1 instruments from database
            project_names = []
            instrument_details = []
            
            try:
                # Check both SMG1 and SMG-1 instruments
                for smg_id in ['SMG1', 'SMG-1']:
                    instrument_info = get_project_info(smg_id)
                    if instrument_info and instrument_info['project_name']:
                        instrument_details.append(instrument_info)
                        if instrument_info['project_name'] not in project_names:
                            project_names.append(instrument_info['project_name'])
            except Exception as e:
                print(f"Error getting project info for SMG instruments: {e}")
                log_alert_event("ERROR", f"Error in check_and_send_seismograph_alert: getting project info for SMG instruments: {e}", 'SMG1')
            
            # Use combined project names or fallback
            if project_names:
                project_name = " & ".join(project_names)
            else:
                project_name = "ANC DAR BC and DGMTS Testing"  # Default fallback
                
            body = _create_seismograph_email_body(alerts_by_timestamp, "Seismograph", project_name, instrument_details)
            
            current_time = datetime.now(timezone.utc)
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"🌊 Seismograph Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print(f"Sent seismograph alert email for {len(alerts_by_timestamp)} timestamps with alerts")
                
                # Record that we've sent for each timestamp
                for timestamp, alert_data in alerts_by_timestamp.items():
                    supabase.table('sent_alerts').insert({
                        'instrument_id': 'SMG1',
                        'node_id': 15092,
                        'timestamp': alert_data['timestamp'],
                        'alert_type': 'any'
                    }).execute()
            else:
                print("No alert/warning/shutdown emails configured for SMG1")
        else:
            print("No thresholds crossed for any reading in the last minute.")
    except Exception as e:
        print(f"Error in check_and_send_seismograph_alert: {e}")

def check_and_send_smg3_seismograph_alert():
    """Check SMG-3 seismograph alerts and send emails if thresholds are exceeded"""
    print("Checking SMG-3 seismograph alerts using background API...")
    try:
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'SMG-3').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for SMG-3")
            log_alert_event("ERROR", f"Error in check_and_send_smg3_seismograph_alert: No instrument found for SMG-3", 'SMG-3')
            return

        # For seismograph, use ONLY single values (not a tiltmeter)
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
        
        # Format dates for API (using instrument time which is 1 hour behind EST)
        start_time = one_minute_ago_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')
        end_time = now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')
        
        print(f"Fetching SMG-3 seismograph data from {start_time} to {end_time} EST")
        print(f"UTC time: {utc_now.strftime('%Y-%m-%dT%H:%M:%S')} UTC")
        print(f"EST time: {now_est.strftime('%Y-%m-%dT%H:%M:%S')} EST")
        print(f"Instrument time (1hr behind): {now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')} EST")

        # 3. Fetch background data from Syscom API
        api_key = os.environ.get('SYSCOM_API_KEY')
        if not api_key:
            print("No SYSCOM_API_KEY set in environment")
            return

        url = f"https://scs.syscom-instruments.com/public-api/v1/records/background/13453/data?start={start_time}&end={end_time}"
        headers = {"x-scs-api-key": api_key}
        response = requests.get(url, headers=headers)
        if response.status_code not in [200, 204]:
            print(f"Failed to fetch SMG-3 background data: {response.status_code} {response.text}")
            log_alert_event("ERROR", f"Failed to fetch SMG-3 background data: {response.status_code} {response.text}", 'SMG-3')
            return
        
        # Handle 204 No Content response
        if response.status_code == 204:
            print("No data available for SMG-3 in the last minute (204 No Content)")
            return

        data = response.json()
        background_data = data.get('data', [])
        
        if not background_data:
            print("No SMG-3 background data received for the last minute")
            return

        # 4. Check thresholds for each reading
        alerts_by_timestamp = {}
        for entry in background_data:
            timestamp = entry[0]  # Format: "2025-08-01T15:40:37.741-04:00"
            x_value = abs(float(entry[1]))
            y_value = abs(float(entry[2]))
            z_value = abs(float(entry[3]))
            
            # Check if we've already sent for this timestamp
            already_sent = supabase.table('sent_alerts') \
                .select('id') \
                .eq('instrument_id', 'SMG-3') \
                .eq('node_id', 13453) \
                .eq('timestamp', timestamp) \
                .execute()
            if already_sent.data:
                print(f"SMG-3 alert already sent for timestamp {timestamp}, skipping.")
                continue

            messages = []
            
            # Check shutdown thresholds
            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if shutdown_value and value >= shutdown_value:
                    messages.append(f"<b>Shutdown threshold reached on {axis}-axis:</b> {value:.6f}")
            
            # Check warning thresholds
            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if warning_value and value >= warning_value:
                    messages.append(f"<b>Warning threshold reached on {axis}-axis:</b> {value:.6f}")
            
            # Check alert thresholds
            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if alert_value and value >= alert_value:
                    messages.append(f"<b>Alert threshold reached on {axis}-axis:</b> {value:.6f}")

            if messages:
                alerts_by_timestamp[timestamp] = {
                    'messages': messages,
                    'timestamp': timestamp,
                    'values': {'X': x_value, 'Y': y_value, 'Z': z_value}
                }

        # 6. Send email if there are alerts
        if alerts_by_timestamp:
            # Get project information and instrument details for SMG-3 seismograph from database
            project_name = "Unknown Project"  # Default fallback
            instrument_details = []
            
            try:
                instrument_info = get_project_info('SMG-3')
                if instrument_info:
                    instrument_details.append(instrument_info)
                    project_name = instrument_info['project_name']
            except Exception as e:
                print(f"Error getting project info for SMG-3: {e}")
                
            body = _create_seismograph_email_body(alerts_by_timestamp, "ANC DAR-BC Seismograph", project_name, instrument_details)
            
            current_time = datetime.now(timezone.utc)
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"🌊 ANC DAR-BC Seismograph Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print(f"Sent SMG-3 seismograph alert email for {len(alerts_by_timestamp)} timestamps with alerts")
                
                # Record that we've sent for each timestamp
                for timestamp, alert_data in alerts_by_timestamp.items():
                    supabase.table('sent_alerts').insert({
                        'instrument_id': 'SMG-3',
                        'node_id': 13453,
                        'timestamp': alert_data['timestamp'],
                        'alert_type': 'any'
                    }).execute()
            else:
                print("No alert/warning/shutdown emails configured for SMG-3")
        else:
            print("No thresholds crossed for any reading in the last minute for SMG-3.")
    except Exception as e:
        print(f"Error in check_and_send_smg3_seismograph_alert: {e}")
        log_alert_event("ERROR", f"Error in check_and_send_smg3_seismograph_alert: {e}", 'SMG-3')

def _create_seismograph_email_body(alerts_by_timestamp, seismograph_name, project_name, instrument_details):
    """Create HTML email body for seismograph alerts"""
    # Format project name for display
    if " & " in project_name:
        project_display = f"Projects: {project_name.replace(' & ', ' & ')}"
    else:
        project_display = f"Project: {project_name}"
        
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
                    <p>📋 {project_display}</p>
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
                    The following {seismograph_name} thresholds have been exceeded in real-time:
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
                    <h3>📊 Real-time Alert - {seismograph_name}</h3>
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
                                               <td>{alert_data['values']['X']:.6f}</td>
                                           </tr>
                                           <tr>
                                               <td>Y (Vertical)</td>
                                               <td>{alert_data['values']['Y']:.6f}</td>
                                           </tr>
                                           <tr>
                                               <td>Z (Transverse)</td>
                                               <td>{alert_data['values']['Z']:.6f}</td>
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
                        Values shown are the actual readings that exceeded thresholds.
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

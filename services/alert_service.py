import os
import requests
from datetime import datetime, timedelta, timezone
import pytz
from supabase import create_client, Client
from config import Config
from .email_service import send_email

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

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

            # Check last 6 hours instead of just 1 hour to catch recent violations
            six_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
            readings_resp = supabase.table('sensor_readings') \
                .select('*') \
                .eq('node_id', node_id) \
                .gte('timestamp', six_hours_ago) \
                .order('timestamp', desc=False) \
                .execute()
            readings = readings_resp.data if readings_resp.data else []

            node_messages = []
            for reading in readings:
                timestamp = reading['timestamp']
                x = reading.get('x_value')
                y = reading.get('y_value')
                z = reading.get('z_value')

                # Check if we've already sent for this timestamp within the last 6 hours
                # This allows re-sending alerts for persistent threshold violations
                six_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
                already_sent = supabase.table('sent_alerts') \
                    .select('id, created_at') \
                    .eq('instrument_id', instrument_id) \
                    .eq('node_id', node_id) \
                    .eq('timestamp', timestamp) \
                    .gte('created_at', six_hours_ago) \
                    .execute()
                if already_sent.data:
                    print(f"DEBUG: Alert already sent for node {node_id} at {timestamp} within the last 6 hours, skipping.")
                    print(f"DEBUG: Found {len(already_sent.data)} existing alert records")
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
            # Create email body with professional styling
            body = _create_tiltmeter_email_body(node_alerts, node_ids)
            
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

def _create_tiltmeter_email_body(node_alerts, node_ids):
    """Create HTML email body for tiltmeter alerts"""
    body = """
    <html>
    <head>
        <style>
            body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }
            .container { max-width: 600px; margin: 0 auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }
            .header { background: linear-gradient(135deg, #0056d2 0%, #007bff 100%); color: white; padding: 20px; text-align: center; }
            .header h1 { margin: 0; font-size: 24px; font-weight: bold; }
            .header p { margin: 5px 0 0 0; opacity: 0.9; }
            .content { padding: 30px; }
            .alert-section { margin-bottom: 25px; }
            .alert-section h3 { color: #0056d2; border-bottom: 2px solid #0056d2; padding-bottom: 10px; margin-bottom: 15px; }
            .alert-item { background-color: #f8f9fa; border-left: 4px solid #dc3545; padding: 15px; margin-bottom: 10px; border-radius: 4px; }
            .alert-item.warning { border-left-color: #ffc107; }
            .alert-item.alert { border-left-color: #fd7e14; }
            .alert-item.shutdown { border-left-color: #dc3545; }
            .timestamp { font-weight: bold; color: #495057; margin-bottom: 10px; }
            .alert-message { color: #212529; line-height: 1.5; }
            .max-values { background-color: #e9ecef; padding: 10px; border-radius: 4px; margin-top: 10px; }
            .max-values table { width: 100%; border-collapse: collapse; }
            .max-values th, .max-values td { padding: 8px; text-align: center; border: 1px solid #dee2e6; }
            .max-values th { background-color: #f8f9fa; font-weight: bold; }
            .footer { background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }
            .footer p { margin: 0; }
            .company-info { font-weight: bold; color: #0056d2; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🚨 TILTMETER ALERT NOTIFICATION</h1>
                <p>Dulles Geotechnical Monitoring System</p>
            </div>
            
            <div class="content">
                <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                    This is an automated alert notification from the DGMTS monitoring system. 
                    The following tiltmeter thresholds have been exceeded in the last hour:
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
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'SMG1').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for SMG1")
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
        
        print(f"Fetching seismograph data from {start_time} to {end_time} EST")

        # 3. Fetch background data from Syscom API
        api_key = os.environ.get('SYSCOM_API_KEY')
        if not api_key:
            print("No SYSCOM_API_KEY set in environment")
            return

        url = f"https://scs.syscom-instruments.com/public-api/v1/records/background/15092/data?start={start_time}&end={end_time}"
        headers = {"x-scs-api-key": api_key}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Failed to fetch background data: {response.status_code} {response.text}")
            return

        data = response.json()
        background_data = data.get('data', [])
        
        if not background_data:
            print("No background data received for the last hour")
            return

        print(f"Received {len(background_data)} data points")

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
                .eq('instrument_id', 'SMG1') \
                .eq('node_id', 15092) \
                .eq('timestamp', timestamp) \
                .execute()
            if already_sent.data:
                print(f"Alert already sent for hour {hour_key}, skipping.")
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
            body = _create_seismograph_email_body(alerts_by_hour, "Seismograph")
            
            current_time = datetime.now(timezone.utc)
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"🌊 Seismograph Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print(f"Sent seismograph alert email for {len(alerts_by_hour)} hours with alerts")
                
                # Record that we've sent for each hour
                for hour_key, alert_data in alerts_by_hour.items():
                    supabase.table('sent_alerts').insert({
                        'instrument_id': 'SMG1',
                        'node_id': 15092,
                        'timestamp': alert_data['timestamp'],
                        'alert_type': 'any'
                    }).execute()
            else:
                print("No alert/warning/shutdown emails configured for SMG1")
        else:
            print("No thresholds crossed for any hour in the last hour.")
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
        
        print(f"Fetching SMG-3 seismograph data from {start_time} to {end_time} EST")

        # 3. Fetch background data from Syscom API
        api_key = os.environ.get('SYSCOM_API_KEY')
        if not api_key:
            print("No SYSCOM_API_KEY set in environment")
            return

        url = f"https://scs.syscom-instruments.com/public-api/v1/records/background/13453/data?start={start_time}&end={end_time}"
        headers = {"x-scs-api-key": api_key}
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Failed to fetch SMG-3 background data: {response.status_code} {response.text}")
            return

        data = response.json()
        background_data = data.get('data', [])
        
        if not background_data:
            print("No SMG-3 background data received for the last hour")
            return

        print(f"Received {len(background_data)} SMG-3 data points")

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
                .eq('instrument_id', 'SMG-3') \
                .eq('node_id', 13453) \
                .eq('timestamp', timestamp) \
                .execute()
            if already_sent.data:
                print(f"SMG-3 alert already sent for hour {hour_key}, skipping.")
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
            body = _create_seismograph_email_body(alerts_by_hour, "ANC DAR-BC Seismograph")
            
            current_time = datetime.now(timezone.utc)
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"🌊 ANC DAR-BC Seismograph Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print(f"Sent SMG-3 seismograph alert email for {len(alerts_by_hour)} hours with alerts")
                
                # Record that we've sent for each hour
                for hour_key, alert_data in alerts_by_hour.items():
                    supabase.table('sent_alerts').insert({
                        'instrument_id': 'SMG-3',
                        'node_id': 13453,
                        'timestamp': alert_data['timestamp'],
                        'alert_type': 'any'
                    }).execute()
            else:
                print("No alert/warning/shutdown emails configured for SMG-3")
        else:
            print("No thresholds crossed for any hour in the last hour for SMG-3.")
    except Exception as e:
        print(f"Error in check_and_send_smg3_seismograph_alert: {e}")

def _create_seismograph_email_body(alerts_by_hour, seismograph_name):
    """Create HTML email body for seismograph alerts"""
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
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🌊 {seismograph_name.upper()} ALERT NOTIFICATION</h1>
                <p>Dulles Geotechnical Monitoring System</p>
            </div>
            
            <div class="content">
                <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                    This is an automated alert notification from the DGMTS monitoring system. 
                    The following {seismograph_name} thresholds have been exceeded in the last hour:
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
                    <h3>📊 Hour: {hour_key.replace('-', ' ')} - {seismograph_name} Alerts</h3>
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

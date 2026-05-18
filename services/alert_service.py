"""
Alert Service for DGMS Backend

TILTMETER ALERTS ARE NOW ENABLED WITH TIME-BASED REFERENCE SYSTEM
===============================================================

The tiltmeter alert system now includes:

1. TIME-BASED REFERENCE VALUES:
   - Checks time_based_reference_values table for active periods
   - Uses reference values based on current datetime
   - Falls back to global reference_values table if no active period

2. AUTOMATIC TRIGGERS:
   - In services/sensor_service.py: Triggers when new tiltmeter data is inserted
   - Uses the new time-based reference system automatically

3. MANUAL TRIGGERS:
   - /trigger-tiltmeter-alerts: Manual trigger endpoint
   - /test-tiltmeter-alerts-with-time-based-refs: Test endpoint with detailed logging

4. TESTING:
   - /test-time-based-references: Test only the reference system
   - Console logs show which reference values are being used (time-based vs global)

The check_and_send_tiltmeter_alerts() function now uses the enhanced time-based reference system.
"""

import os
import requests
from datetime import datetime, timedelta, timezone
import pytz
from supabase import create_client, Client
from config import Config
from .email_service import send_email

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

SYSCOM_INSTRUMENT_FIXED_OFFSET = timezone(timedelta(hours=-5))


def _format_syscom_timestamp_to_est(timestamp_str, fmt='%m-%d-%Y %I:%M:%S %p EST'):
    """Format a Syscom-API timestamp string for display in real US/Eastern time.

    Syscom instrument clocks are configured to a fixed UTC-5 offset (i.e. EST
    without DST). The API returns timestamps in that instrument-local clock,
    typically as a naive ISO-8601 string. Treating that naive string as the
    server's local time produces a value that is 1 hour behind real US/Eastern
    during EDT (and matches real US/Eastern during EST).

    This helper attaches the fixed UTC-5 offset to naive timestamps before
    converting to ``US/Eastern`` (which auto-handles DST), so the displayed
    time always matches real local time in Kennesaw, GA / EDT.

    Returns ``None`` if the input is empty/falsy. Raises on bad input so callers
    can fall back to the raw string in their existing ``try/except`` blocks.
    """
    if not timestamp_str:
        return None
    cleaned = timestamp_str.replace('Z', '+00:00') if timestamp_str.endswith('Z') else timestamp_str
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SYSCOM_INSTRUMENT_FIXED_OFFSET)
    dt_est = dt.astimezone(pytz.timezone('US/Eastern'))
    return dt_est.strftime(fmt)


def _determine_alert_type(messages):
    """Determine the highest priority alert type based on messages"""
    # Check for shutdown threshold messages first (highest priority)
    if any("Shutdown threshold reached" in msg for msg in messages):
        return "shutdown"
    # Check for warning threshold messages (medium priority)
    elif any("Warning threshold reached" in msg for msg in messages):
        return "warning"
    # Check for alert threshold messages (lowest priority)
    elif any("Alert threshold reached" in msg for msg in messages):
        return "alert"
    # Fallback to 'any' if we can't determine
    return "any"

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

def get_time_based_reference_values(instrument_id, current_datetime=None):
    """
    Get time-based reference values for the given instrument_id and current datetime.
    
    Args:
        instrument_id (str): The instrument ID (e.g., 'TILT-142939', 'TILT-143969')
        current_datetime (datetime): Current datetime to check against periods. 
                                   If None, uses current UTC time.
    
    Returns:
        dict: Reference values dict with keys: x_reference_value, y_reference_value, z_reference_value
              Returns None if no active period found
    """
    if current_datetime is None:
        current_datetime = datetime.now(timezone.utc)
    
    try:
        print(f"Checking time-based reference values for {instrument_id} at {current_datetime}")
        
        # Query time_based_reference_values table for active periods
        response = supabase.table('time_based_reference_values') \
            .select('*') \
            .eq('instrument_id', instrument_id) \
            .execute()
        
        if not response.data:
            print(f"No time-based reference values found for {instrument_id}")
            return None
        
        # Find the active period that contains the current datetime
        active_period = None
        for period in response.data:
            from_date = period.get('from_date')
            to_date = period.get('to_date')
            
            # Convert string dates to datetime objects if they exist
            if from_date:
                if isinstance(from_date, str):
                    # Handle different string formats
                    if 'T' in from_date:
                        if from_date.endswith('Z'):
                            from_date = datetime.fromisoformat(from_date.replace('Z', '+00:00'))
                        else:
                            # String format like "2025-07-22T00:00:00" - add timezone
                            from_date = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
                    else:
                        # Handle date-only format by adding timezone
                        from_date = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
                elif hasattr(from_date, 'tzinfo') and from_date.tzinfo is None:
                    # If it's a naive datetime, make it timezone-aware
                    from_date = from_date.replace(tzinfo=timezone.utc)
            
            if to_date:
                if isinstance(to_date, str):
                    # Handle different string formats
                    if 'T' in to_date:
                        if to_date.endswith('Z'):
                            to_date = datetime.fromisoformat(to_date.replace('Z', '+00:00'))
                        else:
                            # String format like "2025-10-10T07:00:00" - add timezone
                            to_date = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc)
                    else:
                        # Handle date-only format by adding timezone
                        to_date = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc)
                elif hasattr(to_date, 'tzinfo') and to_date.tzinfo is None:
                    # If it's a naive datetime, make it timezone-aware
                    to_date = to_date.replace(tzinfo=timezone.utc)
            
            # Check if current datetime falls within this period
            period_active = True
            
            if from_date and current_datetime < from_date:
                period_active = False
            
            if to_date and current_datetime > to_date:
                period_active = False
            
            if period_active:
                active_period = period
                break
        
        if active_period:
            # Return the reference values in the format expected by the existing code
            return {
                'x_reference_value': active_period.get('x_reference_value'),
                'y_reference_value': active_period.get('y_reference_value'),
                'z_reference_value': active_period.get('z_reference_value'),
                'enabled': True,
                'time_based': True,
                'period_id': active_period.get('id'),
                'from_date': active_period.get('from_date'),
                'to_date': active_period.get('to_date')
            }
        else:
            print(f"No active time-based period found for {instrument_id} at {current_datetime}")
            return None
            
    except Exception as e:
        print(f"Error getting time-based reference values for {instrument_id}: {e}")
        return None

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

def test_time_based_reference_system():
    """
    Test function to verify the time-based reference system works correctly.
    This function can be called manually to test different scenarios.
    """
    # Test with current datetime
    current_time = datetime.now(timezone.utc)
    
    # Test both tiltmeter instruments
    test_instruments = ['TILT-142939', 'TILT-143969']
    
    for instrument_id in test_instruments:
        # Test with current datetime
        ref_values = get_time_based_reference_values(instrument_id, current_time)
        if ref_values:
            print(f"✅ Using time-based reference values for {instrument_id}")
        else:
            print(f"⚠️  No time-based reference values found for {instrument_id}, using global fallback")

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
            
            # 1. First try to get time-based reference values for current datetime
            time_based_reference_values = get_time_based_reference_values(instrument_id)
            
            # 2. If no time-based reference found, fall back to global reference_values table
            if time_based_reference_values:
                reference_values = time_based_reference_values
            else:
                # Fall back to original reference_values table
                reference_resp = supabase.table('reference_values').select('*').eq('instrument_id', instrument_id).execute()
                reference_values = reference_resp.data[0] if reference_resp.data else None
            
            # 3. Get instrument settings for this node's instrument_id
            instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
            instrument = instrument_resp.data[0] if instrument_resp.data else None
            if not instrument:
                print(f"No instrument found for {instrument_id}")
                continue

            # 4. Determine which threshold values to use
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
                    formatted_time = dt_est.strftime('%m-%d-%Y %I:%M %p EST')
                except Exception as e:
                    print(f"Failed to parse/convert timestamp: {timestamp}, error: {e}")
                    formatted_time = timestamp

                messages = []
                
                # Calculate calibrated values when reference values are enabled
                if reference_values and reference_values.get('enabled', False):
                    ref_x = reference_values.get('x_reference_value') or 0
                    ref_y = reference_values.get('y_reference_value') or 0
                    ref_z = reference_values.get('z_reference_value') or 0
                    
                    # Calculate calibrated values (raw - reference) to match frontend logic
                    calibrated_x = x - ref_x if x is not None else None
                    calibrated_y = y - ref_y if y is not None else None
                    calibrated_z = z - ref_z if z is not None else None
                    
                    ref_type = "time-based" if reference_values.get('time_based', False) else "global"
                    
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
                    # Determine the highest priority alert type
                    alert_type = _determine_alert_type(messages)
                    
                    # Record that we've sent for this timestamp (use correct instrument_id)
                    supabase.table('sent_alerts').insert({
                        'instrument_id': instrument_id,
                        'node_id': node_id,
                        'timestamp': timestamp,
                        'alert_type': alert_type,
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
            formatted_time = current_time_est.strftime('%m-%d-%Y %I:%M %p EST')
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


def _legacy_syscom_device_id(instrument_id_str: str):
    """Historical hardcoded Syscom device ids when DB column is null."""
    return {
        'SMG-1': 15092,
        'SMG-3': 13453,
        '13453': 13453,
    }.get(str(instrument_id_str).strip())


def _check_single_syscom_background_instrument(instrument, custom_emails=None):
    """Fetch Syscom background data for one instrument row; email if thresholds exceeded (6h window)."""
    instrument_id_str = str(instrument.get('instrument_id', '')).strip()
    if not instrument_id_str:
        print("Skipping syscom alert: empty instrument_id")
        return
    device_raw = instrument.get('syscom_device_id')
    if device_raw is None:
        device_raw = _legacy_syscom_device_id(instrument_id_str)
    if device_raw is None:
        print(f"Skipping syscom alert for {instrument_id_str}: set syscom_device_id on the instrument row")
        return
    try:
        device_id = int(device_raw)
    except (TypeError, ValueError):
        log_alert_event("ERROR", f"Invalid syscom_device_id for {instrument_id_str}: {device_raw}", instrument_id_str)
        return

    alert_value = instrument.get('alert_value')
    warning_value = instrument.get('warning_value')
    shutdown_value = instrument.get('shutdown_value')

    alert_emails = list(instrument.get('alert_emails') or [])
    warning_emails = list(instrument.get('warning_emails') or [])
    shutdown_emails = list(instrument.get('shutdown_emails') or [])

    if custom_emails:
        alert_emails = custom_emails
        warning_emails = custom_emails
        shutdown_emails = custom_emails
        print(f"Using custom emails for test ({instrument_id_str}): {custom_emails}")

    utc_now = datetime.now(timezone.utc)
    est_tz = pytz.timezone('US/Eastern')
    now_est = utc_now.astimezone(est_tz)
    now_instrument_time = now_est - timedelta(hours=1)
    six_hours_ago_instrument_time = now_instrument_time - timedelta(hours=6)

    start_time = six_hours_ago_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')
    end_time = now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')

    print(
        f"[{instrument_id_str}] Syscom device {device_id}: fetching background {start_time} → {end_time} "
        f"(instrument clock ~1h behind EST, 6h window)"
    )

    api_key = os.environ.get('SYSCOM_API_KEY') or Config.SYSCOM_API_KEY
    if not api_key:
        print("No SYSCOM_API_KEY set in environment")
        return

    url = (
        f"https://scs.syscom-instruments.com/public-api/v1/records/background/{device_id}/data"
        f"?start={start_time}&end={end_time}"
    )
    headers = {"x-scs-api-key": api_key}
    response = requests.get(url, headers=headers)
    if response.status_code not in [200, 204]:
        print(f"[{instrument_id_str}] Failed to fetch background data: {response.status_code} {response.text}")
        log_alert_event(
            "ERROR",
            f"Failed to fetch background data: {response.status_code} {response.text}",
            instrument_id_str,
        )
        return

    if response.status_code == 204:
        print(f"[{instrument_id_str}] No data in window (204)")
        return

    data = response.json()
    background_data = data.get('data', [])

    if not background_data:
        print(f"[{instrument_id_str}] No background data in response")
        return

    print(f"[{instrument_id_str}] Received {len(background_data)} readings from Syscom")

    readings_with_exceeded_thresholds = []
    for entry in background_data:
        timestamp = entry[0]
        x_value = abs(float(entry[1]))
        y_value = abs(float(entry[2]))
        z_value = abs(float(entry[3]))

        threshold_exceeded = False
        if (shutdown_value and (x_value >= shutdown_value or y_value >= shutdown_value or z_value >= shutdown_value)) or \
           (warning_value and (x_value >= warning_value or y_value >= warning_value or z_value >= warning_value)) or \
           (alert_value and (x_value >= alert_value or y_value >= alert_value or z_value >= alert_value)):
            threshold_exceeded = True

        if threshold_exceeded:
            readings_with_exceeded_thresholds.append({
                'timestamp': timestamp,
                'x_value': x_value,
                'y_value': y_value,
                'z_value': z_value,
            })

    if not readings_with_exceeded_thresholds:
        print(f"[{instrument_id_str}] No threshold crossings in window")
        return

    alerts_by_timestamp = {}
    for reading in readings_with_exceeded_thresholds:
        timestamp = reading['timestamp']
        x_value = reading['x_value']
        y_value = reading['y_value']
        z_value = reading['z_value']

        already_sent = (
            supabase.table('sent_alerts')
            .select('id')
            .eq('instrument_id', instrument_id_str)
            .eq('node_id', device_id)
            .eq('timestamp', timestamp)
            .execute()
        )
        if already_sent.data:
            continue

        messages = []
        for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
            if shutdown_value and value >= shutdown_value:
                messages.append(f"<b>Shutdown threshold reached on {axis}-axis:</b> {value:.6f}")
        for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
            if warning_value and value >= warning_value:
                messages.append(f"<b>Warning threshold reached on {axis}-axis:</b> {value:.6f}")
        for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
            if alert_value and value >= alert_value:
                messages.append(f"<b>Alert threshold reached on {axis}-axis:</b> {value:.6f}")

        if messages:
            alerts_by_timestamp[timestamp] = {
                'messages': messages,
                'timestamp': timestamp,
                'values': {'X': x_value, 'Y': y_value, 'Z': z_value},
            }

    if not alerts_by_timestamp:
        print(f"[{instrument_id_str}] All threshold crossings already notified")
        return

    instrument_details = []
    project_name = "ANC DAR BC"
    try:
        instrument_info = get_project_info(instrument_id_str)
        if instrument_info:
            instrument_details.append(instrument_info)
            if instrument_info.get('project_name'):
                project_name = instrument_info['project_name']
    except Exception as e:
        print(f"[{instrument_id_str}] Error getting project info: {e}")
        log_alert_event("ERROR", f"get_project_info failed: {e}", instrument_id_str)

    display_name = instrument.get('instrument_name') or 'Seismograph'
    body = _create_seismograph_email_body(
        alerts_by_timestamp,
        display_name,
        project_name,
        instrument_details,
    )

    current_time = datetime.now(timezone.utc)
    current_time_est = current_time.astimezone(est_tz)
    formatted_time = current_time_est.strftime('%m-%d-%Y %I:%M %p EST')
    subject = f"🌊 Seismograph Alert — {instrument_id_str} — {formatted_time}"

    all_emails = set(alert_emails + warning_emails + shutdown_emails)
    if not all_emails:
        print(f"[{instrument_id_str}] No alert/warning/shutdown emails configured")
        return

    email_sent = send_email(",".join(all_emails), subject, body)
    if not email_sent:
        log_alert_event("SEND EMAIL_FAILED", f"Failed to send alert email", instrument_id_str)
        return

    print(f"[{instrument_id_str}] Alert email sent to {len(all_emails)} recipient(s)")
    for _ts, alert_data in alerts_by_timestamp.items():
        alert_type = _determine_alert_type(alert_data['messages'])
        sent_alert_resp = (
            supabase.table('sent_alerts')
            .insert({
                'instrument_id': instrument_id_str,
                'node_id': device_id,
                'timestamp': alert_data['timestamp'],
                'alert_type': alert_type,
            })
            .execute()
        )
        if sent_alert_resp.data:
            alert_id = sent_alert_resp.data[0]['id']
            log_alert_event(
                "ALERT_RECORDED",
                f"Alert recorded with ID {alert_id}",
                instrument_id_str,
                alert_id,
            )


def check_and_send_seismograph_alert(custom_emails=None):
    """SMG-1: Syscom background seismograph alerts (6h window, device 15092)."""
    print("Checking seismograph alerts using background API...")
    try:
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'SMG-1').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for SMG-1")
            log_alert_event("ERROR", f"In check_and_send_seismograph_alert: No instrument found for SMG-1", 'SMG-1')
            return

        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')

        alert_emails = instrument.get('alert_emails') or []
        warning_emails = instrument.get('warning_emails') or []
        shutdown_emails = instrument.get('shutdown_emails') or []

        if custom_emails:
            alert_emails = custom_emails
            warning_emails = custom_emails
            shutdown_emails = custom_emails
            print(f"Using custom emails for test: {custom_emails}")

        utc_now = datetime.now(timezone.utc)
        est_tz = pytz.timezone('US/Eastern')
        now_est = utc_now.astimezone(est_tz)
        now_instrument_time = now_est - timedelta(hours=1)
        six_hours_ago_instrument_time = now_instrument_time - timedelta(hours=6)

        start_time = six_hours_ago_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')
        end_time = now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')

        print(f"Fetching SMG-1 seismograph data from {start_time} to {end_time} EST (last 6 hours)")
        print(f"UTC time: {utc_now.strftime('%Y-%m-%dT%H:%M:%S')} UTC")
        print(f"EST time: {now_est.strftime('%Y-%m-%dT%H:%M:%S')} EST")
        print(f"Instrument time (1hr behind): {now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')} EST")

        api_key = os.environ.get('SYSCOM_API_KEY')
        if not api_key:
            print("No SYSCOM_API_KEY set in environment")
            return

        url = f"https://scs.syscom-instruments.com/public-api/v1/records/background/15092/data?start={start_time}&end={end_time}"
        headers = {"x-scs-api-key": api_key}
        response = requests.get(url, headers=headers)
        if response.status_code not in [200, 204]:
            print(f"Failed to fetch background data: {response.status_code} {response.text}")
            log_alert_event("ERROR", f"Failed to fetch background data: {response.status_code} {response.text}", 'SMG-1')
            return

        if response.status_code == 204:
            print("No data available for SMG-1 in the last 6 hours (204 No Content)")
            return

        data = response.json()
        background_data = data.get('data', [])

        if not background_data:
            print("No background data received for SMG-1 in the last 6 hours")
            return

        print(f"Received {len(background_data)} readings from API for SMG-1")

        print(f"Step 1: Filtering readings that exceed thresholds (in memory)...")
        readings_with_exceeded_thresholds = []

        for entry in background_data:
            timestamp = entry[0]
            x_value = abs(float(entry[1]))
            y_value = abs(float(entry[2]))
            z_value = abs(float(entry[3]))

            threshold_exceeded = False
            if (shutdown_value and (x_value >= shutdown_value or y_value >= shutdown_value or z_value >= shutdown_value)) or \
               (warning_value and (x_value >= warning_value or y_value >= warning_value or z_value >= warning_value)) or \
               (alert_value and (x_value >= alert_value or y_value >= alert_value or z_value >= alert_value)):
                threshold_exceeded = True

            if threshold_exceeded:
                readings_with_exceeded_thresholds.append({
                    'timestamp': timestamp,
                    'x_value': x_value,
                    'y_value': y_value,
                    'z_value': z_value
                })

        print(f"Step 1 Complete: Found {len(readings_with_exceeded_thresholds)} readings that exceed thresholds (out of {len(background_data)} total)")

        if not readings_with_exceeded_thresholds:
            print("No thresholds crossed for any reading in the last 6 hours for SMG-1.")
            return

        print(f"Step 2: Checking database for already-sent alerts (only {len(readings_with_exceeded_thresholds)} queries instead of {len(background_data)})...")
        alerts_by_timestamp = {}

        for reading in readings_with_exceeded_thresholds:
            timestamp = reading['timestamp']
            x_value = reading['x_value']
            y_value = reading['y_value']
            z_value = reading['z_value']

            already_sent = supabase.table('sent_alerts') \
                .select('id') \
                .eq('instrument_id', 'SMG-1') \
                .eq('node_id', 15092) \
                .eq('timestamp', timestamp) \
                .execute()
            if already_sent.data:
                print(f"SMG-1 alert already sent for timestamp {timestamp}, skipping.")
                continue

            messages = []

            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if shutdown_value and value >= shutdown_value:
                    messages.append(f"<b>Shutdown threshold reached on {axis}-axis:</b> {value:.6f}")

            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if warning_value and value >= warning_value:
                    messages.append(f"<b>Warning threshold reached on {axis}-axis:</b> {value:.6f}")

            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if alert_value and value >= alert_value:
                    messages.append(f"<b>Alert threshold reached on {axis}-axis:</b> {value:.6f}")

            if messages:
                alerts_by_timestamp[timestamp] = {
                    'messages': messages,
                    'timestamp': timestamp,
                    'values': {'X': x_value, 'Y': y_value, 'Z': z_value}
                }

        print(f"Step 2 Complete: {len(alerts_by_timestamp)} new alerts to send (after filtering out already-sent)")

        if alerts_by_timestamp:
            project_names = []
            instrument_details = []

            try:
                for smg_id in ['SMG-1']:
                    instrument_info = get_project_info(smg_id)
                    if instrument_info and instrument_info['project_name']:
                        instrument_details.append(instrument_info)
                        if instrument_info['project_name'] not in project_names:
                            project_names.append(instrument_info['project_name'])
            except Exception as e:
                print(f"Error getting project info for SMG instruments: {e}")
                log_alert_event("ERROR", f"Error in check_and_send_seismograph_alert: getting project info for SMG instruments: {e}", 'SMG1')

            if project_names:
                project_name = " & ".join(project_names)
            else:
                project_name = "ANC DAR BC"

            body = _create_seismograph_email_body(alerts_by_timestamp, "Seismograph", project_name, instrument_details)

            current_time = datetime.now(timezone.utc)
            current_time_est = current_time.astimezone(est_tz)
            formatted_time = current_time_est.strftime('%m-%d-%Y %I:%M %p EST')
            subject = f"🌊 Seismograph Alert Notification - {formatted_time}"

            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                email_sent = send_email(",".join(all_emails), subject, body)
                if email_sent:
                    print(f"Alert email sent successfully for SMG-1 to {len(all_emails)} recipients")
                    for timestamp, alert_data in alerts_by_timestamp.items():
                        alert_type = _determine_alert_type(alert_data['messages'])

                        sent_alert_resp = supabase.table('sent_alerts').insert({
                            'instrument_id': 'SMG-1',
                            'node_id': 15092,
                            'timestamp': alert_data['timestamp'],
                            'alert_type': alert_type
                        }).execute()
                        if sent_alert_resp.data:
                            alert_id = sent_alert_resp.data[0]['id']
                            log_alert_event("ALERT_RECORDED", f"Alert recorded in sent_alerts table with ID {alert_id}", 'SMG-1', alert_id)
                else:
                    log_alert_event("SEND EMAIL_FAILED", f"Failed to send alert email for SMG-1", 'SMG-1')
            else:
                print("No alert/warning/shutdown emails configured for SMG-1")
        else:
            print("No thresholds crossed for any reading in the last 6 hours for SMG-1.")
    except Exception as e:
        print(f"Error in check_and_send_seismograph_alert: {e}")
        log_alert_event("ERROR", f"Error in check_and_send_seismograph_alert: {e}", 'SMG-1')


def check_and_send_smg3_seismograph_alert():
    """SMG-3: separate schedule/logic (1 minute window, Syscom device 13453)."""
    print("Checking SMG-3 seismograph alerts using background API...")
    try:
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'SMG-3').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for SMG-3")
            log_alert_event("ERROR", f"Error in check_and_send_smg3_seismograph_alert: No instrument found for SMG-3", 'SMG-3')
            return

        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')

        alert_emails = instrument.get('alert_emails') or []
        warning_emails = instrument.get('warning_emails') or []
        shutdown_emails = instrument.get('shutdown_emails') or []

        utc_now = datetime.now(timezone.utc)
        est_tz = pytz.timezone('US/Eastern')
        now_est = utc_now.astimezone(est_tz)
        now_instrument_time = now_est - timedelta(hours=1)
        one_minute_ago_instrument_time = now_instrument_time - timedelta(minutes=1)

        start_time = one_minute_ago_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')
        end_time = now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')

        print(f"Fetching SMG-3 seismograph data from {start_time} to {end_time} EST")
        print(f"UTC time: {utc_now.strftime('%Y-%m-%dT%H:%M:%S')} UTC")
        print(f"EST time: {now_est.strftime('%Y-%m-%dT%H:%M:%S')} EST")
        print(f"Instrument time (1hr behind): {now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')} EST")

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

        if response.status_code == 204:
            print("No data available for SMG-3 in the last minute (204 No Content)")
            return

        data = response.json()
        background_data = data.get('data', [])

        if not background_data:
            print("No SMG-3 background data received for the last minute")
            return

        alerts_by_timestamp = {}
        for entry in background_data:
            timestamp = entry[0]
            x_value = abs(float(entry[1]))
            y_value = abs(float(entry[2]))
            z_value = abs(float(entry[3]))

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

            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if shutdown_value and value >= shutdown_value:
                    messages.append(f"<b>Shutdown threshold reached on {axis}-axis:</b> {value:.6f}")

            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if warning_value and value >= warning_value:
                    messages.append(f"<b>Warning threshold reached on {axis}-axis:</b> {value:.6f}")

            for axis, value in [('X', x_value), ('Y', y_value), ('Z', z_value)]:
                if alert_value and value >= alert_value:
                    messages.append(f"<b>Alert threshold reached on {axis}-axis:</b> {value:.6f}")

            if messages:
                alerts_by_timestamp[timestamp] = {
                    'messages': messages,
                    'timestamp': timestamp,
                    'values': {'X': x_value, 'Y': y_value, 'Z': z_value}
                }

        if alerts_by_timestamp:
            project_name = "Unknown Project"
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
            current_time_est = current_time.astimezone(est_tz)
            formatted_time = current_time_est.strftime('%m-%d-%Y %I:%M %p EST')
            subject = f"🌊 ANC DAR-BC Seismograph Alert Notification - {formatted_time}"

            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print(f"Sent SMG-3 seismograph alert email for {len(alerts_by_timestamp)} timestamps with alerts")

                for timestamp, alert_data in alerts_by_timestamp.items():
                    alert_type = _determine_alert_type(alert_data['messages'])

                    supabase.table('sent_alerts').insert({
                        'instrument_id': 'SMG-3',
                        'node_id': 13453,
                        'timestamp': alert_data['timestamp'],
                        'alert_type': alert_type
                    }).execute()
            else:
                print("No alert/warning/shutdown emails configured for SMG-3")
        else:
            print("No thresholds crossed for any reading in the last minute for SMG-3.")
    except Exception as e:
        print(f"Error in check_and_send_smg3_seismograph_alert: {e}")
        log_alert_event("ERROR", f"Error in check_and_send_smg3_seismograph_alert: {e}", 'SMG-3')


def check_and_send_seismograph_instrument_13453_alert():
    """New ANC Syscom seismograph (instruments.instrument_id = 13453). Same pattern as SMG-1: 6h background window."""
    print("Checking seismograph alerts for instrument 13453 (SMG-1-style / 6h window)...")
    try:
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', '13453').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument row found for instrument_id 13453 — skipping")
            log_alert_event("ERROR", "check_and_send_seismograph_instrument_13453_alert: No instrument found", '13453')
            return
        _check_single_syscom_background_instrument(instrument)
    except Exception as e:
        print(f"Error in check_and_send_seismograph_instrument_13453_alert: {e}")
        log_alert_event("ERROR", f"Error in check_and_send_seismograph_instrument_13453_alert: {e}", '13453')


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
        # Format timestamp to real US/Eastern (handles Syscom's fixed UTC-5 instrument clock)
        try:
            formatted_time = _format_syscom_timestamp_to_est(alert_data['timestamp'])
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

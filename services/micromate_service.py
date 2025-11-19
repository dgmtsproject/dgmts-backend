import os
import requests
import csv
import glob
from datetime import datetime, timedelta, timezone
import pytz
from supabase import create_client, Client
from config import Config
from .email_service import send_email

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

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
    """Log alert events to sent_alert_logs table
    
    Note: INFO logs are skipped for Instantel 1 and Instantel 2 to reduce noise.
    Only ERROR, EMAIL_SENT, ALERT_RECORDED, and other important log types are recorded.
    """
    # Skip INFO logs for Instantel instruments
    if log_type == "INFO" and instrument_id in ["Instantel 1", "Instantel 2"]:
        print(f"Skipped INFO log for {instrument_id}: {log_text}")
        return
    
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

def check_and_send_micromate_alert(custom_emails=None, time_window_minutes=30, force_resend=False):
    """Check Instantel Micromate alerts and send emails if thresholds are exceeded
    
    This function checks ALL readings from the last 30 minutes (default). Since readings are
    inserted in batches every 30 minutes with 5-minute intervals, we need to check all readings
    in the time window to ensure no alerts are missed.
    
    For each reading:
    - If thresholds are exceeded AND no alert has been sent for that timestamp, send an alert
    - If alert was already sent for that timestamp, skip it
    
    Args:
        custom_emails (list, optional): Custom email addresses to use instead of instrument emails
        time_window_minutes (int, optional): Time window in minutes to check for alerts. Default is 30 minutes.
        force_resend (bool, optional): If True, will resend alerts even if they were already sent (for testing). Default is False.
    
    Returns:
        dict: Summary of the alert check including:
            - total_readings_checked: Number of readings checked
            - readings_with_alerts: Number of readings that exceeded thresholds
            - readings_already_sent: Number of readings that already had alerts sent
            - emails_sent: Number of emails sent
            - alert_timestamps: List of timestamps that had alerts
            - skipped_timestamps: List of timestamps that were skipped (already sent)
    """
    print("Checking Instantel Micromate alerts...")
    result_summary = {
        'total_readings_checked': 0,
        'readings_with_alerts': 0,
        'readings_already_sent': 0,
        'emails_sent': 0,
        'alert_timestamps': [],
        'skipped_timestamps': []
    }
    try:
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'Instantel 1').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for Instantel 1")
            log_alert_event("ERROR", f"In check_and_send_micromate_alert: No instrument found for Instantel 1", 'Instantel 1')
            result_summary['error'] = "No instrument found for Instantel 1"
            return result_summary

        # For micromate, use single values for each axis
        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')
        
        # Print threshold values for debugging
        print("=" * 80)
        print("THRESHOLD VALUES:")
        print("=" * 80)
        print(f"Alert Value: {alert_value}")
        print(f"Warning Value: {warning_value}")
        print(f"Shutdown Value: {shutdown_value}")
        print("=" * 80)
        
        alert_emails = instrument.get('alert_emails') or []
        warning_emails = instrument.get('warning_emails') or []
        shutdown_emails = instrument.get('shutdown_emails') or []
        
        # Check if emails are configured
        if not alert_emails and not warning_emails and not shutdown_emails:
            print("⚠️  WARNING: No alert emails configured! Alerts cannot be sent.")
            log_alert_event("ERROR", "No alert/warning/shutdown emails configured for Instantel 1", 'Instantel 1')
        
        # Use custom emails if provided, otherwise use instrument emails
        if custom_emails:
            alert_emails = custom_emails
            warning_emails = custom_emails
            shutdown_emails = custom_emails
            print(f"Using custom emails for test: {custom_emails}")

        # 2. Calculate time range for checking alerts (30 minutes by default)
        # Readings are inserted in batches every 30 minutes with 5-minute intervals
        # So we need to check all readings in the last 30 minutes
        from datetime import datetime, timedelta, timezone
        utc_now = datetime.now(timezone.utc)
        
        # Calculate time window - check readings from the last time_window_minutes
        # Readings come in UTC format, so we work directly with UTC
        start_time = utc_now - timedelta(minutes=time_window_minutes)
        
        print(f"Checking Micromate readings from last {time_window_minutes} minutes")
        print(f"Time window: {start_time.strftime('%Y-%m-%dT%H:%M:%S')} UTC to {utc_now.strftime('%Y-%m-%dT%H:%M:%S')} UTC")

        # 3. Fetch data from Micromate API
        url = "https://imsite.dullesgeotechnical.com/api/micromate/readings"
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Failed to fetch Micromate data: {response.status_code} {response.text}")
            log_alert_event("ERROR", f"Failed to fetch Micromate data: {response.status_code} {response.text}", 'Instantel 1')
            result_summary['error'] = f"Failed to fetch Micromate data: {response.status_code}"
            return result_summary

        data = response.json()
        micromate_readings = data.get('MicromateReadings', [])
        
        if not micromate_readings:
            print("No Micromate data received")
            log_alert_event("ERROR", "No Micromate data received", 'Instantel 1')
            result_summary['error'] = "No Micromate data received"
            return result_summary

        print(f"Received {len(micromate_readings)} Micromate data points")

        # 4. Filter readings within the time window and check each one
        alerts_by_timestamp = {}
        
        if not micromate_readings:
            print("No Micromate readings available")
            log_alert_event("ERROR", "No Micromate readings available", 'Instantel 1')
            result_summary['error'] = "No Micromate readings available"
            return result_summary
        
        # Filter readings within the time window
        readings_in_window = []
        for reading in micromate_readings:
            try:
                timestamp_str = reading.get('Time', '')
                if not timestamp_str:
                    continue
                
                # Parse timestamp (format: "2025-11-18T22:00:06.055+00:00")
                reading_dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                
                # Check if reading is within the time window
                if start_time <= reading_dt <= utc_now:
                    readings_in_window.append(reading)
            except Exception as e:
                print(f"Failed to parse timestamp for reading: {e}")
                continue
        
        if not readings_in_window:
            print(f"No Micromate readings found in time window (last {time_window_minutes} minutes)")
            log_alert_event("INFO", f"No Micromate readings found in time window (last {time_window_minutes} minutes)", 'Instantel 1')
            return result_summary
        
        print(f"Found {len(readings_in_window)} readings in time window (last {time_window_minutes} minutes)")
        
        # Sort readings by Time (oldest first) to process them chronologically
        sorted_readings = sorted(readings_in_window, key=lambda x: x.get('Time', ''))
        
        print("=" * 80)
        print(f"PROCESSING {len(sorted_readings)} READINGS FROM TIME WINDOW:")
        print("=" * 80)
        
        # Check thresholds for each reading in the time window
        result_summary['total_readings_checked'] = len(sorted_readings)
        
        for reading in sorted_readings:
            timestamp_str = reading['Time']
            longitudinal = abs(float(reading.get('Longitudinal', 0)))
            transverse = abs(float(reading.get('Transverse', 0)))
            vertical = abs(float(reading.get('Vertical', 0)))
            
            print(f"\nChecking reading: {timestamp_str}")
            print(f"  Values - Longitudinal: {longitudinal:.6f}, Transverse: {transverse:.6f}, Vertical: {vertical:.6f}")
        
            # Check if we've already sent for this timestamp (unless force_resend is True)
            if not force_resend:
                try:
                    already_sent = supabase.table('sent_alerts') \
                        .select('id, timestamp, alert_type, created_at') \
                        .eq('instrument_id', 'Instantel 1') \
                        .eq('node_id', 24252) \
                        .eq('timestamp', timestamp_str) \
                        .execute()
                    if already_sent.data:
                        print(f"  ⏭️  SKIP: Alert already sent for this timestamp")
                        result_summary['readings_already_sent'] += 1
                        result_summary['skipped_timestamps'].append(timestamp_str)
                        continue
                    else:
                        print(f"  ✅ No alert sent yet - checking thresholds")
                except Exception as e:
                    print(f"  ⚠️  WARNING: Error checking sent_alerts table: {e}")
                    print("     Proceeding with alert check anyway (at all cost)")
                    log_alert_event("ERROR", f"Error checking sent_alerts table for {timestamp_str}: {e}", 'Instantel 1')
            else:
                print(f"  Force resend enabled - checking even if already sent")

            messages = []
            threshold_exceeded = False
            
            # Check shutdown thresholds (highest priority)
            for axis, value, axis_desc in [('Longitudinal', longitudinal, 'Longitudinal'), ('Transverse', transverse, 'Transverse'), ('Vertical', vertical, 'Vertical')]:
                if shutdown_value and value >= shutdown_value:
                    msg = f"<b>Shutdown threshold reached on {axis_desc} axis:</b> {value:.6f}"
                    messages.append(msg)
                    threshold_exceeded = True
                    print(f"  🔴 SHUTDOWN THRESHOLD EXCEEDED: {axis_desc} = {value:.6f} >= {shutdown_value}")
            
            # Check warning thresholds (medium priority)
            for axis, value, axis_desc in [('Longitudinal', longitudinal, 'Longitudinal'), ('Transverse', transverse, 'Transverse'), ('Vertical', vertical, 'Vertical')]:
                if warning_value and value >= warning_value:
                    msg = f"<b>Warning threshold reached on {axis_desc} axis:</b> {value:.6f}"
                    messages.append(msg)
                    threshold_exceeded = True
                    print(f"  🟡 WARNING THRESHOLD EXCEEDED: {axis_desc} = {value:.6f} >= {warning_value}")
            
            # Check alert thresholds (lowest priority)
            for axis, value, axis_desc in [('Longitudinal', longitudinal, 'Longitudinal'), ('Transverse', transverse, 'Transverse'), ('Vertical', vertical, 'Vertical')]:
                if alert_value and value >= alert_value:
                    msg = f"<b>Alert threshold reached on {axis_desc} axis:</b> {value:.6f}"
                    messages.append(msg)
                    threshold_exceeded = True
                    print(f"  🟠 ALERT THRESHOLD EXCEEDED: {axis_desc} = {value:.6f} >= {alert_value}")
            
            if threshold_exceeded:
                print(f"  ✅ THRESHOLDS EXCEEDED - {len(messages)} alert(s) detected - WILL SEND ALERT")
                result_summary['readings_with_alerts'] += 1
                result_summary['alert_timestamps'].append(timestamp_str)
                alerts_by_timestamp[timestamp_str] = {
                    'messages': messages,
                    'timestamp': timestamp_str,
                    'values': {
                        'Longitudinal': longitudinal, 
                        'Transverse': transverse, 
                        'Vertical': vertical
                    }
                }
            else:
                print(f"  ℹ️  No thresholds exceeded")
        
        print("=" * 80)
        print(f"SUMMARY: Checked {len(sorted_readings)} readings, {len(alerts_by_timestamp)} with alerts, {result_summary['readings_already_sent']} already sent")
        print("=" * 80)

        # 6. Send email if there are alerts - AT ALL COST
        if alerts_by_timestamp:
            print("=" * 80)
            print("PREPARING TO SEND ALERT EMAIL...")
            print("=" * 80)
            
            # Get project information and instrument details for micromate from database
            project_name = "Lincoln Lewis Fairfax"  # Default fallback
            instrument_details = []
            
            try:
                instrument_info = get_project_info('Instantel 1')
                if instrument_info:
                    instrument_details.append(instrument_info)
                    project_name = instrument_info['project_name']
            except Exception as e:
                print(f"⚠️  Warning: Error getting project info for Instantel 1: {e}")
                print("   Continuing with default project name")
                log_alert_event("ERROR", f"Error getting project info for Instantel 1: {e}", 'Instantel 1')
            
            try:
                body = _create_micromate_email_body(alerts_by_timestamp, project_name, instrument_details)
            except Exception as e:
                print(f"⚠️  Warning: Error creating email body: {e}")
                print("   Creating simple email body as fallback")
                # Create a simple fallback email body
                body = f"""
                <html>
                <body>
                    <h1>📊 INSTANTEL MICROMATE ALERT NOTIFICATION</h1>
                    <p><strong>Timestamp:</strong> {timestamp_str}</p>
                    <h2>Threshold Exceeded:</h2>
                    <ul>
                """
                for msg in messages:
                    body += f"<li>{msg}</li>"
                body += f"""
                    </ul>
                    <h2>Values:</h2>
                    <ul>
                        <li>Longitudinal: {longitudinal:.6f}</li>
                        <li>Transverse: {transverse:.6f}</li>
                        <li>Vertical: {vertical:.6f}</li>
                    </ul>
                </body>
                </html>
                """
                log_alert_event("ERROR", f"Error creating email body: {e}", 'Instantel 1')
            
            # Use the timestamp from the reading for the subject (no timezone conversion)
            formatted_time = timestamp_str
            subject = f"📊 Micromate Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            print(f"Recipients: {all_emails}")
            
            if all_emails:
                print("Attempting to send email...")
                try:
                    email_sent = send_email(",".join(all_emails), subject, body)
                    if email_sent:
                        result_summary['emails_sent'] = 1
                        print(f"✅ SUCCESS: Sent Micromate alert email for {len(alerts_by_timestamp)} timestamps with alerts to {len(all_emails)} recipients")
                        log_alert_event("EMAIL_SENT", f"Alert email sent successfully to {len(all_emails)} recipients for {len(alerts_by_timestamp)} timestamps", 'Instantel 1')
                        
                        # Record that we've sent for each timestamp (only if not force_resend, to avoid duplicates)
                        if not force_resend:
                            for timestamp, alert_data in alerts_by_timestamp.items():
                                try:
                                    # Determine the highest priority alert type
                                    alert_type = _determine_alert_type(alert_data['messages'])
                                    
                                    sent_alert_resp = supabase.table('sent_alerts').insert({
                                        'instrument_id': 'Instantel 1',
                                        'node_id': 24252,
                                        'timestamp': alert_data['timestamp'],
                                        'alert_type': alert_type
                                    }).execute()
                                    if sent_alert_resp.data:
                                        alert_id = sent_alert_resp.data[0]['id']
                                        print(f"✅ Alert recorded in sent_alerts table with ID {alert_id}")
                                        log_alert_event("ALERT_RECORDED", f"Alert recorded in sent_alerts table with ID {alert_id} for timestamp {timestamp}", 'Instantel 1', alert_id)
                                    else:
                                        print(f"⚠️  Warning: Failed to record alert in sent_alerts table")
                                        log_alert_event("ERROR", f"Failed to record alert in sent_alerts table for timestamp {timestamp}", 'Instantel 1')
                                except Exception as e:
                                    print(f"⚠️  Warning: Error recording alert in sent_alerts table: {e}")
                                    print("   Email was sent successfully, but alert record failed")
                                    log_alert_event("ERROR", f"Error recording alert in sent_alerts table: {e}", 'Instantel 1')
                        else:
                            print(f"Force resend mode: Skipping sent_alerts table insertion to avoid duplicates")
                            log_alert_event("INFO", f"Force resend mode: Sent {len(alerts_by_timestamp)} alerts without recording in sent_alerts table", 'Instantel 1')
                    else:
                        print(f"❌ CRITICAL ERROR: Failed to send alert email!")
                        print(f"   This is a critical failure - alert should have been sent")
                        log_alert_event("SEND EMAIL_FAILED", f"CRITICAL: Failed to send alert email for Instantel 1 - thresholds were exceeded!", 'Instantel 1')
                        result_summary['error'] = "CRITICAL: Failed to send alert email"
                except Exception as e:
                    print(f"❌ CRITICAL ERROR: Exception while sending email: {e}")
                    print(f"   This is a critical failure - alert should have been sent")
                    import traceback
                    traceback.print_exc()
                    log_alert_event("SEND EMAIL_FAILED", f"CRITICAL: Exception while sending alert email: {e}", 'Instantel 1')
                    result_summary['error'] = f"CRITICAL: Exception while sending email: {e}"
            else:
                print("❌ CRITICAL ERROR: No alert/warning/shutdown emails configured for Instantel 1")
                print("   Cannot send alert - no email addresses configured!")
                log_alert_event("ERROR", "CRITICAL: No alert/warning/shutdown emails configured for Instantel 1", 'Instantel 1')
                result_summary['error'] = "CRITICAL: No emails configured"
        else:
            print(f"No thresholds crossed for any reading in the time window (checked {len(sorted_readings)} readings).")
            log_alert_event("INFO", f"No thresholds crossed for any reading in the time window. Checked {len(sorted_readings)} readings.", 'Instantel 1')
        
        return result_summary
    except Exception as e:
        print(f"Error in check_and_send_micromate_alert: {e}")
        log_alert_event("ERROR", f"Error in check_and_send_micromate_alert: {e}", 'Instantel 1')
        result_summary['error'] = str(e)
        return result_summary

def check_and_send_instantel2_alert(custom_emails=None, time_window_minutes=10, force_resend=False):
    """Check Instantel 2 (UM16368) alerts and send emails if thresholds are exceeded
    
    Args:
        custom_emails (list, optional): Custom email addresses to use instead of instrument emails
        time_window_minutes (int, optional): Time window in minutes to check for alerts. Default is 10 minutes.
        force_resend (bool, optional): If True, will resend alerts even if they were already sent (for testing). Default is False.
    
    Returns:
        dict: Summary of the alert check including:
            - total_readings_checked: Number of readings checked
            - readings_with_alerts: Number of readings that exceeded thresholds
            - readings_already_sent: Number of readings that already had alerts sent
            - emails_sent: Number of emails sent
            - alert_timestamps: List of timestamps that had alerts
            - skipped_timestamps: List of timestamps that were skipped (already sent)
    """
    print("Checking Instantel 2 (UM16368) alerts...")
    result_summary = {
        'total_readings_checked': 0,
        'readings_with_alerts': 0,
        'readings_already_sent': 0,
        'emails_sent': 0,
        'alert_timestamps': [],
        'skipped_timestamps': []
    }
    try:
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'Instantel 2').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for Instantel 2")
            log_alert_event("ERROR", f"In check_and_send_instantel2_alert: No instrument found for Instantel 2", 'Instantel 2')
            result_summary['error'] = "No instrument found for Instantel 2"
            return result_summary

        # For Instantel 2, use single values for each axis
        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')
        
        alert_emails = instrument.get('alert_emails') or []
        warning_emails = instrument.get('warning_emails') or []
        shutdown_emails = instrument.get('shutdown_emails') or []
        
        # Use custom emails if provided, otherwise use instrument emails
        if custom_emails:
            alert_emails = custom_emails
            warning_emails = custom_emails
            shutdown_emails = custom_emails
            print(f"Using custom emails for test: {custom_emails}")

        # 2. Calculate time range for checking alerts
        # Account for instrument clock being 1 hour behind EST
        utc_now = datetime.now(timezone.utc)
        est_tz = pytz.timezone('US/Eastern')
        now_est = utc_now.astimezone(est_tz)
        # Subtract 1 hour to account for instrument clock being behind
        now_instrument_time = now_est - timedelta(hours=1)
        # Calculate start time based on time_window_minutes parameter
        start_instrument_time = now_instrument_time - timedelta(minutes=time_window_minutes)
        
        # Format time window description
        if time_window_minutes >= 1440:  # 1 day or more
            time_window_desc = f"{time_window_minutes / 1440:.1f} days"
        elif time_window_minutes >= 60:  # 1 hour or more
            time_window_desc = f"{time_window_minutes / 60:.1f} hours"
        else:
            time_window_desc = f"{time_window_minutes} minutes"
        
        print(f"Checking Instantel 2 (UM16368) data from {start_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')} to {now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')} EST (last {time_window_desc})")
        print(f"UTC time: {utc_now.strftime('%Y-%m-%dT%H:%M:%S')} UTC")
        print(f"EST time: {now_est.strftime('%Y-%m-%dT%H:%M:%S')} EST")
        print(f"Instrument time (1hr behind): {now_instrument_time.strftime('%Y-%m-%dT%H:%M:%S')} EST")

        # 3. Fetch data from UM16368 API
        url = "https://imsite.dullesgeotechnical.com/api/micromate/UM16368/readings"
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Failed to fetch UM16368 data: {response.status_code} {response.text}")
            log_alert_event("ERROR", f"Failed to fetch UM16368 data: {response.status_code} {response.text}", 'Instantel 2')
            result_summary['error'] = f"Failed to fetch UM16368 data: {response.status_code}"
            return result_summary

        data = response.json()
        um16368_readings = data.get('UM16368Readings', [])
        
        if not um16368_readings:
            print("No UM16368 data received")
            log_alert_event("ERROR", "No UM16368 data received", 'Instantel 2')
            result_summary['error'] = "No UM16368 data received"
            return result_summary

        print(f"Received {len(um16368_readings)} UM16368 data points")

        # 4. Check thresholds for readings within the time window
        # Filter readings within the specified time window (accounting for instrument time offset)
        alerts_by_timestamp = {}
        
        # Convert time window to UTC for comparison
        start_utc = start_instrument_time.astimezone(timezone.utc)
        now_utc = now_instrument_time.astimezone(timezone.utc)
        
        readings_in_window = []
        for reading in um16368_readings:
            try:
                timestamp_str = reading['Time']
                # Parse timestamp - format is "2025-10-27 14:27:45" (no timezone, assume EST)
                # Convert to datetime object
                dt_naive = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                # Assume it's in EST and convert to UTC
                dt_est = est_tz.localize(dt_naive)
                dt_utc = dt_est.astimezone(timezone.utc)
                
                # Check if reading is within the time window
                if start_utc <= dt_utc <= now_utc:
                    readings_in_window.append(reading)
                    
            except Exception as e:
                print(f"Failed to parse timestamp: {e}")
                continue
        
        if not readings_in_window:
            print(f"No UM16368 readings found in time window ({start_instrument_time.strftime('%Y-%m-%d %H:%M:%S')} to {now_instrument_time.strftime('%Y-%m-%d %H:%M:%S')} EST)")
            log_alert_event("INFO", f"No UM16368 readings found in time window ({start_instrument_time.strftime('%Y-%m-%d %H:%M:%S')} to {now_instrument_time.strftime('%Y-%m-%d %H:%M:%S')} EST)", 'Instantel 2')
            return result_summary
        
        print(f"Found {len(readings_in_window)} readings in time window")
        
        # Check thresholds for all readings in the time window
        result_summary['total_readings_checked'] = len(readings_in_window)
        
        for reading in readings_in_window:
            timestamp_str = reading['Time']
            # Map: Longitudinal_PPV -> X-axis, Transverse_PPV -> Y-axis, Vertical_PPV -> Z-axis
            x_axis = abs(float(reading.get('Longitudinal_PPV', 0)))
            y_axis = abs(float(reading.get('Transverse_PPV', 0)))
            z_axis = abs(float(reading.get('Vertical_PPV', 0)))
            
            # Check if we've already sent for this timestamp (unless force_resend is True)
            if not force_resend:
                already_sent = supabase.table('sent_alerts') \
                    .select('id') \
                    .eq('instrument_id', 'Instantel 2') \
                    .eq('node_id', 24252) \
                    .eq('timestamp', timestamp_str) \
                    .execute()
                if already_sent.data:
                    print(f"Instantel 2 alert already sent for timestamp {timestamp_str}, skipping.")
                    result_summary['readings_already_sent'] += 1
                    result_summary['skipped_timestamps'].append(timestamp_str)
                    continue
            else:
                print(f"Force resend enabled - checking timestamp {timestamp_str} even if already sent")

            messages = []
            
            # Check shutdown thresholds
            for axis, value, axis_desc in [('X', x_axis, 'X-axis (Longitudinal)'), ('Y', y_axis, 'Y-axis (Transverse)'), ('Z', z_axis, 'Z-axis (Vertical)')]:
                if shutdown_value and value >= shutdown_value:
                    messages.append(f"<b>Shutdown threshold reached on {axis_desc}:</b> {value:.6f}")
            
            # Check warning thresholds
            for axis, value, axis_desc in [('X', x_axis, 'X-axis (Longitudinal)'), ('Y', y_axis, 'Y-axis (Transverse)'), ('Z', z_axis, 'Z-axis (Vertical)')]:
                if warning_value and value >= warning_value:
                    messages.append(f"<b>Warning threshold reached on {axis_desc}:</b> {value:.6f}")
            
            # Check alert thresholds
            for axis, value, axis_desc in [('X', x_axis, 'X-axis (Longitudinal)'), ('Y', y_axis, 'Y-axis (Transverse)'), ('Z', z_axis, 'Z-axis (Vertical)')]:
                if alert_value and value >= alert_value:
                    messages.append(f"<b>Alert threshold reached on {axis_desc}:</b> {value:.6f}")

            if messages:
                result_summary['readings_with_alerts'] += 1
                result_summary['alert_timestamps'].append(timestamp_str)
                alerts_by_timestamp[timestamp_str] = {
                    'messages': messages,
                    'timestamp': timestamp_str,
                    'values': {
                        'X': x_axis, 
                        'Y': y_axis, 
                        'Z': z_axis
                    }
                }

        # 6. Send email if there are alerts
        if alerts_by_timestamp:
            # Get project information and instrument details for Instantel 2 from database
            project_name = "Lincoln Lewis Fairfax"  # Default fallback
            instrument_details = []
            
            try:
                instrument_info = get_project_info('Instantel 2')
                if instrument_info:
                    instrument_details.append(instrument_info)
                    project_name = instrument_info['project_name']
            except Exception as e:
                print(f"Error getting project info for Instantel 2: {e}")
                log_alert_event("ERROR", f"Error getting project info for Instantel 2: {e}", 'Instantel 2')
                
            body = _create_instantel2_email_body(alerts_by_timestamp, project_name, instrument_details)
            
            current_time = datetime.now(timezone.utc)
            est_tz = pytz.timezone('US/Eastern')
            current_time_est = current_time.astimezone(est_tz)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"📊 Instantel 2 (UM16368) Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                email_sent = send_email(",".join(all_emails), subject, body)
                if email_sent:
                    result_summary['emails_sent'] = 1
                    print(f"Sent Instantel 2 alert email for {len(alerts_by_timestamp)} timestamps with alerts to {len(all_emails)} recipients")
                    log_alert_event("EMAIL_SENT", f"Alert email sent successfully to {len(all_emails)} recipients for {len(alerts_by_timestamp)} timestamps", 'Instantel 2')
                    
                    # Record that we've sent for each timestamp (only if not force_resend, to avoid duplicates)
                    if not force_resend:
                        for timestamp, alert_data in alerts_by_timestamp.items():
                            # Determine the highest priority alert type
                            alert_type = _determine_alert_type(alert_data['messages'])
                            
                            sent_alert_resp = supabase.table('sent_alerts').insert({
                                'instrument_id': 'Instantel 2',
                                'node_id': 24252,  # Using same node_id as Instantel 1, adjust if needed
                                'timestamp': alert_data['timestamp'],
                                'alert_type': alert_type
                            }).execute()
                            if sent_alert_resp.data:
                                alert_id = sent_alert_resp.data[0]['id']
                                log_alert_event("ALERT_RECORDED", f"Alert recorded in sent_alerts table with ID {alert_id} for timestamp {timestamp}", 'Instantel 2', alert_id)
                    else:
                        print(f"Force resend mode: Skipping sent_alerts table insertion to avoid duplicates")
                        log_alert_event("INFO", f"Force resend mode: Sent {len(alerts_by_timestamp)} alerts without recording in sent_alerts table", 'Instantel 2')
                else:
                    log_alert_event("SEND EMAIL_FAILED", f"Failed to send alert email for Instantel 2", 'Instantel 2')
            else:
                print("No alert/warning/shutdown emails configured for Instantel 2")
                log_alert_event("ERROR", "No alert/warning/shutdown emails configured for Instantel 2", 'Instantel 2')
        else:
            print("No thresholds crossed for any reading in the time window for Instantel 2.")
            log_alert_event("INFO", f"No thresholds crossed for any reading in the time window. Checked {len(readings_in_window)} readings.", 'Instantel 2')
        
        return result_summary
    except Exception as e:
        print(f"Error in check_and_send_instantel2_alert: {e}")
        log_alert_event("ERROR", f"Error in check_and_send_instantel2_alert: {e}", 'Instantel 2')
        result_summary['error'] = str(e)
        return result_summary

def _create_instantel2_email_body(alerts_by_timestamp, project_name, instrument_details):
    """Create HTML email body for Instantel 2 (UM16368) alerts"""
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
                <h1>📊 INSTANTEL 2 (UM16368) ALERT NOTIFICATION</h1>
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
                    The following Instantel 2 (UM16368) thresholds have been exceeded in real-time:
                </p>
    """
    
    # Add alerts for each timestamp
    for timestamp, alert_data in alerts_by_timestamp.items():
        # Format timestamp to EST
        try:
            dt_naive = datetime.strptime(alert_data['timestamp'], '%Y-%m-%d %H:%M:%S')
            est_tz = pytz.timezone('US/Eastern')
            dt_est = est_tz.localize(dt_naive)
            formatted_time = dt_est.strftime('%Y-%m-%d %I:%M:%S %p EST')
        except Exception as e:
            print(f"Failed to parse/convert timestamp: {alert_data['timestamp']}, error: {e}")
            formatted_time = alert_data['timestamp']
        
        body += f"""
                <div class="alert-section">
                    <h3>📊 Real-time Alert - Instantel 2 (UM16368)</h3>
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
                                               <td>X-axis (Longitudinal)</td>
                                               <td>{alert_data['values']['X']:.6f}</td>
                                           </tr>
                                           <tr>
                                               <td>Y-axis (Transverse)</td>
                                               <td>{alert_data['values']['Y']:.6f}</td>
                                           </tr>
                                           <tr>
                                               <td>Z-axis (Vertical)</td>
                                               <td>{alert_data['values']['Z']:.6f}</td>
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
                        Please review the Instantel 2 (UM16368) data and take appropriate action if necessary. 
                        Values shown are the actual readings that exceeded thresholds.
                        <br><br>
                        <strong>Project ID:</strong> 24252<br>
                        <strong>Instrument:</strong> Instantel 2 (UM16368)
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

def get_um16368_readings():
    """
    Parse CSV files from /root/root/ftp-server/Dulles Test/UM16368/CSV directory
    and extract readings dynamically by finding the header structure:
    - Only processes files ending with IDFH.csv (excludes IDFW.csv files)
    - Search for "PPV" in any cell
    - When found, check the row 1 row after PPV (next row after PPV row)
    - That row should have "TIME" in the first column - this is the header row
    - The row with PPV contains column names or format indicators
    - Rows below the header row contain the actual readings
    
    Returns a list of readings with key-value pairs for each timestamp.
    """
    csv_directory = "/root/root/ftp-server/Dulles Test/UM16368/CSV"
    
    if not os.path.exists(csv_directory):
        print(f"CSV directory not found: {csv_directory}")
        return {
            'readings': [],
            'summary': {
                'total_readings': 0,
                'files_processed': 0,
                'files_found': 0,
                'errors_count': 1
            },
            'processed_files': [],
            'errors': [f'CSV directory not found: {csv_directory}']
        }
    
    # Find all IDFH.csv files in the directory (only process IDFH files, not IDFW)
    pattern = os.path.join(csv_directory, "*IDFH.csv")
    csv_files = glob.glob(pattern)
    
    if not csv_files:
        print(f"No IDFH.csv files found in directory: {csv_directory}")
        return {
            'readings': [],
            'summary': {
                'total_readings': 0,
                'files_processed': 0,
                'files_found': 0,
                'errors_count': 1
            },
            'processed_files': [],
            'errors': [f'No IDFH.csv files found in directory: {csv_directory}']
        }
    
    # Sort files by name
    csv_files.sort()
    
    all_readings = []
    processed_files = []
    errors = []
    
    for file_path in csv_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                reader = csv.reader(file)
                rows = list(reader)
                
                if len(rows) < 3:
                    errors.append(f'File {os.path.basename(file_path)} has less than 3 rows')
                    continue
                
                # Dynamically find the header structure by searching for PPV
                ppv_row_idx = None
                header_row_idx = None
                format_row_idx = None
                column_row_idx = None
                
                # Search for PPV in any cell
                for row_idx, row in enumerate(rows):
                    for col_idx, cell in enumerate(row):
                        if cell and cell.strip().upper() == "PPV":
                            ppv_row_idx = row_idx
                            break
                    if ppv_row_idx is not None:
                        break
                
                if ppv_row_idx is None:
                    errors.append(f'File {os.path.basename(file_path)}: PPV not found in file')
                    continue
                
                # Check if row 1 row after PPV has TIME in first column
                time_header_row_idx = ppv_row_idx + 1
                if time_header_row_idx < len(rows):
                    first_col = rows[time_header_row_idx][0].strip().upper() if rows[time_header_row_idx] and len(rows[time_header_row_idx]) > 0 else ""
                    if first_col == "TIME":
                        header_row_idx = time_header_row_idx
                        format_row_idx = ppv_row_idx
                        column_row_idx = ppv_row_idx
                    else:
                        errors.append(f'File {os.path.basename(file_path)}: TIME not found in first column 1 row after PPV (row {time_header_row_idx + 1})')
                        continue
                else:
                    errors.append(f'File {os.path.basename(file_path)}: Not enough rows after PPV (found at row {ppv_row_idx + 1})')
                    continue
                
                # Get the header rows based on the actual structure:
                # Row 2 before PPV (column_row): Contains column names (Tran, Vert, Long, Geophone)
                # Row with PPV (format_row): Contains formats (PPV, Freq, PVS, etc.)
                # Row 1 after PPV (header_row): Contains TIME in first column and units (in/s, Hz, etc.)
                format_row = rows[format_row_idx] if format_row_idx < len(rows) else []
                header_row = rows[header_row_idx] if header_row_idx < len(rows) else []
                column_row = rows[format_row_idx - 2] if format_row_idx >= 2 else []
                
                # Find Time column index (should be first column based on logic, but search to be sure)
                time_index = 0  # Default to first column
                for i, col_name in enumerate(header_row):
                    col_name_upper = col_name.strip().upper() if col_name else ""
                    if col_name_upper == "TIME":
                        time_index = i
                        break
                
                # Find column indices by matching pattern
                # Structure: column_row (2 rows before PPV) has column names, format_row has formats, header_row has units
                # We need to match: column name in column_row + format "PPV" in format_row (same column index)
                # Column names might span multiple columns, so we check if the column name appears in the column_row
                # and then verify that the same column in format_row has "PPV"
                tran_index = None
                vert_index = None
                long_index = None
                geophone_index = None
                mic_lmax_index = None
                mic_l10_index = None
                mic_l90_index = None
                
                # Find the maximum column count across all rows
                max_cols = max(len(format_row), len(header_row), len(column_row))
                
                for i in range(max_cols):
                    # Get values from each row (safe access)
                    # Skip TIME column when matching
                    if i == time_index:
                        continue
                    
                    format_val = format_row[i].strip().upper() if i < len(format_row) and format_row[i] else ""
                    header_col = header_row[i].strip().upper() if i < len(header_row) and header_row[i] else ""
                    col_name_val = column_row[i].strip().upper() if i < len(column_row) and column_row[i] else ""
                    
                    # Check if column name appears in column_row (2 rows before PPV)
                    # For Tran PPV: column_row should have "TRAN" and format_row should have "PPV" in same column, and header_row should have "in/s"
                    # We need to match: column_row[i] = "TRAN", format_row[i] = "PPV", header_row[i] = "in/s"
                    if col_name_val == "TRAN" and format_val == "PPV":
                        # Verify unit is "in/s" in header_row
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "in/s" in unit_val and tran_index is None:
                            tran_index = i
                    # Also check if "TRAN" appears but format is in next column (spanning case)
                    elif col_name_val == "TRAN" and tran_index is None:
                        # Check if format_row has PPV in this column or next column
                        if format_val == "PPV":
                            unit_val = header_col.strip().lower() if header_col else ""
                            if "in/s" in unit_val:
                                tran_index = i
                        elif i + 1 < len(format_row) and format_row[i + 1].strip().upper() == "PPV":
                            unit_val = header_row[i + 1].strip().lower() if i + 1 < len(header_row) and header_row[i + 1] else ""
                            if "in/s" in unit_val:
                                tran_index = i + 1
                    
                    # For Vert PPV: column_row should have "VERT" and format_row should have "PPV" in same column
                    if col_name_val == "VERT" and format_val == "PPV":
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "in/s" in unit_val and vert_index is None:
                            vert_index = i
                    elif col_name_val == "VERT" and vert_index is None:
                        if format_val == "PPV":
                            unit_val = header_col.strip().lower() if header_col else ""
                            if "in/s" in unit_val:
                                vert_index = i
                        elif i + 1 < len(format_row) and format_row[i + 1].strip().upper() == "PPV":
                            unit_val = header_row[i + 1].strip().lower() if i + 1 < len(header_row) and header_row[i + 1] else ""
                            if "in/s" in unit_val:
                                vert_index = i + 1
                    
                    # For Long PPV: column_row should have "LONG" and format_row should have "PPV" in same column
                    if col_name_val == "LONG" and format_val == "PPV":
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "in/s" in unit_val and long_index is None:
                            long_index = i
                    elif col_name_val == "LONG" and long_index is None:
                        if format_val == "PPV":
                            unit_val = header_col.strip().lower() if header_col else ""
                            if "in/s" in unit_val:
                                long_index = i
                        elif i + 1 < len(format_row) and format_row[i + 1].strip().upper() == "PPV":
                            unit_val = header_row[i + 1].strip().lower() if i + 1 < len(header_row) and header_row[i + 1] else ""
                            if "in/s" in unit_val:
                                long_index = i + 1
                    
                    # For Geophone: column_row should have "GEOPHONE" and format_row should have "PVS", header_row should have "in/s"
                    if col_name_val == "GEOPHONE" and geophone_index is None:
                        # Check if format is PVS in the same column
                        if format_val == "PVS":
                            unit_val = header_col.strip().lower() if header_col else ""
                            if "in/s" in unit_val:
                                geophone_index = i
                        # Or if format_row doesn't have PVS, just use the column if it exists and has in/s
                        else:
                            unit_val = header_col.strip().lower() if header_col else ""
                            if "in/s" in unit_val:
                                geophone_index = i
                    
                    # For Mic LMax: column_row should have "MIC" and format_row should have "LMAX", header_row should have "db(A)"
                    if col_name_val == "MIC" and format_val == "LMAX":
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "db(a)" in unit_val and mic_lmax_index is None:
                            mic_lmax_index = i
                    elif col_name_val == "MIC" and mic_lmax_index is None:
                        if format_val == "LMAX":
                            unit_val = header_col.strip().lower() if header_col else ""
                            if "db(a)" in unit_val:
                                mic_lmax_index = i
                        elif i + 1 < len(format_row) and format_row[i + 1].strip().upper() == "LMAX":
                            unit_val = header_row[i + 1].strip().lower() if i + 1 < len(header_row) and header_row[i + 1] else ""
                            if "db(a)" in unit_val:
                                mic_lmax_index = i + 1
                    
                    # For Mic L10: column_row should have "MIC" and format_row should have "L10", header_row should have "db(A)"
                    if col_name_val == "MIC" and format_val == "L10":
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "db(a)" in unit_val and mic_l10_index is None:
                            mic_l10_index = i
                    elif col_name_val == "MIC" and mic_l10_index is None:
                        if format_val == "L10":
                            unit_val = header_col.strip().lower() if header_col else ""
                            if "db(a)" in unit_val:
                                mic_l10_index = i
                        elif i + 1 < len(format_row) and format_row[i + 1].strip().upper() == "L10":
                            unit_val = header_row[i + 1].strip().lower() if i + 1 < len(header_row) and header_row[i + 1] else ""
                            if "db(a)" in unit_val:
                                mic_l10_index = i + 1
                    
                    # For Mic L90: column_row should have "MIC" and format_row should have "L90", header_row should have "db(A)"
                    if col_name_val == "MIC" and format_val == "L90":
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "db(a)" in unit_val and mic_l90_index is None:
                            mic_l90_index = i
                    elif col_name_val == "MIC" and mic_l90_index is None:
                        if format_val == "L90":
                            unit_val = header_col.strip().lower() if header_col else ""
                            if "db(a)" in unit_val:
                                mic_l90_index = i
                        elif i + 1 < len(format_row) and format_row[i + 1].strip().upper() == "L90":
                            unit_val = header_row[i + 1].strip().lower() if i + 1 < len(header_row) and header_row[i + 1] else ""
                            if "db(a)" in unit_val:
                                mic_l90_index = i + 1
                    
                    # Also check if column names appear in header_row (fallback, though less likely based on structure)
                    if header_col == "TRAN" and format_val == "PPV" and tran_index is None:
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "in/s" in unit_val or not unit_val:  # Allow if unit not found as fallback
                            tran_index = i
                    elif header_col == "VERT" and format_val == "PPV" and vert_index is None:
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "in/s" in unit_val or not unit_val:
                            vert_index = i
                    elif header_col == "LONG" and format_val == "PPV" and long_index is None:
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "in/s" in unit_val or not unit_val:
                            long_index = i
                    elif header_col == "GEOPHONE" and geophone_index is None:
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "in/s" in unit_val or not unit_val:
                            geophone_index = i
                    elif header_col == "MIC" and format_val == "LMAX" and mic_lmax_index is None:
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "db(a)" in unit_val or not unit_val:
                            mic_lmax_index = i
                    elif header_col == "MIC" and format_val == "L10" and mic_l10_index is None:
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "db(a)" in unit_val or not unit_val:
                            mic_l10_index = i
                    elif header_col == "MIC" and format_val == "L90" and mic_l90_index is None:
                        unit_val = header_col.strip().lower() if header_col else ""
                        if "db(a)" in unit_val or not unit_val:
                            mic_l90_index = i
                
                # Fallback: If we still haven't found columns, try to match PPV with "in/s" units directly
                # This handles cases where column names might not be found but format and units match
                if tran_index is None or vert_index is None or long_index is None:
                    for i in range(max_cols):
                        if i == time_index:
                            continue
                        format_val = format_row[i].strip().upper() if i < len(format_row) and format_row[i] else ""
                        unit_val = header_row[i].strip().lower() if i < len(header_row) and header_row[i] else ""
                        unit_val_lower = unit_val.lower() if unit_val else ""
                        
                        if format_val == "PPV" and "in/s" in unit_val_lower:
                            # Assign to first available PPV column
                            if tran_index is None:
                                tran_index = i
                            elif vert_index is None:
                                vert_index = i
                            elif long_index is None:
                                long_index = i
                
                # Fallback for Geophone: Find PVS with "in/s"
                if geophone_index is None:
                    for i in range(max_cols):
                        if i == time_index:
                            continue
                        format_val = format_row[i].strip().upper() if i < len(format_row) and format_row[i] else ""
                        unit_val = header_row[i].strip().lower() if i < len(header_row) and header_row[i] else ""
                        if format_val == "PVS" and "in/s" in unit_val:
                            geophone_index = i
                
                # Fallback for Mic: Find LMAX, L10, L90 with "db(A)"
                if mic_lmax_index is None or mic_l10_index is None or mic_l90_index is None:
                    for i in range(max_cols):
                        if i == time_index:
                            continue
                        format_val = format_row[i].strip().upper() if i < len(format_row) and format_row[i] else ""
                        unit_val = header_row[i].strip().lower() if i < len(header_row) and header_row[i] else ""
                        if "db(a)" in unit_val:
                            if format_val == "LMAX" and mic_lmax_index is None:
                                mic_lmax_index = i
                            elif format_val == "L10" and mic_l10_index is None:
                                mic_l10_index = i
                            elif format_val == "L90" and mic_l90_index is None:
                                mic_l90_index = i
                
                # Debug: Print row contents and found indices
                print(f"File {os.path.basename(file_path)}: PPV at row {ppv_row_idx + 1}, TIME at row {header_row_idx + 1}")
                print(f"  Column row (row {format_row_idx - 1}): {column_row[:10] if len(column_row) > 10 else column_row}")
                print(f"  Format row (row {format_row_idx}): {format_row[:10] if len(format_row) > 10 else format_row}")
                print(f"  Header row (row {header_row_idx}): {header_row[:10] if len(header_row) > 10 else header_row}")
                print(f"  Found indices - Tran: {tran_index}, Vert: {vert_index}, Long: {long_index}, Geophone: {geophone_index}, Mic LMax: {mic_lmax_index}, Mic L10: {mic_l10_index}, Mic L90: {mic_l90_index}")
                
                # Process readings from row after header onwards
                file_readings = []
                rows_processed = 0
                rows_skipped_empty = 0
                rows_skipped_no_time = 0
                rows_skipped_no_data = 0
                
                for row_idx in range(header_row_idx + 1, len(rows)):
                    row = rows[row_idx]
                    rows_processed += 1
                    
                    # Skip empty rows
                    if not row or (len(row) == 1 and not row[0].strip()):
                        rows_skipped_empty += 1
                        continue
                    
                    # Extract time
                    time_value = row[time_index].strip() if time_index < len(row) else ""
                    if not time_value:
                        rows_skipped_no_time += 1
                        continue
                    
                    # Build reading object with all values at the same level
                    reading = {
                        'Time': time_value,
                        'source_file': os.path.basename(file_path)
                    }
                    
                    # Add Tran reading if available (only if pattern matched)
                    if tran_index is not None and tran_index < len(row):
                        tran_value = row[tran_index].strip()
                        if tran_value:
                            try:
                                reading['Transverse_PPV'] = float(tran_value)
                            except ValueError:
                                reading['Transverse_PPV'] = tran_value
                    
                    # Add Vert reading if available (only if pattern matched)
                    if vert_index is not None and vert_index < len(row):
                        vert_value = row[vert_index].strip()
                        if vert_value:
                            try:
                                reading['Vertical_PPV'] = float(vert_value)
                            except ValueError:
                                reading['Vertical_PPV'] = vert_value
                    
                    # Add Long reading if available (only if pattern matched)
                    if long_index is not None and long_index < len(row):
                        long_value = row[long_index].strip()
                        if long_value:
                            try:
                                reading['Longitudinal_PPV'] = float(long_value)
                            except ValueError:
                                reading['Longitudinal_PPV'] = long_value
                    
                    # Add Geophone reading if available (keep format as found)
                    if geophone_index is not None and geophone_index < len(row):
                        geophone_value = row[geophone_index].strip()
                        if geophone_value:
                            # Get the format from format_row for Geophone
                            geophone_format = format_row[geophone_index].strip() if geophone_index < len(format_row) else "PVS"
                            try:
                                reading[f'Geophone_{geophone_format}'] = float(geophone_value)
                            except ValueError:
                                reading[f'Geophone_{geophone_format}'] = geophone_value
                    
                    # Add Mic LMax reading if available
                    if mic_lmax_index is not None and mic_lmax_index < len(row):
                        mic_lmax_value = row[mic_lmax_index].strip()
                        if mic_lmax_value:
                            try:
                                reading['Mic_LMax_db(A)'] = float(mic_lmax_value)
                            except ValueError:
                                reading['Mic_LMax_db(A)'] = mic_lmax_value
                    
                    # Add Mic L10 reading if available
                    if mic_l10_index is not None and mic_l10_index < len(row):
                        mic_l10_value = row[mic_l10_index].strip()
                        if mic_l10_value:
                            try:
                                reading['Mic_L10_db(A)'] = float(mic_l10_value)
                            except ValueError:
                                reading['Mic_L10_db(A)'] = mic_l10_value
                    
                    # Add Mic L90 reading if available
                    if mic_l90_index is not None and mic_l90_index < len(row):
                        mic_l90_value = row[mic_l90_index].strip()
                        if mic_l90_value:
                            try:
                                reading['Mic_L90_db(A)'] = float(mic_l90_value)
                            except ValueError:
                                reading['Mic_L90_db(A)'] = mic_l90_value
                    
                    # Only add reading if it has at least one reading value (excluding Time and source_file)
                    if len(reading) > 2:  # More than just Time and source_file
                        file_readings.append(reading)
                    else:
                        rows_skipped_no_data += 1
                
                # Debug: Print processing stats
                print(f"  Processed {rows_processed} rows: {len(file_readings)} readings, {rows_skipped_empty} empty, {rows_skipped_no_time} no time, {rows_skipped_no_data} no data")
                
                all_readings.extend(file_readings)
                processed_files.append({
                    'file': os.path.basename(file_path),
                    'readings_count': len(file_readings)
                })
                
        except Exception as e:
            error_msg = f'Error processing {os.path.basename(file_path)}: {str(e)}'
            errors.append(error_msg)
            print(error_msg)
            continue
    
    # Sort all readings by Time
    try:
        all_readings.sort(key=lambda x: x.get('Time', ''))
    except Exception as e:
        print(f"Error sorting readings: {e}")
    
    print(f"Processed {len(processed_files)} files, extracted {len(all_readings)} readings")
    if errors:
        print(f"Errors encountered: {errors}")
    
    return {
        'readings': all_readings,
        'summary': {
            'total_readings': len(all_readings),
            'files_processed': len(processed_files),
            'files_found': len(csv_files),
            'errors_count': len(errors)
        },
        'processed_files': processed_files,
        'errors': errors if errors else []
    }


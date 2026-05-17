#!/usr/bin/env python3
"""
Function to send missed Rock Seismograph alert emails by fetching historical data.
This will check past data and send emails for any missed threshold violations.
"""

import os
import sys
import requests
from datetime import datetime, timedelta, timezone
import pytz

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from services.rock_seismograph_service import _create_rock_seismograph_email_body, get_project_info, log_alert_event
from services.email_service import send_email
from supabase import create_client
from config import Config

def send_missed_rock_seismograph_alerts(instrument_id='ROCKSMG-1', days_back=30, custom_emails=None):
    """
    Fetch historical data and send emails for missed Rock Seismograph alerts.
    
    Args:
        instrument_id (str): The instrument ID to check (ROCKSMG-1 or ROCKSMG-2)
        days_back (int): How many days back to check for missed alerts
        custom_emails (list): Optional list of custom email addresses to send to instead of configured ones
    
    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    # Ensure days_back is an integer
    try:
        days_back = int(days_back)
    except (ValueError, TypeError):
        print(f"⚠️ Invalid days_back value: {days_back}, using default 30")
        days_back = 30
    
    print(f"🔍 Checking missed alerts for {instrument_id} (last {days_back} days)...")
    
    try:
        # Initialize Supabase client
        supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        
        # Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
        if not instrument_resp.data:
            error_msg = f"No instrument found for {instrument_id}"
            print(f"❌ {error_msg}")
            return False, error_msg
            
        instrument = instrument_resp.data[0]
        
        # Get thresholds and emails
        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')
        
        alert_emails = instrument.get('alert_emails') or []
        warning_emails = instrument.get('warning_emails') or []
        shutdown_emails = instrument.get('shutdown_emails') or []
        
        # Use custom emails if provided, otherwise use configured emails
        if custom_emails:
            # Ensure custom_emails is a flat list of strings
            if isinstance(custom_emails, list):
                # Flatten the list in case it contains nested lists
                flat_emails = []
                for email in custom_emails:
                    if isinstance(email, list):
                        flat_emails.extend(email)
                    elif isinstance(email, str):
                        flat_emails.append(email)
                all_emails = set(flat_emails)
            else:
                all_emails = set([str(custom_emails)])
            print(f"📬 Using custom emails: {list(all_emails)}")
        else:
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if not all_emails:
                error_msg = f"No emails configured for {instrument_id} and no custom emails provided"
                print(f"❌ {error_msg}")
                return False, error_msg
        
        # Get device ID
        device_id = instrument.get('syscom_device_id')
        if not device_id:
            error_msg = f"No syscom_device_id found for {instrument_id}. Please configure the device ID in the instruments table."
            print(f"❌ {error_msg}")
            return False, error_msg
        
        print(f"📡 Using device_id: {device_id}")
        print(f"📬 Recipients: {list(all_emails)}")
        
        # Calculate time range
        est = pytz.timezone('US/Eastern')
        now_est = datetime.now(est)
        start_date = now_est - timedelta(days=days_back)
        
        # Format dates for API
        start_time = start_date.strftime('%Y-%m-%dT%H:%M:%S')
        end_time = now_est.strftime('%Y-%m-%dT%H:%M:%S')
        
        print(f"📅 Checking data from {start_time} to {end_time} EST")
        
        # Fetch historical data from Syscom API
        api_key = os.environ.get('SYSCOM_API_KEY')
        if not api_key:
            error_msg = "SYSCOM_API_KEY not set in environment variables"
            print(f"❌ {error_msg}")
            return False, error_msg
        
        url = f"https://scs.syscom-instruments.com/public-api/v1/records/background/{device_id}/data?start={start_time}&end={end_time}"
        headers = {"x-scs-api-key": api_key}
        
        print(f"🌐 Fetching data from Syscom API...")
        print(f"   URL: {url}")
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            error_msg = f"Failed to fetch data from Syscom API: HTTP {response.status_code} - {response.text[:200]}"
            print(f"❌ {error_msg}")
            return False, error_msg
        
        data = response.json()
        background_data = data.get('data', [])
        
        if not background_data:
            print(f"📊 No historical data found for {instrument_id} in the last {days_back} days")
            return True, None
        
        print(f"📊 Received {len(background_data)} data points")
        
        # Group data by hour and find highest values for each axis
        hourly_data = {}
        for entry in background_data:
            timestamp = entry[0]
            x_value = float(entry[1])
            y_value = float(entry[2])
            z_value = float(entry[3])
            
            # Extract hour key (YYYY-MM-DD-HH)
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                dt_est = dt.astimezone(est)
                hour_key = dt_est.strftime('%Y-%m-%d-%H')
            except Exception as e:
                print(f"⚠️ Failed to parse timestamp {timestamp}: {e}")
                continue
            
            if hour_key not in hourly_data:
                hourly_data[hour_key] = {
                    'max_x': abs(x_value),
                    'max_y': abs(y_value),
                    'max_z': abs(z_value),
                    'timestamp': timestamp
                }
            else:
                hourly_data[hour_key]['max_x'] = max(hourly_data[hour_key]['max_x'], abs(x_value))
                hourly_data[hour_key]['max_y'] = max(hourly_data[hour_key]['max_y'], abs(y_value))
                hourly_data[hour_key]['max_z'] = max(hourly_data[hour_key]['max_z'], abs(z_value))
        
        print(f"📊 Grouped into {len(hourly_data)} hours")
        
        # Check thresholds for each hour and find missed alerts
        missed_alerts = []
        emails_sent = 0
        
        for hour_key, hour_data in hourly_data.items():
            max_x = hour_data['max_x']
            max_y = hour_data['max_y']
            max_z = hour_data['max_z']
            timestamp = hour_data['timestamp']
            
            # Check if we've already sent for this hour (any alert type)
            already_sent = supabase.table('sent_alerts') \
                .select('id') \
                .eq('instrument_id', instrument_id) \
                .eq('node_id', device_id) \
                .eq('timestamp', timestamp) \
                .execute()
            
            if already_sent.data:
                continue  # Skip if already sent for this hour
            
            # Check shutdown thresholds - send separate email for each
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if shutdown_value and value >= shutdown_value:
                    missed_alerts.append({
                        'hour_key': hour_key,
                        'timestamp': timestamp,
                        'alert_type': 'shutdown',
                        'axis': axis,
                        'value': value,
                        'max_values': {'X': max_x, 'Y': max_y, 'Z': max_z},
                        'message': f"<b>Shutdown threshold reached on {axis}-axis:</b> {value:.6f}"
                    })
            
            # Check warning thresholds - send separate email for each
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if warning_value and value >= warning_value:
                    missed_alerts.append({
                        'hour_key': hour_key,
                        'timestamp': timestamp,
                        'alert_type': 'warning',
                        'axis': axis,
                        'value': value,
                        'max_values': {'X': max_x, 'Y': max_y, 'Z': max_z},
                        'message': f"<b>Warning threshold reached on {axis}-axis:</b> {value:.6f}"
                    })
            
            # Check alert thresholds - send separate email for each
            for axis, value in [('X', max_x), ('Y', max_y), ('Z', max_z)]:
                if alert_value and value >= alert_value:
                    missed_alerts.append({
                        'hour_key': hour_key,
                        'timestamp': timestamp,
                        'alert_type': 'alert',
                        'axis': axis,
                        'value': value,
                        'max_values': {'X': max_x, 'Y': max_y, 'Z': max_z},
                        'message': f"<b>Alert threshold reached on {axis}-axis:</b> {value:.6f}"
                    })
        
        print(f"🚨 Found {len(missed_alerts)} individual missed alerts")
        
        if not missed_alerts:
            print("✅ No missed alerts found!")
            return True, None
        
        # Get project info
        instrument_info = get_project_info(instrument_id)
        if not instrument_info:
            error_msg = f"Could not get project info for {instrument_id}"
            print(f"❌ {error_msg}")
            return False, error_msg
        
        instrument_details = [instrument_info]
        project_name = instrument_info['project_name']
        seismograph_name = "Rock Seismograph"
        
        # Track which hours we've already recorded in sent_alerts
        recorded_hours = set()
        
        # Send separate email for each individual missed alert
        for alert in missed_alerts:
            hour_key = alert['hour_key']
            alert_type = alert['alert_type']
            axis = alert['axis']
            value = alert['value']
            timestamp = alert['timestamp']
            
            print(f"📧 Sending {alert_type} email for {hour_key} - {axis}-axis: {value:.6f}")
            
            # Create email body for this specific alert
            # The email function expects 'values' not 'max_values'
            single_alert_data = {
                hour_key: {
                    'messages': [alert['message']],
                    'timestamp': timestamp,
                    'values': alert['max_values']  # Use 'values' key as expected by email function
                }
            }
            
            body = _create_rock_seismograph_email_body(
                single_alert_data, 
                seismograph_name, 
                project_name, 
                instrument_id, 
                instrument_details
            )
            
            # Create subject with specific alert type and hour
            try:
                dt_utc = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                dt_est = dt_utc.astimezone(est)
                formatted_time = dt_est.strftime('%m-%d-%Y %I:%M %p EST')
            except:
                formatted_time = hour_key.replace('-', ' ')
            
            subject = f"🌊 {alert_type.upper()} - {seismograph_name} {axis}-axis - {formatted_time}"
            
            # Send email
            email_sent = send_email(",".join(all_emails), subject, body)
            
            if email_sent:
                print(f"✅ {alert_type.upper()} email sent for {hour_key} - {axis}-axis")
                emails_sent += 1
                
                # Record that we've sent for this hour (only insert once per hour)
                if hour_key not in recorded_hours:
                    try:
                        sent_alert_resp = supabase.table('sent_alerts').insert({
                            'instrument_id': instrument_id,
                            'node_id': device_id,
                            'timestamp': timestamp,
                            'alert_type': 'any'  # Use generic type to avoid duplicates
                        }).execute()
                        
                        if sent_alert_resp.data:
                            alert_id = sent_alert_resp.data[0]['id']
                            recorded_hours.add(hour_key)
                    except Exception as insert_error:
                        # If duplicate key error, just log that it was already recorded
                        if "duplicate key" in str(insert_error).lower() or "23505" in str(insert_error):
                            recorded_hours.add(hour_key)
                        else:
                            print(f"⚠️ Failed to record alert in database: {insert_error}")
                
            else:
                print(f"❌ Failed to send {alert_type} email for {hour_key} - {axis}-axis")
        
        print(f"📊 Summary: {emails_sent}/{len(missed_alerts)} emails sent successfully")
        
        return True, None
        
    except Exception as e:
        import traceback
        error_msg = f"Error processing missed alerts for {instrument_id}: {str(e)}"
        error_trace = traceback.format_exc()
        print(f"❌ {error_msg}")
        print(f"Traceback: {error_trace}")
        return False, error_msg

def main():
    """Main function to run the missed alerts check"""
    print("🌊 Rock Seismograph Missed Alerts Sender")
    print("=" * 50)
    
    # Check both instruments
    instruments = ['ROCKSMG-1', 'ROCKSMG-2']
    
    for instrument_id in instruments:
        print(f"\n🎯 Checking {instrument_id}...")
        result = send_missed_rock_seismograph_alerts(instrument_id, days_back=30)
        # Handle both tuple and bool return formats
        if isinstance(result, tuple):
            success, error_message = result
            if error_message:
                print(f"   Error: {error_message}")
        else:
            success = result
        
        if success:
            print(f"✅ {instrument_id} check completed")
        else:
            print(f"❌ {instrument_id} check failed")
        print("-" * 30)
    
    print("\n🏁 Missed alerts check completed!")


if __name__ == "__main__":
    main()

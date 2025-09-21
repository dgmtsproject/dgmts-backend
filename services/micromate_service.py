import os
import requests
from datetime import datetime, timedelta, timezone
import pytz
from supabase import create_client, Client
from config import Config
from .email_service import send_email

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

def check_and_send_micromate_alert():
    """Check Instantel Micromate alerts and send emails if thresholds are exceeded"""
    print("Checking Instantel Micromate alerts...")
    try:
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'INSTANTEL-1').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            print("No instrument found for INSTANTEL-1")
            return

        # For micromate, use single values for each axis
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
        
        print(f"Fetching Micromate data from {one_hour_ago_est.strftime('%Y-%m-%dT%H:%M:%S')} to {now_est.strftime('%Y-%m-%dT%H:%M:%S')} EST")

        # 3. Fetch data from Micromate API
        url = "https://imsite.dullesgeotechnical.com/api/micromate/readings"
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Failed to fetch Micromate data: {response.status_code} {response.text}")
            return

        data = response.json()
        micromate_readings = data.get('MicromateReadings', [])
        
        if not micromate_readings:
            print("No Micromate data received")
            return

        print(f"Received {len(micromate_readings)} Micromate data points")

        # 4. Filter data for the last hour and group by hour
        hourly_data = {}
        for reading in micromate_readings:
            try:
                # Parse timestamp
                timestamp_str = reading['Time']
                dt_utc = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                dt_est = dt_utc.astimezone(est)
                
                # Check if reading is within the last hour
                if dt_est < one_hour_ago_est or dt_est > now_est:
                    continue
                
                # Extract hour key (YYYY-MM-DD-HH)
                hour_key = dt_est.strftime('%Y-%m-%d-%H')
                
                # Get values
                longitudinal = abs(float(reading['Longitudinal']))
                transverse = abs(float(reading['Transverse']))
                vertical = abs(float(reading['Vertical']))
                
                if hour_key not in hourly_data:
                    hourly_data[hour_key] = {
                        'max_longitudinal': longitudinal,
                        'max_transverse': transverse,
                        'max_vertical': vertical,
                        'timestamp': timestamp_str,
                        'readings_count': 1
                    }
                else:
                    hourly_data[hour_key]['max_longitudinal'] = max(hourly_data[hour_key]['max_longitudinal'], longitudinal)
                    hourly_data[hour_key]['max_transverse'] = max(hourly_data[hour_key]['max_transverse'], transverse)
                    hourly_data[hour_key]['max_vertical'] = max(hourly_data[hour_key]['max_vertical'], vertical)
                    hourly_data[hour_key]['readings_count'] += 1
                    
            except Exception as e:
                print(f"Failed to process reading: {e}")
                continue

        if not hourly_data:
            print("No Micromate data found for the last hour")
            return

        # 5. Check thresholds for each hour
        alerts_by_hour = {}
        for hour_key, hour_data in hourly_data.items():
            max_longitudinal = hour_data['max_longitudinal']
            max_transverse = hour_data['max_transverse']
            max_vertical = hour_data['max_vertical']
            timestamp = hour_data['timestamp']
            readings_count = hour_data['readings_count']
            
            # Check if we've already sent for this hour
            already_sent = supabase.table('sent_alerts') \
                .select('id') \
                .eq('instrument_id', 'INSTANTEL-1') \
                .eq('node_id', 24252) \
                .eq('timestamp', timestamp) \
                .execute()
            if already_sent.data:
                print(f"Micromate alert already sent for hour {hour_key}, skipping.")
                continue

            messages = []
            
            # Check shutdown thresholds
            for axis, value, axis_desc in [('Longitudinal', max_longitudinal, 'Longitudinal'), ('Transverse', max_transverse, 'Transverse'), ('Vertical', max_vertical, 'Vertical')]:
                if shutdown_value and value >= shutdown_value:
                    messages.append(f"<b>Shutdown threshold reached on {axis_desc} axis:</b> {value:.6f}")
            
            # Check warning thresholds
            for axis, value, axis_desc in [('Longitudinal', max_longitudinal, 'Longitudinal'), ('Transverse', max_transverse, 'Transverse'), ('Vertical', max_vertical, 'Vertical')]:
                if warning_value and value >= warning_value:
                    messages.append(f"<b>Warning threshold reached on {axis_desc} axis:</b> {value:.6f}")
            
            # Check alert thresholds
            for axis, value, axis_desc in [('Longitudinal', max_longitudinal, 'Longitudinal'), ('Transverse', max_transverse, 'Transverse'), ('Vertical', max_vertical, 'Vertical')]:
                if alert_value and value >= alert_value:
                    messages.append(f"<b>Alert threshold reached on {axis_desc} axis:</b> {value:.6f}")

            if messages:
                alerts_by_hour[hour_key] = {
                    'messages': messages,
                    'timestamp': timestamp,
                    'max_values': {
                        'Longitudinal': max_longitudinal, 
                        'Transverse': max_transverse, 
                        'Vertical': max_vertical
                    },
                    'readings_count': readings_count
                }

        # 6. Send email if there are alerts
        if alerts_by_hour:
            body = _create_micromate_email_body(alerts_by_hour)
            
            current_time = datetime.now(timezone.utc)
            current_time_est = current_time.astimezone(est)
            formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
            subject = f"📊 Instantel Micromate Alert Notification - {formatted_time}"
            
            all_emails = set(alert_emails + warning_emails + shutdown_emails)
            if all_emails:
                send_email(",".join(all_emails), subject, body)
                print(f"Sent Micromate alert email for {len(alerts_by_hour)} hours with alerts")
                
                # Record that we've sent for each hour
                for hour_key, alert_data in alerts_by_hour.items():
                    supabase.table('sent_alerts').insert({
                        'instrument_id': 'INSTANTEL-1',
                        'node_id': 24252,
                        'timestamp': alert_data['timestamp'],
                        'alert_type': 'any'
                    }).execute()
            else:
                print("No alert/warning/shutdown emails configured for INSTANTEL-1")
        else:
            print("No thresholds crossed for any hour in the last hour for Micromate.")
    except Exception as e:
        print(f"Error in check_and_send_micromate_alert: {e}")

def _create_micromate_email_body(alerts_by_hour):
    """Create HTML email body for Micromate alerts"""
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
                <h1>📊 INSTANTEL MICROMATE ALERT NOTIFICATION</h1>
                <p>Dulles Geotechnical Monitoring System</p>
            </div>
            
            <div class="content">
                <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                    This is an automated alert notification from the DGMTS monitoring system. 
                    The following Instantel Micromate thresholds have been exceeded in the last hour:
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
                    <h3>📊 Hour: {hour_key.replace('-', ' ')} - Instantel Micromate Alerts</h3>
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
                                        <td>{alert_data['max_values']['Longitudinal']:.6f}</td>
                                    </tr>
                                    <tr>
                                        <td>Transverse</td>
                                        <td>{alert_data['max_values']['Transverse']:.6f}</td>
                                    </tr>
                                    <tr>
                                        <td>Vertical</td>
                                        <td>{alert_data['max_values']['Vertical']:.6f}</td>
                                    </tr>
                                </tbody>
                            </table>
                            <p style="margin: 10px 0 0 0; font-size: 12px; color: #6c757d;">
                                Based on {alert_data['readings_count']} readings in this hour
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
                        Values shown are the peak readings for each axis during the specified hour.
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

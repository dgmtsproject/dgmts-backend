from flask import Blueprint, request, jsonify
from services.email_service import send_email
from services.alert_service import check_and_send_tiltmeter_alerts, check_and_send_seismograph_alert, check_and_send_smg3_seismograph_alert
from supabase import create_client, Client
from config import Config
from datetime import datetime, timezone
import pytz

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

# Create Blueprint
email_bp = Blueprint('email', __name__, url_prefix='/api')

@email_bp.route('/test-email', methods=['POST'])
def test_email():
    """Test endpoint to verify email functionality"""
    data = request.get_json()
    test_email = data.get('email', 'mahmerraza19@gmail.com')
    
    subject = "Test Email - DGMTS"
    body = """
    <html>
    <body>
        <h2>Test Email</h2>
        <p>This is a test email to verify the email functionality is working.</p>
        <p>If you receive this email, the email configuration is correct.</p>
        <p>Best regards,<br>DGMTS Team</p>
    </body>
    </html>
    """
    
    if send_email(test_email, subject, body):
        return jsonify({"message": "Test email sent successfully"})
    else:
        return jsonify({"error": "Failed to send test email"}), 500

@email_bp.route('/test-tiltmeter-alert', methods=['POST'])
def test_tiltmeter_alert():
    """Test endpoint to send a sample tiltmeter alert email using actual data"""
    try:
        # Get email addresses from request body
        data = request.get_json() or {}
        test_emails = data.get('emails', ['mahmerraza19@gmail.com'])
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = ['mahmerraza19@gmail.com']
        
        # Get latest sensor readings for both nodes
        node_ids = [142939, 143969]
        actual_alerts = {}
        
        for node_id in node_ids:
            instrument_id = Config.NODE_TO_INSTRUMENT_ID.get(node_id)
            if not instrument_id:
                continue
                
            # Get instrument settings
            instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
            instrument = instrument_resp.data[0] if instrument_resp.data else None
            if not instrument:
                continue
            
            # Get reference values
            reference_resp = supabase.table('reference_values').select('*').eq('instrument_id', instrument_id).execute()
            reference_values = reference_resp.data[0] if reference_resp.data else None
            
            # Get latest sensor reading for this node
            latest_resp = supabase.table('sensor_readings') \
                .select('*') \
                .eq('node_id', node_id) \
                .order('timestamp', desc=True) \
                .limit(1) \
                .execute()
            latest_reading = latest_resp.data[0] if latest_resp.data else None
            
            # Get threshold values
            xyz_alert_values = instrument.get('x_y_z_alert_values')
            xyz_warning_values = instrument.get('x_y_z_warning_values')
            xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
            
            print(f"DEBUG TEST {node_id}: Latest reading found: {latest_reading is not None}")
            print(f"DEBUG TEST {node_id}: xyz_alert_values={xyz_alert_values}")
            print(f"DEBUG TEST {node_id}: reference_values enabled={reference_values.get('enabled', False) if reference_values else False}")
            
            if not latest_reading:
                continue
            
            # Process the latest reading
            timestamp = latest_reading['timestamp']
            x = latest_reading.get('x_value')
            y = latest_reading.get('y_value')
            z = latest_reading.get('z_value')
            
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
                ref_x = reference_values.get('reference_x_value', 0)
                ref_y = reference_values.get('reference_y_value', 0)
                ref_z = reference_values.get('reference_z_value', 0)
                
                # Convert to float to ensure proper calculation
                ref_x = float(ref_x) if ref_x is not None else 0.0
                ref_y = float(ref_y) if ref_y is not None else 0.0
                ref_z = float(ref_z) if ref_z is not None else 0.0
                
                # Calculate calibrated values (raw - reference)
                calibrated_x = float(x) - ref_x if x is not None else None
                calibrated_y = float(y) - ref_y if y is not None else None
                calibrated_z = float(z) - ref_z if z is not None else None
                
                print(f"DEBUG TEST {node_id}: Calibrated values - x={calibrated_x}, y={calibrated_y}, z={calibrated_z}")
                print(f"DEBUG TEST {node_id}: Reference values - ref_x={ref_x}, ref_y={ref_y}, ref_z={ref_z}")
                
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
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}")
                
                # Check warning thresholds using calibrated values (X and Z only, no Y)
                for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                    if calibrated_value is None:
                        continue
                    axis_warning_value = base_xyz_warning_values.get(axis_key) if base_xyz_warning_values else None
                    if axis_warning_value and abs(calibrated_value) >= axis_warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}")
                
                # Check alert thresholds using calibrated values (X and Z only, no Y)
                for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                    if calibrated_value is None:
                        continue
                    axis_alert_value = base_xyz_alert_values.get(axis_key) if base_xyz_alert_values else None
                    print(f"DEBUG TEST {node_id} {axis}: calibrated_value={calibrated_value}, axis_alert_value={axis_alert_value}, abs(calibrated_value)={abs(calibrated_value)}, threshold_check={abs(calibrated_value) >= axis_alert_value if axis_alert_value else False}")
                    if axis_alert_value and abs(calibrated_value) >= axis_alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}")
            else:
                print(f"DEBUG TEST {node_id}: Reference values not enabled, using raw values")
                print(f"DEBUG TEST {node_id}: Raw values - x={x}, y={y}, z={z}")
                print(f"DEBUG TEST {node_id}: Threshold values - alert={xyz_alert_values}, warning={xyz_warning_values}, shutdown={xyz_shutdown_values}")
                
                # Use original logic when reference values are not enabled (X and Z only, no Y)
                # Check shutdown thresholds
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    axis_shutdown_value = xyz_shutdown_values.get(axis_key) if xyz_shutdown_values else None
                    if axis_shutdown_value and abs(value) >= axis_shutdown_value:
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {value:.6f} at {formatted_time}")
                
                # Check warning thresholds
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    axis_warning_value = xyz_warning_values.get(axis_key) if xyz_warning_values else None
                    if axis_warning_value and abs(value) >= axis_warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {value:.6f} at {formatted_time}")
                
                # Check alert thresholds
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    axis_alert_value = xyz_alert_values.get(axis_key) if xyz_alert_values else None
                    if axis_alert_value and abs(value) >= axis_alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {value:.6f} at {formatted_time}")
            
            if messages:
                node_messages = [f"<u><b>Timestamp: {formatted_time}</b></u><br>" + "<br>".join(messages)]
                actual_alerts[node_id] = node_messages
        
        # If no actual alerts found, return empty response
        if not actual_alerts:
            return jsonify({
                "message": "No tiltmeter alerts found in latest readings. No email sent.",
                "note": "Only sends emails when actual thresholds are exceeded"
            })
        
        # Create email body with professional styling
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
                .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; border-top: 1px solid #dee2e6; }}
                .footer p {{ margin: 0; }}
                .company-info {{ font-weight: bold; color: #0056d2; }}
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
                        The following tiltmeter thresholds have been exceeded in the latest readings:
                    </p>
        """
        
        # Add alerts for each node
        for node_id, alerts in actual_alerts.items():
            body += f"""
                    <div class="alert-section">
                        <h3>📊 Node {node_id} - Tiltmeter Alerts</h3>
            """
            
            for alert in alerts:
                # Determine alert type for styling
                alert_class = "alert-item"
                if "Shutdown" in alert:
                    alert_class += " shutdown"
                elif "Warning" in alert:
                    alert_class += " warning"
                elif "Alert" in alert:
                    alert_class += " alert"
                elif "Test Alert" in alert:
                    alert_class += " alert"  # Use alert styling for test alerts
                
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
        
        body += f"""
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
        
        current_time = datetime.now(timezone.utc)
        est = pytz.timezone('US/Eastern')
        current_time_est = current_time.astimezone(est)
        formatted_current_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
        
        subject = f"🚨 Tiltmeter Alert Notification - {formatted_current_time}"
        
        # Send to test emails
        if send_email(test_emails, subject, body):
            return jsonify({
                "message": f"Tiltmeter alert email sent successfully to {', '.join(test_emails)}",
                "subject": subject,
                "note": "This shows actual threshold breaches from latest readings",
                "emails_sent_to": test_emails
            })
        else:
            return jsonify({"error": "Failed to send tiltmeter alert email"}), 500
            
    except Exception as e:
        return jsonify({"error": f"Failed to send tiltmeter alert: {str(e)}"}), 500

@email_bp.route('/trigger-tiltmeter-alerts', methods=['POST'])
def trigger_tiltmeter_alerts():
    """Manually trigger the actual tiltmeter alert system"""
    try:
        print("Manually triggering tiltmeter alert system...")
        check_and_send_tiltmeter_alerts()
        return jsonify({
            "message": "Tiltmeter alert system triggered successfully",
            "status": "success"
        })
    except Exception as e:
        print(f"Error triggering tiltmeter alerts: {e}")
        return jsonify({"error": f"Failed to trigger tiltmeter alerts: {str(e)}"}), 500

@email_bp.route('/test-tiltmeter-alerts-with-time-based-refs', methods=['POST'])
def test_tiltmeter_alerts_with_time_based_refs():
    """Test endpoint to trigger tiltmeter alerts and see time-based reference system in action"""
    try:
        # Get email addresses from request body for testing
        data = request.get_json() or {}
        test_emails = data.get('emails', [])
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = []
        
        print("🧪 Testing Tiltmeter Alerts with Time-Based References")
        
        if test_emails:
            print(f"📧 Test emails provided: {', '.join(test_emails)}")
        else:
            print("📧 No test emails provided - will use configured alert emails")
        
        # First show the time-based reference system test
        from services.alert_service import test_time_based_reference_system
        test_time_based_reference_system()
        
        print("🚨 Checking for threshold violations...")
        
        # Then trigger the actual alert system
        check_and_send_tiltmeter_alerts()
        
        return jsonify({
            "message": "Tiltmeter alert test completed successfully - check console logs for time-based reference details",
            "status": "success",
            "note": "Check your email and console logs to see the time-based reference system in action",
            "test_emails_used": test_emails if test_emails else "Using configured alert emails"
        })
    except Exception as e:
        print(f"Error testing tiltmeter alerts with time-based references: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to test tiltmeter alerts: {str(e)}"}), 500

@email_bp.route('/test-time-based-references', methods=['POST'])
def test_time_based_references():
    """Test endpoint to verify the time-based reference system"""
    try:
        from services.alert_service import test_time_based_reference_system
        print("Testing time-based reference system...")
        test_time_based_reference_system()
        return jsonify({
            "message": "Time-based reference system test completed successfully",
            "status": "success"
        })
    except Exception as e:
        print(f"Error testing time-based references: {e}")
        return jsonify({"error": f"Failed to test time-based references: {str(e)}"}), 500

@email_bp.route('/test-tiltmeter-alert-simple', methods=['POST'])
def test_tiltmeter_alert_simple():
    """Test endpoint to send a sample tiltmeter alert email with time-based references"""
    try:
        # Get email addresses from request body
        data = request.get_json() or {}
        test_emails = data.get('emails', ['mahmerraza19@gmail.com'])
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = ['mahmerraza19@gmail.com']
        
        print(f"🧪 Testing Tiltmeter Alert with Time-Based References")
        print(f"📧 Sending to: {', '.join(test_emails)}")
        
        # First show the time-based reference system test
        from services.alert_service import test_time_based_reference_system
        test_time_based_reference_system()
        
        # Get latest sensor readings for both nodes
        node_ids = [142939, 143969]
        actual_alerts = {}
        
        for node_id in node_ids:
            instrument_id = Config.NODE_TO_INSTRUMENT_ID.get(node_id)
            if not instrument_id:
                continue
                
            # Get instrument settings
            instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
            instrument = instrument_resp.data[0] if instrument_resp.data else None
            if not instrument:
                continue
            
            # Get time-based reference values first, then fallback to global
            from services.alert_service import get_time_based_reference_values
            time_based_ref = get_time_based_reference_values(instrument_id)
            
            if time_based_ref:
                reference_values = time_based_ref
                print(f"Using time-based reference values for {instrument_id}")
            else:
                # Fall back to global reference values
                reference_resp = supabase.table('reference_values').select('*').eq('instrument_id', instrument_id).execute()
                reference_values = reference_resp.data[0] if reference_resp.data else None
                if reference_values:
                    print(f"Using global reference values for {instrument_id}")
                else:
                    print(f"No reference values found for {instrument_id}")
            
            # Get latest sensor reading for this node
            latest_resp = supabase.table('sensor_readings') \
                .select('*') \
                .eq('node_id', node_id) \
                .order('timestamp', desc=True) \
                .limit(1) \
                .execute()
            latest_reading = latest_resp.data[0] if latest_resp.data else None
            
            if not latest_reading:
                print(f"No readings found for node {node_id}")
                continue
            
            # Get threshold values
            xyz_alert_values = instrument.get('x_y_z_alert_values')
            xyz_warning_values = instrument.get('x_y_z_warning_values')
            xyz_shutdown_values = instrument.get('x_y_z_shutdown_values')
            
            # Extract values
            x = latest_reading.get('x_value')
            y = latest_reading.get('y_value')
            z = latest_reading.get('z_value')
            timestamp = latest_reading.get('timestamp')
            
            # Format timestamp
            try:
                dt_utc = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                est = pytz.timezone('US/Eastern')
                dt_est = dt_utc.astimezone(est)
                formatted_time = dt_est.strftime('%Y-%m-%d %I:%M %p EST')
            except Exception as e:
                formatted_time = timestamp
            
            messages = []
            
            # Calculate calibrated values when reference values are enabled
            if reference_values and reference_values.get('enabled', False):
                ref_x = reference_values.get('x_reference_value') or 0
                ref_y = reference_values.get('y_reference_value') or 0
                ref_z = reference_values.get('z_reference_value') or 0
                
                # Calculate calibrated values (raw - reference)
                calibrated_x = x - ref_x if x is not None else None
                calibrated_y = y - ref_y if y is not None else None
                calibrated_z = z - ref_z if z is not None else None
                
                ref_type = "time-based" if reference_values.get('time_based', False) else "global"
                print(f"Reference values enabled for {instrument_id} ({ref_type}): X={ref_x}, Y={ref_y}, Z={ref_z}")
                if reference_values.get('time_based', False):
                    print(f"Time-based period: {reference_values.get('from_date')} to {reference_values.get('to_date')}")
                print(f"Raw values: X={x}, Y={y}, Z={z}")
                print(f"Calibrated values: X={calibrated_x}, Y={calibrated_y}, Z={calibrated_z}")
                
                # Check thresholds using calibrated values (X and Z only, no Y)
                for axis, calibrated_value, axis_key, axis_desc in [('X', calibrated_x, 'x', 'Longitudinal'), ('Z', calibrated_z, 'z', 'Transverse')]:
                    if calibrated_value is None:
                        continue
                    
                    # Check shutdown thresholds
                    axis_shutdown_value = xyz_shutdown_values.get(axis_key) if xyz_shutdown_values else None
                    if axis_shutdown_value and abs(calibrated_value) >= axis_shutdown_value:
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
                    
                    # Check warning thresholds
                    axis_warning_value = xyz_warning_values.get(axis_key) if xyz_warning_values else None
                    if axis_warning_value and abs(calibrated_value) >= axis_warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
                    
                    # Check alert thresholds
                    axis_alert_value = xyz_alert_values.get(axis_key) if xyz_alert_values else None
                    if axis_alert_value and abs(calibrated_value) >= axis_alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {calibrated_value:.6f} at {formatted_time}</b>")
            else:
                # Use original logic when reference values are not enabled
                for axis, value, axis_key, axis_desc in [('X', x, 'x', 'Longitudinal'), ('Z', z, 'z', 'Transverse')]:
                    if value is None:
                        continue
                    
                    # Check shutdown thresholds
                    axis_shutdown_value = xyz_shutdown_values.get(axis_key) if xyz_shutdown_values else None
                    if axis_shutdown_value and abs(value) >= axis_shutdown_value:
                        messages.append(f"<b>Shutdown threshold reached on {axis}-axis ({axis_desc}) > {axis_shutdown_value:.3f}: value- {value:.6f} at {formatted_time}</b>")
                    
                    # Check warning thresholds
                    axis_warning_value = xyz_warning_values.get(axis_key) if xyz_warning_values else None
                    if axis_warning_value and abs(value) >= axis_warning_value:
                        messages.append(f"<b>Warning threshold reached on {axis}-axis ({axis_desc}) > {axis_warning_value:.3f}: value- {value:.6f} at {formatted_time}</b>")
                    
                    # Check alert thresholds
                    axis_alert_value = xyz_alert_values.get(axis_key) if xyz_alert_values else None
                    if axis_alert_value and abs(value) >= axis_alert_value:
                        messages.append(f"<b>Alert threshold reached on {axis}-axis ({axis_desc}) > {axis_alert_value:.3f}: value- {value:.6f} at {formatted_time}</b>")
            
            if messages:
                actual_alerts[node_id] = [f"<u><b>Timestamp: {formatted_time}</b></u><br>" + "<br>".join(messages)]
        
        if actual_alerts:
            # Create email body
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
                    .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; font-size: 14px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>🚨 Tiltmeter Alert</h1>
                        <p>Dulles Airport Monitoring System</p>
                    </div>
                    <div class="content">
                        <div class="alert-section">
                            <h3>Alert Details</h3>
            """
            
            for node_id, node_messages in actual_alerts.items():
                body += f"<h4>Node {node_id}</h4>"
                for message in node_messages:
                    body += f'<div class="alert-item">{message}</div>'
            
            body += """
                        </div>
                    </div>
                    <div class="footer">
                        <p>This is a test alert to verify the time-based reference system.</p>
                        <p>Dulles Geotechnical Monitoring & Testing Services</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            # Format current time for subject
            try:
                current_time = datetime.now(pytz.timezone('US/Eastern'))
                formatted_current_time = current_time.strftime('%Y-%m-%d %I:%M %p EST')
            except Exception as e:
                formatted_current_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            
            subject = f"🚨 Tiltmeter Alert Notification - {formatted_current_time}"
            
            # Send email to all test emails
            for email in test_emails:
                if send_email(email, subject, body):
                    print(f"✅ Test alert email sent successfully to {email}")
                else:
                    print(f"❌ Failed to send test alert email to {email}")
            
            return jsonify({
                "message": "Test tiltmeter alert sent successfully",
                "status": "success",
                "emails_sent_to": test_emails,
                "alerts_found": len(actual_alerts),
                "note": "Check console logs for time-based reference system details"
            })
        else:
            return jsonify({
                "message": "No alerts found - thresholds not exceeded",
                "status": "success",
                "emails_sent_to": test_emails,
                "note": "Check console logs for time-based reference system details"
            })
            
    except Exception as e:
        print(f"Error in test tiltmeter alert: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Failed to send test tiltmeter alert: {str(e)}"}), 500

@email_bp.route('/test-seismograph-alert', methods=['POST'])
def test_seismograph_alert():
    """Test endpoint to send a sample seismograph alert email"""
    try:
        # Get email addresses from request body
        data = request.get_json() or {}
        test_emails = data.get('emails', ['mahmerraza19@gmail.com'])
        seismograph_type = data.get('type', 'SMG1')  # SMG1 or SMG-3
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = ['mahmerraza19@gmail.com']
        
        # Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', seismograph_type).execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            return jsonify({"error": f"No instrument found for {seismograph_type}"}), 404
        
        # Create test alert data
        test_alerts = {
            'test_hour': {
                'messages': [
                    "<b>Test Alert threshold reached on X-axis:</b> 0.001234",
                    "<b>Test Warning threshold reached on Y-axis:</b> 0.002345",
                    "<b>Test Shutdown threshold reached on Z-axis:</b> 0.003456"
                ],
                'timestamp': datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d %I:%M %p EST'),
                'max_values': {'X': 0.001234, 'Y': 0.002345, 'Z': 0.003456}
            }
        }
        
        # Create email body
        seismograph_name = "ANC DAR-BC Seismograph" if seismograph_type == "SMG-3" else "Seismograph"
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
                        This is a <strong>TEST</strong> alert notification from the DGMTS monitoring system. 
                        The following {seismograph_name} ({seismograph_type}) thresholds have been exceeded:
                    </p>
        """
        
        # Add alerts for each hour
        for hour_key, alert_data in test_alerts.items():
            body += f"""
                    <div class="alert-section">
                        <h3>📊 Hour: {hour_key.replace('_', ' ').title()} - {seismograph_name} Alerts</h3>
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
                            <div class="timestamp">{alert_data['timestamp']}</div>
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
                            This is a test email to verify the alert system is working correctly.
                        </p>
                    </div>
                </div>
                
                <div class="footer">
                    <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                    <p style="font-size: 12px; margin-top: 5px;">
                        This is a test message. Please do not reply to this email.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        current_time = datetime.now(timezone.utc)
        current_time_est = current_time.astimezone(pytz.timezone('US/Eastern'))
        formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
        subject = f"🌊 {seismograph_name} Test Alert Notification - {formatted_time}"
        
        if send_email(test_emails, subject, body):
            return jsonify({"message": f"Test {seismograph_name} alert email sent successfully"})
        else:
            return jsonify({"error": f"Failed to send test {seismograph_name} alert email"}), 500
            
    except Exception as e:
        print(f"Error in test_seismograph_alert: {e}")
        return jsonify({"error": str(e)}), 500

@email_bp.route('/test-rock-seismograph-alert', methods=['POST'])
def test_rock_seismograph_alert():
    """Test endpoint to send a sample Rock Seismograph alert email"""
    try:
        # Get email addresses and instrument type from request body
        data = request.get_json() or {}
        test_emails = data.get('emails', ['mahmerraza19@gmail.com'])
        instrument_id = data.get('instrument_id', 'ROCKSMG-1')  # ROCKSMG-1 or ROCKSMG-2
        
        # Ensure test_emails is a list
        if isinstance(test_emails, str):
            test_emails = [email.strip() for email in test_emails.split(',') if email.strip()]
        elif not isinstance(test_emails, list):
            test_emails = ['mahmerraza19@gmail.com']
        
        # Validate instrument_id
        if instrument_id not in Config.ROCK_SEISMOGRAPH_INSTRUMENTS:
            return jsonify({"error": f"Invalid instrument_id. Must be one of: {list(Config.ROCK_SEISMOGRAPH_INSTRUMENTS.keys())}"}), 400
        
        # Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', instrument_id).execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            return jsonify({"error": f"No instrument found for {instrument_id}"}), 404
        
        # Create test alert data
        test_alerts = {
            'test_hour': {
                'messages': [
                    "<b>Test Alert threshold reached on X-axis:</b> 0.001234",
                    "<b>Test Warning threshold reached on Y-axis:</b> 0.002345",
                    "<b>Test Shutdown threshold reached on Z-axis:</b> 0.003456"
                ],
                'timestamp': datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d %I:%M %p EST'),
                'max_values': {'X': 0.001234, 'Y': 0.002345, 'Z': 0.003456}
            }
        }
        
        # Get instrument details from config
        seismograph_name = Config.ROCK_SEISMOGRAPH_INSTRUMENTS[instrument_id]['name']
        project_name = Config.ROCK_SEISMOGRAPH_INSTRUMENTS[instrument_id]['project_name']
        
        # Create email body
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
                    <p>Dulles Geotechnical Monitoring System - {project_name}</p>
                </div>
                
                <div class="content">
                    <p style="font-size: 16px; color: #495057; margin-bottom: 25px;">
                        This is a <strong>TEST</strong> alert notification from the DGMTS monitoring system. 
                        The following {seismograph_name} ({instrument_id}) thresholds have been exceeded:
                    </p>
        """
        
        # Add alerts for each hour
        for hour_key, alert_data in test_alerts.items():
            body += f"""
                    <div class="alert-section">
                        <h3>📊 Hour: {hour_key.replace('_', ' ').title()} - {seismograph_name} Alerts ({instrument_id})</h3>
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
                            <div class="timestamp">{alert_data['timestamp']}</div>
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
                            This is a test email to verify the alert system is working correctly.
                            <br><br>
                            <strong>Project:</strong> {project_name}<br>
                            <strong>Instrument:</strong> {instrument_id}
                        </p>
                    </div>
                </div>
                
                <div class="footer">
                    <p><span class="company-info">Dulles Geotechnical</span> | Instrumentation Monitoring System</p>
                    <p style="font-size: 12px; margin-top: 5px;">
                        This is a test message. Please do not reply to this email.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        current_time = datetime.now(timezone.utc)
        current_time_est = current_time.astimezone(pytz.timezone('US/Eastern'))
        formatted_time = current_time_est.strftime('%Y-%m-%d %I:%M %p EST')
        subject = f"🌊 {seismograph_name} Test Alert Notification - {formatted_time}"
        
        if send_email(test_emails, subject, body):
            return jsonify({
                "message": f"Test {seismograph_name} alert email sent successfully",
                "instrument_id": instrument_id,
                "project_name": project_name,
                "emails_sent_to": test_emails
            })
        else:
            return jsonify({"error": f"Failed to send test {seismograph_name} alert email"}), 500
            
    except Exception as e:
        print(f"Error in test_rock_seismograph_alert: {e}")
        return jsonify({"error": str(e)}), 500

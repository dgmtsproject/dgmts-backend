import os
import json
import glob
import re
from flask import Blueprint, jsonify, current_app, request
from config import Config
from services.micromate_service import check_and_send_micromate_alert, check_and_send_instantel2_alert, get_um16368_readings

micromate_bp = Blueprint('micromate', __name__, url_prefix='/api/micromate')

@micromate_bp.route('/readings', methods=['GET'])
def get_micromate_readings():
    """
    Get all VibrationHistograms data from -H.json files in the FTP server files directory.
    Returns a combined list of all MicromateReadings from all files.
    """
    try:
        # Get the FTP server files path from config
        ftp_files_path = current_app.config.get('FTP_SERVER_FILES_PATH', 'ftp-server-files')
        
        # Check if the directory exists
        if not os.path.exists(ftp_files_path):
            return jsonify({
                'error': f'FTP server files directory not found: {ftp_files_path}',
                'message': 'Please check the FTP_SERVER_FILES_PATH configuration'
            }), 404
        
        # Find all -H.json files in the directory
        pattern = os.path.join(ftp_files_path, '*-H.json')
        h_files = glob.glob(pattern)
        
        if not h_files:
            return jsonify({
                'error': f'No -H.json files found in directory: {ftp_files_path}',
                'message': 'Please ensure the directory contains files ending with -H.json'
            }), 404
        
        # Sort files by name (which includes timestamp)
        h_files.sort()
        
        all_vibration_histograms = []
        processed_files = []
        errors = []
        
        # Process each -H.json file
        for file_path in h_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as file:
                    data = json.load(file)
                    
                    # Extract VibrationHistograms if they exist
                    if 'VibrationHistograms' in data and isinstance(data['VibrationHistograms'], list):
                        # Add file metadata to each reading
                        file_name = os.path.basename(file_path)
                        for reading in data['VibrationHistograms']:
                            reading['source_file'] = file_name
                            all_vibration_histograms.append(reading)
                        
                        processed_files.append({
                            'file': file_name,
                            'readings_count': len(data['VibrationHistograms'])
                        })
                    else:
                        errors.append(f'No VibrationHistograms found in {os.path.basename(file_path)}')
                        
            except json.JSONDecodeError as e:
                errors.append(f'Invalid JSON in {os.path.basename(file_path)}: {str(e)}')
            except Exception as e:
                errors.append(f'Error reading {os.path.basename(file_path)}: {str(e)}')
        
        # Sort all readings by Time
        all_vibration_histograms.sort(key=lambda x: x.get('Time', ''))
        
        response_data = {
            'MicromateReadings': all_vibration_histograms,
            'summary': {
                'total_readings': len(all_vibration_histograms),
                'files_processed': len(processed_files),
                'files_found': len(h_files),
                'errors_count': len(errors)
            },
            'processed_files': processed_files
        }
        
        # Include errors in response if any
        if errors:
            response_data['errors'] = errors
        
        return jsonify(response_data)
        
    except Exception as e:
        return jsonify({
            'error': f'Internal server error: {str(e)}',
            'message': 'An unexpected error occurred while processing the request'
        }), 500

@micromate_bp.route('/files', methods=['GET'])
def list_h_files():
    """
    List all -H.json files in the FTP server files directory.
    Useful for debugging and verification.
    """
    try:
        # Get the FTP server files path from config
        ftp_files_path = current_app.config.get('FTP_SERVER_FILES_PATH', 'ftp-server-files')
        
        # Check if the directory exists
        if not os.path.exists(ftp_files_path):
            return jsonify({
                'error': f'FTP server files directory not found: {ftp_files_path}',
                'message': 'Please check the FTP_SERVER_FILES_PATH configuration'
            }), 404
        
        # Find all -H.json files in the directory
        pattern = os.path.join(ftp_files_path, '*-H.json')
        h_files = glob.glob(pattern)
        
        # Sort files by name
        h_files.sort()
        
        # Get file information
        file_info = []
        for file_path in h_files:
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            file_info.append({
                'filename': file_name,
                'size_bytes': file_size,
                'path': file_path
            })
        
        return jsonify({
            'ftp_files_path': ftp_files_path,
            'total_files': len(h_files),
            'files': file_info
        })
        
    except Exception as e:
        return jsonify({
            'error': f'Internal server error: {str(e)}',
            'message': 'An unexpected error occurred while listing files'
        }), 500

@micromate_bp.route('/check-alerts', methods=['POST'])
def check_micromate_alerts():
    """
    Check Instantel Micromate alerts and send emails if thresholds are exceeded.
    This endpoint triggers the alert checking process using instrument-configured emails.
    """
    try:
        check_and_send_micromate_alert()
        return jsonify({
            'message': 'Micromate alert check completed successfully',
            'status': 'success'
        }), 200
    except Exception as e:
        return jsonify({
            'error': f'Failed to check Micromate alerts: {str(e)}',
            'message': 'An error occurred while checking alerts'
        }), 500

@micromate_bp.route('/check-alerts-custom', methods=['POST'])
def check_micromate_alerts_custom():
    """
    Check Instantel Micromate alerts for all axes over the past 1 week and send emails to custom email addresses.
    
    Request body (JSON):
    {
        "emails": ["email1@example.com", "email2@example.com"],
        "force_resend": false  // Optional: if true, will resend even if alert was already sent (for testing)
    }
    
    This endpoint checks all three axes (Longitudinal, Transverse, Vertical) for the past 7 days
    and sends alerts to the provided email addresses if any thresholds are exceeded.
    The time window is 1 week (10,080 minutes) from the current time.
    
    Note: This is a TEST endpoint and does not affect the production scheduled alerts.
    """
    try:
        # Get request data
        data = request.get_json()
        
        if not data:
            return jsonify({
                'error': 'No request body provided',
                'message': 'Please provide email addresses in the request body'
            }), 400
        
        emails = data.get('emails', [])
        force_resend = data.get('force_resend', False)  # Default to False for safety
        
        if not emails:
            return jsonify({
                'error': 'No email addresses provided',
                'message': 'Please provide at least one email address in the "emails" field'
            }), 400
        
        if not isinstance(emails, list):
            return jsonify({
                'error': 'Invalid email format',
                'message': 'Emails must be provided as a list/array'
            }), 400
        
        # Validate email format (basic validation)
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        invalid_emails = [email for email in emails if not re.match(email_pattern, str(email))]
        if invalid_emails:
            return jsonify({
                'error': 'Invalid email addresses',
                'message': f'The following email addresses are invalid: {invalid_emails}'
            }), 400
        
        # Check alerts with custom emails for the past 1 week (7 days = 10080 minutes)
        one_week_minutes = 7 * 24 * 60  # 10080 minutes
        result = check_and_send_micromate_alert(custom_emails=emails, time_window_minutes=one_week_minutes, force_resend=force_resend)
        
        response_data = {
            'message': 'Micromate alert check completed successfully',
            'status': 'success',
            'emails_sent_to': emails,
            'total_recipients': len(emails),
            'time_window': '1 week (7 days)',
            'time_window_minutes': one_week_minutes
        }
        
        # Add detailed results if available
        if result:
            response_data.update({
                'total_readings_checked': result.get('total_readings_checked', 0),
                'readings_with_alerts': result.get('readings_with_alerts', 0),
                'readings_already_sent': result.get('readings_already_sent', 0),
                'emails_sent': result.get('emails_sent', 0),
                'alert_timestamps': result.get('alert_timestamps', []),
                'skipped_timestamps_count': len(result.get('skipped_timestamps', [])),
                'force_resend': force_resend,
                'test_mode': True  # Indicate this is a test endpoint
            })
            
            # Add error if any
            if 'error' in result:
                response_data['error'] = result['error']
                response_data['status'] = 'error'
        
        return jsonify(response_data), 200
        
    except Exception as e:
        return jsonify({
            'error': f'Failed to check Micromate alerts: {str(e)}',
            'message': 'An error occurred while checking alerts'
        }), 500

@micromate_bp.route('/instantel2/check-alerts', methods=['POST'])
def check_instantel2_alerts():
    """
    Check Instantel 2 (UM16368) alerts and send emails if thresholds are exceeded.
    This endpoint triggers the alert checking process using instrument-configured emails.
    """
    try:
        check_and_send_instantel2_alert()
        return jsonify({
            'message': 'Instantel 2 alert check completed successfully',
            'status': 'success'
        }), 200
    except Exception as e:
        return jsonify({
            'error': f'Failed to check Instantel 2 alerts: {str(e)}',
            'message': 'An error occurred while checking alerts'
        }), 500

@micromate_bp.route('/instantel2/check-alerts-custom', methods=['POST'])
def check_instantel2_alerts_custom():
    """
    Check Instantel 2 (UM16368) alerts for all axes over the past 1 week and send emails to custom email addresses.
    
    Request body (JSON):
    {
        "emails": ["email1@example.com", "email2@example.com"],
        "force_resend": false  // Optional: if true, will resend even if alert was already sent (for testing)
    }
    
    This endpoint checks all three axes (X-axis/Longitudinal, Y-axis/Transverse, Z-axis/Vertical) for the past 7 days
    and sends alerts to the provided email addresses if any thresholds are exceeded.
    The time window is 1 week (10,080 minutes) from the current time.
    
    Note: This is a TEST endpoint and does not affect the production scheduled alerts.
    """
    try:
        # Get request data
        data = request.get_json()
        
        if not data:
            return jsonify({
                'error': 'No request body provided',
                'message': 'Please provide email addresses in the request body'
            }), 400
        
        emails = data.get('emails', [])
        force_resend = data.get('force_resend', False)  # Default to False for safety
        
        if not emails:
            return jsonify({
                'error': 'No email addresses provided',
                'message': 'Please provide at least one email address in the "emails" field'
            }), 400
        
        if not isinstance(emails, list):
            return jsonify({
                'error': 'Invalid email format',
                'message': 'Emails must be provided as a list/array'
            }), 400
        
        # Validate email format (basic validation)
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        invalid_emails = [email for email in emails if not re.match(email_pattern, str(email))]
        if invalid_emails:
            return jsonify({
                'error': 'Invalid email addresses',
                'message': f'The following email addresses are invalid: {invalid_emails}'
            }), 400
        
        # Check alerts with custom emails for the past 1 week (7 days = 10080 minutes)
        one_week_minutes = 7 * 24 * 60  # 10080 minutes
        result = check_and_send_instantel2_alert(custom_emails=emails, time_window_minutes=one_week_minutes, force_resend=force_resend)
        
        response_data = {
            'message': 'Instantel 2 alert check completed successfully',
            'status': 'success',
            'emails_sent_to': emails,
            'total_recipients': len(emails),
            'time_window': '1 week (7 days)',
            'time_window_minutes': one_week_minutes
        }
        
        # Add detailed results if available
        if result:
            response_data.update({
                'total_readings_checked': result.get('total_readings_checked', 0),
                'readings_with_alerts': result.get('readings_with_alerts', 0),
                'readings_already_sent': result.get('readings_already_sent', 0),
                'emails_sent': result.get('emails_sent', 0),
                'alert_timestamps': result.get('alert_timestamps', []),
                'skipped_timestamps_count': len(result.get('skipped_timestamps', [])),
                'force_resend': force_resend,
                'test_mode': True  # Indicate this is a test endpoint
            })
            
            # Add error if any
            if 'error' in result:
                response_data['error'] = result['error']
                response_data['status'] = 'error'
        
        return jsonify(response_data), 200
        
    except Exception as e:
        return jsonify({
            'error': f'Failed to check Instantel 2 alerts: {str(e)}',
            'message': 'An error occurred while checking alerts'
        }), 500

@micromate_bp.route('/test-last-reading', methods=['GET', 'POST'])
def test_last_reading():
    """
    Test endpoint to show what the scheduler will get when checking Instantel 1 alerts.
    
    GET: Shows the actual latest reading
    POST: Can provide a timestamp to test with a specific reading
    
    POST Body Example:
    {
        "time": "2025-11-18T13:50:06.053+00:00"
    }
    
    This will find the latest reading that is <= the provided timestamp and check it.
    """
    # Get optional timestamp from request
    test_timestamp = None
    if request.method == 'POST':
        data = request.get_json() or {}
        test_timestamp = data.get('time') or data.get('Time') or data.get('timestamp')
    
    return _test_last_reading_internal(test_timestamp)

@micromate_bp.route('/test-last-reading-check', methods=['POST'])
def test_last_reading_and_check():
    """
    Test endpoint that checks a specific reading and actually runs the alert check.
    
    POST Body Example:
    {
        "time": "2025-11-18T13:50:06.053+00:00",
        "send_alert": false  // Optional: if true, will actually send alert
    }
    
    This will:
    1. Find the latest reading <= provided timestamp
    2. Check thresholds
    3. Optionally send alert if thresholds exceeded
    """
    try:
        data = request.get_json() or {}
        test_timestamp = data.get('time') or data.get('Time') or data.get('timestamp')
        send_alert = data.get('send_alert', False)
        
        if not test_timestamp:
            return jsonify({
                'error': 'Timestamp required. Provide "time" in request body.',
                'example': {'time': '2025-11-18T13:50:06.053+00:00'}
            }), 400
        
        # Use the internal function to get the reading info
        result = _test_last_reading_internal(test_timestamp)
        
        if result[1] != 200:  # If there was an error
            return result
        
        reading_data = result[0].get_json()
        
        # If thresholds exceeded and send_alert is True, actually send the alert
        if reading_data.get('any_threshold_exceeded') and send_alert:
            from services.micromate_service import check_and_send_micromate_alert
            # We need to modify the function to accept a specific reading
            # For now, just return the info
            return jsonify({
                **reading_data,
                'note': 'To actually send alert, use the /api/micromate/check-alerts endpoint',
                'send_alert_requested': True
            }), 200
        
        return jsonify({
            **reading_data,
            'send_alert_requested': send_alert,
            'note': 'Set "send_alert": true to actually send alert if thresholds exceeded'
        }), 200
        
    except Exception as e:
        import traceback
        return jsonify({
            'error': f'Failed to test reading: {str(e)}',
            'traceback': traceback.format_exc(),
            'status': 'error'
        }), 500

def _test_last_reading_internal(test_timestamp=None):
    """
    Internal function to test last reading with optional timestamp.
    Finds the latest reading <= test_timestamp (or actual latest if None).
    """
    try:
        from supabase import create_client
        from config import Config
        import requests
        from datetime import datetime
        
        supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        
        # 1. Get instrument settings
        instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'Instantel 1').execute()
        instrument = instrument_resp.data[0] if instrument_resp.data else None
        if not instrument:
            return jsonify({
                'error': 'No instrument found for Instantel 1',
                'status': 'error'
            }), 404
        
        alert_value = instrument.get('alert_value')
        warning_value = instrument.get('warning_value')
        shutdown_value = instrument.get('shutdown_value')
        
        # 2. Fetch data from Micromate API
        url = "https://imsite.dullesgeotechnical.com/api/micromate/readings"
        response = requests.get(url)
        if response.status_code != 200:
            return jsonify({
                'error': f'Failed to fetch Micromate data: {response.status_code}',
                'status': 'error'
            }), 500
        
        data = response.json()
        micromate_readings = data.get('MicromateReadings', [])
        
        if not micromate_readings:
            return jsonify({
                'error': 'No Micromate data received',
                'status': 'error'
            }), 404
        
        # 3. Get the last reading (with respect to test_timestamp if provided)
        if test_timestamp:
            # Find the latest reading that is <= test_timestamp
            print(f"Testing with provided timestamp: {test_timestamp}")
            try:
                test_dt = datetime.fromisoformat(test_timestamp.replace('Z', '+00:00'))
                # Filter readings <= test_timestamp and sort by Time
                filtered_readings = []
                for reading in micromate_readings:
                    try:
                        reading_time = reading.get('Time', '')
                        if reading_time:
                            reading_dt = datetime.fromisoformat(reading_time.replace('Z', '+00:00'))
                            if reading_dt <= test_dt:
                                filtered_readings.append(reading)
                    except:
                        continue
                
                if not filtered_readings:
                    return jsonify({
                        'error': f'No readings found before or at the provided timestamp: {test_timestamp}',
                        'status': 'error',
                        'test_timestamp': test_timestamp
                    }), 404
                
                sorted_readings = sorted(filtered_readings, key=lambda x: x.get('Time', ''), reverse=True)
                last_reading = sorted_readings[0]
                print(f"Found latest reading up to {test_timestamp}: {last_reading.get('Time')}")
            except Exception as e:
                return jsonify({
                    'error': f'Invalid timestamp format: {test_timestamp}. Error: {str(e)}',
                    'status': 'error',
                    'expected_format': '2025-11-18T13:50:06.053+00:00'
                }), 400
        else:
            # Get the actual latest reading
            sorted_readings = sorted(micromate_readings, key=lambda x: x.get('Time', ''), reverse=True)
            last_reading = sorted_readings[0]
        
        timestamp_str = last_reading.get('Time', 'N/A')
        longitudinal = abs(float(last_reading.get('Longitudinal', 0)))
        transverse = abs(float(last_reading.get('Transverse', 0)))
        vertical = abs(float(last_reading.get('Vertical', 0)))
        
        # 4. Check if alert was already sent
        already_sent = supabase.table('sent_alerts') \
            .select('id, timestamp, alert_type') \
            .eq('instrument_id', 'Instantel 1') \
            .eq('node_id', 24252) \
            .eq('timestamp', timestamp_str) \
            .execute()
        
        alert_already_sent = len(already_sent.data) > 0
        sent_alert_record = already_sent.data[0] if already_sent.data else None
        
        # 5. Check thresholds
        threshold_checks = {
            'Longitudinal': {
                'value': longitudinal,
                'exceeds_alert': alert_value and longitudinal >= alert_value,
                'exceeds_warning': warning_value and longitudinal >= warning_value,
                'exceeds_shutdown': shutdown_value and longitudinal >= shutdown_value
            },
            'Transverse': {
                'value': transverse,
                'exceeds_alert': alert_value and transverse >= alert_value,
                'exceeds_warning': warning_value and transverse >= warning_value,
                'exceeds_shutdown': shutdown_value and transverse >= shutdown_value
            },
            'Vertical': {
                'value': vertical,
                'exceeds_alert': alert_value and vertical >= alert_value,
                'exceeds_warning': warning_value and vertical >= warning_value,
                'exceeds_shutdown': shutdown_value and vertical >= shutdown_value
            }
        }
        
        # Determine if any threshold is exceeded
        any_threshold_exceeded = any(
            check['exceeds_alert'] or check['exceeds_warning'] or check['exceeds_shutdown']
            for check in threshold_checks.values()
        )
        
        # Determine what action scheduler would take
        if alert_already_sent:
            action = 'SKIP - Alert already sent for this timestamp'
        elif any_threshold_exceeded:
            action = 'SEND ALERT - Thresholds exceeded and no alert sent yet'
        else:
            action = 'NO ACTION - Thresholds not exceeded'
        
        return jsonify({
            'status': 'success',
            'instrument_id': 'Instantel 1',
            'test_timestamp_provided': test_timestamp if test_timestamp else None,
            'last_reading': {
                'timestamp': timestamp_str,
                'Longitudinal': longitudinal,
                'Transverse': transverse,
                'Vertical': vertical,
                'full_reading': last_reading
            },
            'thresholds': {
                'alert_value': alert_value,
                'warning_value': warning_value,
                'shutdown_value': shutdown_value
            },
            'threshold_checks': threshold_checks,
            'any_threshold_exceeded': any_threshold_exceeded,
            'alert_already_sent': alert_already_sent,
            'sent_alert_record': sent_alert_record,
            'scheduler_action': action,
            'total_readings_available': len(micromate_readings)
        }), 200
        
    except Exception as e:
        import traceback
        return jsonify({
            'error': f'Failed to test last reading: {str(e)}',
            'traceback': traceback.format_exc(),
            'status': 'error'
        }), 500

@micromate_bp.route('/UM16368/readings', methods=['GET'])
def get_um16368_readings_endpoint():
    """
    Get all readings from CSV files in /root/root/ftp-server/Dulles Test/UM16368/CSV directory.
    Returns readings parsed from CSV files with dynamic header detection:
    - Only processes files ending with IDFH.csv (excludes IDFW.csv files)
    - Searches for "PPV" in any cell to locate the header structure
    - Row with PPV: Contains formats (PPV, PVS, etc.)
    - Row 1 after PPV: Contains TIME in first column, column names (Tran, Vert, Long, Geophone), and units
    - Rows after header: Actual readings
    
    Each reading contains Time, source_file, and readings (key-value pairs).
    """
    try:
        result = get_um16368_readings()
        
        if not result:
            return jsonify({
                'error': 'Failed to retrieve UM16368 readings',
                'message': 'No data could be retrieved from CSV files'
            }), 500
        
        return jsonify({
            'UM16368Readings': result.get('readings', []),
            'summary': result.get('summary', {}),
            'processed_files': result.get('processed_files', []),
            'errors': result.get('errors', [])
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': f'Internal server error: {str(e)}',
            'message': 'An unexpected error occurred while processing the request'
        }), 500

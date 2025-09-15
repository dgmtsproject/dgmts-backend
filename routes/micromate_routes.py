import os
import json
import glob
from flask import Blueprint, jsonify, current_app
from config import Config

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

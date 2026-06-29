import os
import json
from flask import Blueprint, jsonify, current_app, request

access_software_bp = Blueprint('access_software', __name__, url_prefix='/api/access-software')

# Tables exported from iCCard3000.mdb by the RDP-side script, mapped to the
# JSON filename uploaded via SFTP and the key used in the API response.
EXPORTED_TABLES = {
    'SwipeRecords': 't_d_SwipeRecord.json',
    'Attendance': 't_a_Attendence.json',
}


def _read_export(files_path, filename):
    """Read one exported JSON file and return (rows, error).

    The RDP-side script writes each table as a JSON array of row objects.
    """
    file_path = os.path.join(files_path, filename)
    if not os.path.exists(file_path):
        return [], f'File not found: {filename}'
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return [], f'Invalid JSON in {filename}: {str(e)}'
    except Exception as e:
        return [], f'Error reading {filename}: {str(e)}'

    # PowerShell ConvertTo-Json emits a single object (not an array) when a
    # table has exactly one row; normalise both shapes to a list.
    if isinstance(data, dict):
        data = [data]
    elif not isinstance(data, list):
        return [], f'Unexpected JSON structure in {filename}'

    return data, None


@access_software_bp.route('/routes', methods=['GET'])
def get_access_software_routes():
    """
    Return the access-control records exported from iCCard3000.mdb as JSON.

    Reads the JSON files uploaded by the RDP-side export script from
    ACCESS_SOFTWARE_FILES_PATH and returns:
      - SwipeRecords: rows from t_d_SwipeRecord (door swipe events)
      - Attendance:   rows from t_a_Attendence (attendance parameters)

    Optional query parameters:
      - table: limit the response to one logical table
               (e.g. ?table=SwipeRecords or ?table=Attendance)
    """
    try:
        files_path = current_app.config.get('ACCESS_SOFTWARE_FILES_PATH', 'access-software-files')

        if not os.path.exists(files_path):
            return jsonify({
                'error': f'Access software files directory not found: {files_path}',
                'message': 'Please check the ACCESS_SOFTWARE_FILES_PATH configuration and that the RDP upload has run.'
            }), 404

        requested = request.args.get('table')
        if requested and requested not in EXPORTED_TABLES:
            return jsonify({
                'error': f'Unknown table: {requested}',
                'available_tables': list(EXPORTED_TABLES.keys())
            }), 400

        tables_to_read = {requested: EXPORTED_TABLES[requested]} if requested else EXPORTED_TABLES

        response_data = {}
        summary = {}
        errors = []

        for key, filename in tables_to_read.items():
            rows, error = _read_export(files_path, filename)
            response_data[key] = rows
            summary[key] = len(rows)
            if error:
                errors.append(error)

        result = {
            **response_data,
            'summary': summary,
        }
        if errors:
            result['errors'] = errors

        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'error': f'Internal server error: {str(e)}',
            'message': 'An unexpected error occurred while processing the request'
        }), 500

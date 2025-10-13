from flask import Blueprint, request, jsonify
from services.sensor_service import get_sensor_data_with_reference_values, get_raw_sensor_data, fetch_and_store_all_sensor_data

# Create Blueprint
sensor_bp = Blueprint('sensor', __name__, url_prefix='/api')

@sensor_bp.route('/sensor-data/<int:node_id>', methods=['GET'])
def api_get_sensor_data(node_id):
    """API endpoint to get sensor data with reference values applied if enabled"""
    try:
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')
        limit = int(request.args.get('limit', 1000))
        
        data = get_sensor_data_with_reference_values(node_id, start_time, end_time, limit)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@sensor_bp.route('/sensor-data-raw/<int:node_id>', methods=['GET'])
def api_get_raw_sensor_data(node_id):
    """API endpoint to get raw sensor data without reference values applied"""
    try:
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')
        limit = int(request.args.get('limit', 1000))
        
        data = get_raw_sensor_data(node_id, start_time, end_time, limit)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@sensor_bp.route('/fetch-sensor-data', methods=['POST'])
def api_fetch_sensor_data():
    """Manually trigger sensor data fetch"""
    try:
        fetch_and_store_all_sensor_data()
        return jsonify({"message": "Sensor data fetch completed"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

import requests
from supabase import create_client, Client
from config import Config
from datetime import datetime, timedelta, timezone
import pytz

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

def fetch_sensor_data_from_api(node_id):
    """Fetch sensor data from external API with basic auth"""
    try:
        url = f"{Config.SENSOR_API_BASE}/{node_id}"
        print(f"Fetching data from: {url}")
        response = requests.get(url, auth=(Config.SENSOR_USERNAME, Config.SENSOR_PASSWORD))
        
        print(f"Response status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Received {len(data)} records for node {node_id}")
            return data
        else:
            print(f"API request failed for node {node_id}: {response.status_code}")
            print(f"Response text: {response.text}")
            return []
    except Exception as e:
        print(f"Error fetching data for node {node_id}: {e}")
        return []

def store_sensor_data(data, node_id):
    """Store simplified sensor data in Supabase"""
    try:
        print(f"Processing {len(data)} records for node {node_id}")
        stored_count = 0
        
        for i, reading in enumerate(data):
            # Only process til90ReadingsV1
            if reading.get('type') != 'til90ReadingsV1':
                print(f"Skipping record {i+1}: type is {reading.get('type')}")
                continue

            value = reading.get('value', {})
            readings = value.get('readings', [])
            timestamp = value.get('readTimestamp')

            if not readings or not timestamp:
                print(f"Skipping record {i+1}: missing readings or timestamp")
                print(f"DEBUG: reading={reading}")
                continue

            # Extract x, y, z values from channels
            x_value = y_value = z_value = None
            for channel_reading in readings:
                channel = channel_reading.get('channel')
                tilt = channel_reading.get('tilt')
                if channel == 0:
                    x_value = tilt
                elif channel == 1:
                    y_value = tilt
                elif channel == 2:
                    z_value = tilt

            print(f"Extracted values - X: {x_value}, Y: {y_value}, Z: {z_value}")

            # Prepare data for insertion
            sensor_data = {
                'node_id': node_id,
                'timestamp': timestamp,
                'x_value': x_value,
                'y_value': y_value,
                'z_value': z_value
            }
            print(f"Inserting data: {sensor_data}")
            response = supabase.table('sensor_readings').insert(sensor_data).execute()
            print(f"Insert response: {response}")
            stored_count += 1
        print(f"Successfully stored {stored_count} records for node {node_id}")
        return True
    except Exception as e:
        print(f"Error storing sensor data: {e}")
        import traceback
        traceback.print_exc()
        return False

def fetch_and_store_all_sensor_data():
    """Fetch and store data for all nodes"""
    print("Starting fetch_and_store_all_sensor_data...")
    for node_id in Config.SENSOR_NODES:
        print(f"\n=== Processing Node {node_id} ===")
        data = fetch_sensor_data_from_api(node_id)
        if data:
            print(f"Data received for node {node_id}, attempting to store...")
            if store_sensor_data(data, node_id):
                print(f"Successfully stored data for node {node_id}")
            else:
                print(f"Failed to store data for node {node_id}")
        else:
            print(f"No data received for node {node_id}")
    print("Completed fetch_and_store_all_sensor_data")

def get_sensor_data_with_reference_values(node_id, start_time=None, end_time=None, limit=1000):
    """Get sensor data with reference values applied if enabled"""
    try:
        query = supabase.table('sensor_readings').select('*').eq('node_id', node_id)
        
        if start_time:
            query = query.gte('timestamp', start_time)
        if end_time:
            query = query.lte('timestamp', end_time)
            
        query = query.order('timestamp', desc=True).limit(limit)
        response = query.execute()
        
        # Apply reference values if enabled
        instrument_id = Config.NODE_TO_INSTRUMENT_ID.get(node_id)
        if instrument_id:
            reference_resp = supabase.table('reference_values').select('*').eq('instrument_id', instrument_id).execute()
            reference_values = reference_resp.data[0] if reference_resp.data else None
            
            if reference_values and reference_values.get('enabled', False):
                # Apply reference values to sensor data
                ref_x = reference_values.get('reference_x_value', 0) or 0
                ref_y = reference_values.get('reference_y_value', 0) or 0
                ref_z = reference_values.get('reference_z_value', 0) or 0
                
                calibrated_data = []
                for reading in response.data:
                    calibrated_reading = reading.copy()
                    if reading.get('x_value') is not None:
                        calibrated_reading['x_value'] = reading['x_value'] - ref_x
                    if reading.get('y_value') is not None:
                        calibrated_reading['y_value'] = reading['y_value'] - ref_y
                    if reading.get('z_value') is not None:
                        calibrated_reading['z_value'] = reading['z_value'] - ref_z
                    calibrated_data.append(calibrated_reading)
                
                return calibrated_data
        
        return response.data
    except Exception as e:
        print(f"Error getting sensor data: {e}")
        return []

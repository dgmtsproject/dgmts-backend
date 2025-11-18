#!/usr/bin/env python3
"""
Test script to check Instantel 1 alert functionality locally.
This script simulates what the scheduler does without starting the actual server.

Usage:
    python test.py

This will:
1. Fetch the last reading from the API
2. Show threshold values
3. Check if thresholds are exceeded
4. Check if alert was already sent
5. Show what action would be taken
"""

import requests
from supabase import create_client
from config import Config
from services.micromate_service import check_and_send_micromate_alert

def test_instantel1_alert():
    """Test Instantel 1 alert checking"""
    print("=" * 80)
    print("INSTANTEL 1 ALERT TEST")
    print("=" * 80)
    print()
    
    # Initialize Supabase
    supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
    
    # 1. Get instrument settings
    print("1. Fetching instrument settings...")
    instrument_resp = supabase.table('instruments').select('*').eq('instrument_id', 'Instantel 1').execute()
    instrument = instrument_resp.data[0] if instrument_resp.data else None
    if not instrument:
        print("   ❌ ERROR: No instrument found for Instantel 1")
        return
    
    alert_value = instrument.get('alert_value')
    warning_value = instrument.get('warning_value')
    shutdown_value = instrument.get('shutdown_value')
    
    print(f"   ✅ Instrument found: Instantel 1")
    print(f"   📊 Thresholds:")
    print(f"      - Alert: {alert_value}")
    print(f"      - Warning: {warning_value}")
    print(f"      - Shutdown: {shutdown_value}")
    print()
    
    # 2. Fetch data from API
    print("2. Fetching last reading from API...")
    url = "https://imsite.dullesgeotechnical.com/api/micromate/readings"
    response = requests.get(url)
    if response.status_code != 200:
        print(f"   ❌ ERROR: Failed to fetch data: {response.status_code}")
        return
    
    data = response.json()
    micromate_readings = data.get('MicromateReadings', [])
    
    if not micromate_readings:
        print("   ❌ ERROR: No readings received")
        return
    
    print(f"   ✅ Received {len(micromate_readings)} readings")
    
    # 3. Get the last reading
    sorted_readings = sorted(micromate_readings, key=lambda x: x.get('Time', ''), reverse=True)
    last_reading = sorted_readings[0]
    
    timestamp_str = last_reading.get('Time', 'N/A')
    longitudinal = abs(float(last_reading.get('Longitudinal', 0)))
    transverse = abs(float(last_reading.get('Transverse', 0)))
    vertical = abs(float(last_reading.get('Vertical', 0)))
    
    print(f"   📍 Last reading timestamp: {timestamp_str}")
    print(f"   📊 Values:")
    print(f"      - Longitudinal: {longitudinal:.6f}")
    print(f"      - Transverse: {transverse:.6f}")
    print(f"      - Vertical: {vertical:.6f}")
    print()
    
    # 4. Check if alert was already sent
    print("3. Checking if alert was already sent...")
    already_sent = supabase.table('sent_alerts') \
        .select('id, timestamp, alert_type, created_at') \
        .eq('instrument_id', 'Instantel 1') \
        .eq('node_id', 24252) \
        .eq('timestamp', timestamp_str) \
        .execute()
    
    alert_already_sent = len(already_sent.data) > 0
    if alert_already_sent:
        print(f"   ⚠️  Alert already sent for timestamp {timestamp_str}")
        print(f"   📝 Alert record: {already_sent.data[0]}")
    else:
        print(f"   ✅ No alert sent yet for this timestamp")
    print()
    
    # 5. Check thresholds
    print("4. Checking thresholds...")
    threshold_exceeded = False
    messages = []
    
    for axis, value, axis_name in [
        ('Longitudinal', longitudinal, 'Longitudinal'),
        ('Transverse', transverse, 'Transverse'),
        ('Vertical', vertical, 'Vertical')
    ]:
        axis_exceeded = False
        if shutdown_value and value >= shutdown_value:
            print(f"   🔴 SHUTDOWN threshold exceeded on {axis_name}: {value:.6f} >= {shutdown_value}")
            messages.append(f"Shutdown threshold reached on {axis_name}: {value:.6f}")
            axis_exceeded = True
            threshold_exceeded = True
        elif warning_value and value >= warning_value:
            print(f"   🟡 WARNING threshold exceeded on {axis_name}: {value:.6f} >= {warning_value}")
            messages.append(f"Warning threshold reached on {axis_name}: {value:.6f}")
            axis_exceeded = True
            threshold_exceeded = True
        elif alert_value and value >= alert_value:
            print(f"   🟠 ALERT threshold exceeded on {axis_name}: {value:.6f} >= {alert_value}")
            messages.append(f"Alert threshold reached on {axis_name}: {value:.6f}")
            axis_exceeded = True
            threshold_exceeded = True
        
        if not axis_exceeded:
            print(f"   ✅ {axis_name}: {value:.6f} (within thresholds)")
    print()
    
    # 6. Determine action
    print("5. Scheduler action:")
    if alert_already_sent:
        action = "SKIP - Alert already sent for this timestamp"
        print(f"   ⏭️  {action}")
        print("   ℹ️  The scheduler will skip this reading and wait for the next one.")
    elif threshold_exceeded:
        action = "SEND ALERT - Thresholds exceeded and no alert sent yet"
        print(f"   📧 {action}")
        print("   ℹ️  The scheduler will send an alert email for this reading.")
        print(f"   📝 Messages that would be sent: {messages}")
    else:
        action = "NO ACTION - Thresholds not exceeded"
        print(f"   ✅ {action}")
        print("   ℹ️  The scheduler will check again on the next run (every minute).")
    print()
    
    # 7. Show what the actual function would return
    print("6. Testing actual alert function (dry run - no emails will be sent)...")
    print("   (This simulates what the scheduler does)")
    print()
    
    # Note: We're not actually calling the function here to avoid sending emails
    # But we can show what it would do
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Instrument: Instantel 1")
    print(f"Last Reading Timestamp: {timestamp_str}")
    print(f"Longitudinal: {longitudinal:.6f}")
    print(f"Transverse: {transverse:.6f}")
    print(f"Vertical: {vertical:.6f}")
    print(f"Alert Already Sent: {alert_already_sent}")
    print(f"Thresholds Exceeded: {threshold_exceeded}")
    if threshold_exceeded:
        print(f"Messages: {messages}")
    print(f"Action: {action}")
    print("=" * 80)
    
    # Ask if user wants to actually test the function
    print()
    response = input("Do you want to actually run the alert check function? (yes/no): ").lower().strip()
    if response == 'yes':
        print()
        print("Running check_and_send_micromate_alert()...")
        print("(This will send emails if thresholds are exceeded and alert not already sent)")
        print()
        result = check_and_send_micromate_alert(force_resend=False)
        print()
        print("Result:")
        print(f"  Total readings checked: {result.get('total_readings_checked', 0)}")
        print(f"  Readings with alerts: {result.get('readings_with_alerts', 0)}")
        print(f"  Readings already sent: {result.get('readings_already_sent', 0)}")
        print(f"  Emails sent: {result.get('emails_sent', 0)}")
        if 'error' in result:
            print(f"  Error: {result['error']}")
    else:
        print("Skipping actual function call.")

if __name__ == "__main__":
    try:
        test_instantel1_alert()
    except Exception as e:
        import traceback
        print(f"\n❌ ERROR: {e}")
        print("\nTraceback:")
        traceback.print_exc()


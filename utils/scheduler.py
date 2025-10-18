import schedule
import time
import threading
from services.sensor_service import fetch_and_store_all_sensor_data
from services.alert_service import check_and_send_tiltmeter_alerts, check_and_send_seismograph_alert, check_and_send_smg3_seismograph_alert
from services.rock_seismograph_service import check_and_send_rock_seismograph_alert
from services.micromate_service import check_and_send_micromate_alert
from config import Config

def run_scheduler():
    """Run the scheduler in a background thread"""
    while True:
        schedule.run_pending()
        time.sleep(60)

def setup_scheduled_tasks():
    """Setup all scheduled tasks"""
    # Schedule to run every minute for real-time threshold checking
    schedule.every().minute.do(fetch_and_store_all_sensor_data)
    schedule.every().minute.do(check_and_send_seismograph_alert)
    schedule.every().minute.do(check_and_send_smg3_seismograph_alert)
    schedule.every().minute.do(check_and_send_micromate_alert)
    
    # Tiltmeter alerts are now triggered automatically when new data is inserted
    # No need for scheduled tiltmeter checks
    
    # Schedule Rock Seismograph alerts for each instrument
    for instrument_id in Config.ROCK_SEISMOGRAPH_INSTRUMENTS.keys():
        schedule.every().minute.do(check_and_send_rock_seismograph_alert, instrument_id)

def start_scheduler():
    """Start the scheduler in a background thread"""
    setup_scheduled_tasks()
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    return scheduler_thread

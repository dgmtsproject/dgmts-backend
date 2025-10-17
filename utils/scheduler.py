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
    # Schedule to run every 5 minutes for threshold checking
    schedule.every(5).minutes.do(fetch_and_store_all_sensor_data)
    schedule.every(5).minutes.do(check_and_send_tiltmeter_alerts)
    schedule.every(5).minutes.do(check_and_send_seismograph_alert)
    schedule.every(5).minutes.do(check_and_send_smg3_seismograph_alert)
    schedule.every(5).minutes.do(check_and_send_micromate_alert)
    
    # Schedule Rock Seismograph alerts for each instrument
    for instrument_id in Config.ROCK_SEISMOGRAPH_INSTRUMENTS.keys():
        schedule.every(5).minutes.do(check_and_send_rock_seismograph_alert, instrument_id)

def start_scheduler():
    """Start the scheduler in a background thread"""
    setup_scheduled_tasks()
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    return scheduler_thread

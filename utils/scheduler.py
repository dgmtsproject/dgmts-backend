import schedule
import time
import threading
from datetime import datetime
from services.sensor_service import fetch_and_store_all_sensor_data
from services.alert_service import check_and_send_tiltmeter_alerts, check_and_send_seismograph_alert, check_and_send_smg3_seismograph_alert
from services.rock_seismograph_service import check_and_send_rock_seismograph_alert
from services.micromate_service import check_and_send_micromate_alert
from config import Config

def run_alert_function_with_timing(func, *args, **kwargs):
    """Run an alert function with timing logs"""
    start_time = datetime.now()
    func_name = func.__name__
    print(f"🔄 Starting {func_name} at {start_time.strftime('%H:%M:%S')}")
    
    try:
        func(*args, **kwargs)
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        print(f"✅ Completed {func_name} in {duration:.2f} seconds")
    except Exception as e:
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        print(f"❌ Error in {func_name} after {duration:.2f} seconds: {e}")

def run_seismograph_alerts_parallel():
    """Run all seismograph alert functions in parallel"""
    print(f"🚀 Starting parallel seismograph alert checks at {datetime.now().strftime('%H:%M:%S')}")
    
    # Create threads for each seismograph alert function
    threads = []
    
    # SMG-1 seismograph alert
    thread1 = threading.Thread(
        target=run_alert_function_with_timing, 
        args=(check_and_send_seismograph_alert,),
        name="SMG-1-Alert"
    )
    threads.append(thread1)
    
    # SMG-3 seismograph alert
    thread2 = threading.Thread(
        target=run_alert_function_with_timing, 
        args=(check_and_send_smg3_seismograph_alert,),
        name="SMG-3-Alert"
    )
    threads.append(thread2)
    
    # Micromate alert
    thread3 = threading.Thread(
        target=run_alert_function_with_timing, 
        args=(check_and_send_micromate_alert,),
        name="Micromate-Alert"
    )
    threads.append(thread3)
    
    # Rock seismograph alerts for each instrument
    for instrument_id in Config.ROCK_SEISMOGRAPH_INSTRUMENTS.keys():
        thread = threading.Thread(
            target=run_alert_function_with_timing, 
            args=(check_and_send_rock_seismograph_alert, instrument_id),
            name=f"Rock-{instrument_id}-Alert"
        )
        threads.append(thread)
    
    # Start all threads
    for thread in threads:
        thread.start()
    
    # Wait for all threads to complete
    for thread in threads:
        thread.join()
    
    print(f"🏁 All seismograph alerts completed at {datetime.now().strftime('%H:%M:%S')}")

def run_scheduler():
    """Run the scheduler in a background thread"""
    while True:
        schedule.run_pending()
        time.sleep(60)

def setup_scheduled_tasks():
    """Setup all scheduled tasks"""
    # Schedule to run every minute for real-time threshold checking
    schedule.every().minute.do(fetch_and_store_all_sensor_data)
    
    # Run all seismograph alerts in parallel to prevent missing minutes
    schedule.every().minute.do(run_seismograph_alerts_parallel)
    
    # Tiltmeter alerts are now triggered automatically when new data is inserted
    # No need for scheduled tiltmeter checks

def start_scheduler():
    """Start the scheduler in a background thread"""
    setup_scheduled_tasks()
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    return scheduler_thread

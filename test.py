#!/usr/bin/env python3
"""
Test script to run Rock Seismograph test alerts locally.
This runs the TEST version with hardcoded thresholds and test email.

Usage:
    python test.py

This will:
- Run Rock Seismograph TEST alerts every minute
- Use hardcoded thresholds: 0.013 (alert, warning, shutdown)
- Send to test email: mahmerraza19@gmail.com
- Check last 6 hours of data
- Test only ROCKSMG-2 instrument
- Run until you press Ctrl+C
"""

import schedule
import time
from services.rock_seismograph_service import check_and_send_rock_seismograph_alert_test
from config import Config

def run_test_scheduler():
    """Run Rock Seismograph test scheduler continuously"""
    print("=" * 80)
    print("ROCK SEISMOGRAPH TEST SCHEDULER")
    print("=" * 80)
    print("Running TEST version with:")
    print("  - Hardcoded thresholds: 0.013 (alert, warning, shutdown)")
    print("  - Test email: mahmerraza19@gmail.com")
    print("  - Time window: Last 6 hours")
    print("  - Instrument: ROCKSMG-2 only")
    print("=" * 80)
    print()
    
    # Setup test scheduled tasks - only for ROCKSMG-2
    instrument_id = "ROCKSMG-2"
    if instrument_id in Config.ROCK_SEISMOGRAPH_INSTRUMENTS.keys():
        schedule.every().minute.do(check_and_send_rock_seismograph_alert_test, instrument_id)
        print(f"✅ Scheduled TEST alerts for {instrument_id}")
    else:
        print(f"❌ ERROR: {instrument_id} not found in ROCK_SEISMOGRAPH_INSTRUMENTS")
        return
    
    print()
    print("Scheduler started. Will run every minute.")
    print("Press Ctrl+C to stop.")
    print()
    
    # Run scheduler
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n\nScheduler stopped by user.")

if __name__ == "__main__":
    try:
        run_test_scheduler()
    except Exception as e:
        import traceback
        print(f"\n❌ ERROR: {e}")
        print("\nTraceback:")
        traceback.print_exc()

#!/usr/bin/env python3
"""
Script to delete INFO log records for Instantel 1 and Instantel 2 from sent_alert_logs table.

This script removes all INFO type logs for Instantel instruments, keeping only:
- ERROR logs
- EMAIL_SENT logs
- ALERT_RECORDED logs
- Other important log types
"""

import os
import sys
from datetime import datetime, timezone
from supabase import create_client, Client
from config import Config

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

def delete_instantel_info_logs(dry_run=False):
    """Delete INFO log records for Instantel 1 and Instantel 2"""
    
    instruments = ["Instantel 1", "Instantel 2"]
    total_deleted = 0
    
    print("🧹 Starting INFO log cleanup for Instantel instruments...")
    print("=" * 60)
    
    if dry_run:
        print("🔍 DRY RUN MODE - No records will be deleted")
        print("=" * 60)
    
    for instrument_id in instruments:
        print(f"\n📊 Processing {instrument_id}...")
        
        try:
            # Fetch all INFO logs for this instrument
            response = supabase.table('sent_alert_logs') \
                .select('id, log_type, log, log_time, for_instrument') \
                .eq('for_instrument', instrument_id) \
                .eq('log_type', 'INFO') \
                .execute()
            
            logs = response.data
            print(f"  Found {len(logs)} INFO log records for {instrument_id}")
            
            if not logs:
                print(f"  ✅ No INFO logs to delete for {instrument_id}")
                continue
            
            # Show sample of what will be deleted
            print(f"\n  Sample of logs to be deleted:")
            for i, log in enumerate(logs[:5]):  # Show first 5
                print(f"    - [{log['id']}] {log['log_time']}: {log['log'][:80]}...")
            if len(logs) > 5:
                print(f"    ... and {len(logs) - 5} more")
            
            if not dry_run:
                # Delete each INFO log record
                deleted_count = 0
                for log in logs:
                    try:
                        delete_response = supabase.table('sent_alert_logs') \
                            .delete() \
                            .eq('id', log['id']) \
                            .execute()
                        
                        if delete_response.data:
                            deleted_count += 1
                        else:
                            print(f"    ⚠️  Failed to delete log ID {log['id']}")
                            
                    except Exception as e:
                        print(f"    ⚠️  Error deleting log ID {log['id']}: {e}")
                
                print(f"  ✅ Deleted {deleted_count} INFO log records for {instrument_id}")
                total_deleted += deleted_count
            else:
                print(f"  🔍 Would delete {len(logs)} INFO log records for {instrument_id}")
                total_deleted += len(logs)
                
        except Exception as e:
            print(f"  ❌ Error processing {instrument_id}: {e}")
            continue
    
    print("\n" + "=" * 60)
    if dry_run:
        print(f"🔍 DRY RUN COMPLETE")
        print(f"📊 Total INFO logs that would be deleted: {total_deleted}")
    else:
        print(f"🎉 Cleanup completed!")
        print(f"📊 Total INFO logs deleted: {total_deleted}")
    print(f"📅 Cleanup timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    return total_deleted

def preview_instantel_info_logs():
    """Preview INFO logs that would be deleted"""
    return delete_instantel_info_logs(dry_run=True)

if __name__ == "__main__":
    print("🔧 Instantel INFO Log Cleanup Script")
    print("This script will delete all INFO type logs for Instantel 1 and Instantel 2")
    print("from the sent_alert_logs table.")
    print()
    
    # Check for command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "--preview" or sys.argv[1] == "-p":
            preview_instantel_info_logs()
        elif sys.argv[1] == "--delete" or sys.argv[1] == "-d":
            confirm = input("⚠️  Are you sure you want to delete all INFO logs for Instantel instruments? (yes/no): ").lower().strip()
            if confirm == "yes":
                delete_instantel_info_logs(dry_run=False)
            else:
                print("Deletion cancelled.")
        else:
            print("Usage:")
            print("  python delete_instantel_info_logs.py --preview  (preview only)")
            print("  python delete_instantel_info_logs.py --delete   (actual deletion)")
    else:
        print("Options:")
        print("  python delete_instantel_info_logs.py --preview  (preview only)")
        print("  python delete_instantel_info_logs.py --delete   (actual deletion)")
        print()
        
        choice = input("Do you want to preview first? (y/n): ").lower().strip()
        if choice == 'y':
            preview_instantel_info_logs()
            print()
            confirm = input("Proceed with actual deletion? (yes/no): ").lower().strip()
            if confirm == "yes":
                delete_instantel_info_logs(dry_run=False)
            else:
                print("Deletion cancelled.")
        else:
            confirm = input("⚠️  Are you sure you want to delete all INFO logs for Instantel instruments? (yes/no): ").lower().strip()
            if confirm == "yes":
                delete_instantel_info_logs(dry_run=False)
            else:
                print("Deletion cancelled.")


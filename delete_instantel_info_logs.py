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

def fetch_all_info_logs(instrument_id, batch_size=1000):
    """Fetch all INFO logs for an instrument using pagination"""
    all_logs = []
    offset = 0
    
    while True:
        try:
            response = supabase.table('sent_alert_logs') \
                .select('id, log_type, log, log_time, for_instrument') \
                .eq('for_instrument', instrument_id) \
                .eq('log_type', 'INFO') \
                .range(offset, offset + batch_size - 1) \
                .execute()
            
            batch = response.data
            if not batch:
                break
            
            all_logs.extend(batch)
            
            # If we got fewer records than batch_size, we've reached the end
            if len(batch) < batch_size:
                break
            
            offset += batch_size
            
            # Progress indicator
            if offset % 5000 == 0:
                print(f"    Fetched {len(all_logs)} records so far...")
                
        except Exception as e:
            print(f"    ⚠️  Error fetching batch at offset {offset}: {e}")
            break
    
    return all_logs

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
            # Fetch all INFO logs for this instrument using pagination
            print(f"  Fetching all INFO logs (this may take a moment)...")
            logs = fetch_all_info_logs(instrument_id)
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
                # Use direct filter-based deletion (most efficient)
                # This deletes all records matching the filter in one operation
                print(f"  Deleting {len(logs)} INFO log records...")
                
                try:
                    # Delete all INFO logs for this instrument using filters
                    delete_response = supabase.table('sent_alert_logs') \
                        .delete() \
                        .eq('for_instrument', instrument_id) \
                        .eq('log_type', 'INFO') \
                        .execute()
                    
                    # Count deleted records
                    deleted_count = len(delete_response.data) if delete_response.data else 0
                    
                    print(f"  ✅ Deleted {deleted_count} INFO log records for {instrument_id}")
                    total_deleted += deleted_count
                    
                except Exception as e:
                    print(f"  ⚠️  Error with bulk deletion: {e}")
                    print(f"  Falling back to individual deletions...")
                    
                    # Fallback: delete one by one
                    deleted_count = 0
                    batch_size = 100
                    total_batches = (len(logs) + batch_size - 1) // batch_size
                    
                    for batch_num in range(total_batches):
                        start_idx = batch_num * batch_size
                        end_idx = min(start_idx + batch_size, len(logs))
                        batch = logs[start_idx:end_idx]
                        
                        for log in batch:
                            try:
                                delete_response = supabase.table('sent_alert_logs') \
                                    .delete() \
                                    .eq('id', log['id']) \
                                    .execute()
                                if delete_response.data:
                                    deleted_count += 1
                            except Exception as e2:
                                print(f"      ⚠️  Failed to delete log ID {log['id']}: {e2}")
                        
                        # Progress indicator
                        if (batch_num + 1) % 10 == 0 or batch_num == total_batches - 1:
                            print(f"    Progress: {batch_num + 1}/{total_batches} batches ({deleted_count}/{len(logs)} deleted)")
                    
                    print(f"  ✅ Deleted {deleted_count} INFO log records for {instrument_id} (individual method)")
                    total_deleted += deleted_count
            else:
                print(f"  🔍 Would delete {len(logs)} INFO log records for {instrument_id}")
                total_deleted += len(logs)
                
        except Exception as e:
            print(f"  ❌ Error processing {instrument_id}: {e}")
            import traceback
            traceback.print_exc()
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


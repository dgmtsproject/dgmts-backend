#!/usr/bin/env python3
"""
Cleanup script to remove exact duplicate tiltmeter readings
Removes records with identical node_id, timestamp, x_value, y_value, z_value
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
from config import Config

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

def cleanup_exact_duplicates():
    """Remove exact duplicate readings (same node_id, timestamp, x_value, y_value, z_value)"""
    
    print("🧹 Starting exact duplicate cleanup...")
    print("=" * 50)
    
    # Calculate cutoff time (2 days ago)
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=2)
    cutoff_str = cutoff_time.isoformat()
    print(f"📅 Processing readings from: {cutoff_str} onwards")
    
    # Nodes to clean up
    nodes_to_clean = [142939, 143969]
    
    total_deleted = 0
    
    for node_id in nodes_to_clean:
        print(f"\n📊 Processing Node {node_id}...")
        
        try:
            # Get readings from the past 2 days only
            print(f"  Fetching readings from past 2 days for node {node_id}...")
            response = supabase.table('sensor_readings') \
                .select('*') \
                .eq('node_id', node_id) \
                .gte('timestamp', cutoff_str) \
                .order('timestamp', desc=False) \
                .execute()
            
            readings = response.data
            print(f"  Found {len(readings)} readings from past 2 days")
            
            if not readings:
                print(f"  No recent readings found for node {node_id}")
                continue
            
            # Group readings by exact match (timestamp + all values)
            exact_groups = {}
            for reading in readings:
                timestamp = reading['timestamp']
                x_val = reading['x_value']
                y_val = reading['y_value']
                z_val = reading['z_value']
                
                # Create a unique key for exact duplicates
                exact_key = f"{timestamp}|{x_val}|{y_val}|{z_val}"
                
                if exact_key not in exact_groups:
                    exact_groups[exact_key] = []
                exact_groups[exact_key].append(reading)
            
            print(f"  Grouped into {len(exact_groups)} exact duplicate groups")
            
            # For each exact duplicate group, keep the first reading and delete the rest
            deleted_count = 0
            for exact_key, duplicate_readings in exact_groups.items():
                if len(duplicate_readings) > 1:
                    timestamp = duplicate_readings[0]['timestamp']
                    print(f"    Exact duplicates at {timestamp}: Found {len(duplicate_readings)} identical readings")
                    
                    # Keep the first reading (lowest ID)
                    keep_reading = min(duplicate_readings, key=lambda x: x['id'])
                    readings_to_delete = [r for r in duplicate_readings if r['id'] != keep_reading['id']]
                    
                    print(f"    Keeping reading ID {keep_reading['id']}")
                    
                    # Delete the duplicate readings
                    for reading_to_delete in readings_to_delete:
                        try:
                            delete_response = supabase.table('sensor_readings') \
                                .delete() \
                                .eq('id', reading_to_delete['id']) \
                                .execute()
                            
                            if delete_response.data:
                                deleted_count += 1
                                print(f"    Deleted duplicate reading ID {reading_to_delete['id']}")
                            else:
                                print(f"    ⚠️  Failed to delete reading ID {reading_to_delete['id']}")
                                
                        except Exception as e:
                            print(f"    ⚠️  Error deleting reading ID {reading_to_delete['id']}: {e}")
                
                else:
                    print(f"    No exact duplicates found")
            
            print(f"  ✅ Node {node_id}: Deleted {deleted_count} exact duplicate readings")
            total_deleted += deleted_count
            
        except Exception as e:
            print(f"  ❌ Error processing node {node_id}: {e}")
            continue
    
    print("\n" + "=" * 50)
    print(f"🎉 Exact duplicate cleanup completed!")
    print(f"📊 Total exact duplicate readings deleted: {total_deleted}")
    print(f"📅 Cleanup timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"📅 Processed readings from: {cutoff_str} onwards")
    
    return total_deleted

def preview_exact_duplicates():
    """Preview what would be deleted without actually deleting"""
    
    print("👀 Previewing exact duplicate cleanup...")
    print("=" * 50)
    
    # Calculate cutoff time (2 days ago)
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=2)
    cutoff_str = cutoff_time.isoformat()
    print(f"📅 Would process readings from: {cutoff_str} onwards")
    
    nodes_to_clean = [142939, 143969]
    total_would_delete = 0
    
    for node_id in nodes_to_clean:
        print(f"\n📊 Node {node_id} Preview:")
        
        try:
            response = supabase.table('sensor_readings') \
                .select('*') \
                .eq('node_id', node_id) \
                .gte('timestamp', cutoff_str) \
                .order('timestamp', desc=False) \
                .execute()
            
            readings = response.data
            print(f"  Found {len(readings)} readings from past 2 days")
            
            if not readings:
                continue
            
            # Group by exact match
            exact_groups = {}
            for reading in readings:
                timestamp = reading['timestamp']
                x_val = reading['x_value']
                y_val = reading['y_value']
                z_val = reading['z_value']
                
                exact_key = f"{timestamp}|{x_val}|{y_val}|{z_val}"
                
                if exact_key not in exact_groups:
                    exact_groups[exact_key] = []
                exact_groups[exact_key].append(reading)
            
            # Count duplicates
            would_delete_count = 0
            for exact_key, duplicate_readings in exact_groups.items():
                if len(duplicate_readings) > 1:
                    duplicates = len(duplicate_readings) - 1
                    would_delete_count += duplicates
                    timestamp = duplicate_readings[0]['timestamp']
                    print(f"    Exact duplicates at {timestamp}: {len(duplicate_readings)} readings → would delete {duplicates}")
            
            print(f"  Would delete {would_delete_count} exact duplicate readings")
            total_would_delete += would_delete_count
            
        except Exception as e:
            print(f"  ❌ Error previewing node {node_id}: {e}")
    
    print(f"\n📊 Total exact duplicates that would be deleted: {total_would_delete}")
    print(f"📅 Would process readings from: {cutoff_str} onwards")
    return total_would_delete

if __name__ == "__main__":
    print("🔧 Exact Duplicate Cleanup Script")
    print("This script will remove exact duplicate tiltmeter readings from the past 2 days")
    print("Duplicates are records with identical node_id, timestamp, x_value, y_value, z_value")
    print()
    
    # Ask user for confirmation
    if len(sys.argv) > 1 and sys.argv[1] == "--preview":
        preview_exact_duplicates()
    else:
        print("Options:")
        print("  python cleanup_exact_duplicates.py --preview  (preview only)")
        print("  python cleanup_exact_duplicates.py            (actual cleanup)")
        print()
        
        choice = input("Do you want to preview first? (y/n): ").lower().strip()
        if choice == 'y':
            preview_exact_duplicates()
            print()
            confirm = input("Proceed with actual cleanup? (y/n): ").lower().strip()
            if confirm == 'y':
                cleanup_exact_duplicates()
            else:
                print("Cleanup cancelled.")
        else:
            confirm = input("Proceed with cleanup? (y/n): ").lower().strip()
            if confirm == 'y':
                cleanup_exact_duplicates()
            else:
                print("Cleanup cancelled.")

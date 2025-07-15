"""
Database Migration Script
Adds VIX column to existing market data tables
"""

import sqlite3
import os

def migrate_add_vix_column(db_path='db/trading_bot.db'):
    """
    Adds vix_value column to existing market data tables.
    This preserves existing data while adding the new column.
    """
    if not os.path.exists(db_path):
        print(f"Database {db_path} does not exist. Creating new database with VIX columns...")
        from utils.db_setup import create_tables
        create_tables(db_path)
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Tables that need VIX column
    tables = ['market_data_1m', 'market_data_5m', 'market_data_15m']
    
    for table in tables:
        try:
            # Check if table exists
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if cursor.fetchone():
                # Check if vix_value column already exists
                cursor.execute(f"PRAGMA table_info({table})")
                columns = [column[1] for column in cursor.fetchall()]
                
                if 'vix_value' not in columns:
                    # Add vix_value column
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN vix_value REAL")
                    print(f"✓ Added vix_value column to {table}")
                else:
                    print(f"✓ {table} already has vix_value column")
            else:
                print(f"⚠ Table {table} does not exist yet")
                
        except Exception as e:
            print(f"✗ Error migrating {table}: {e}")
    
    conn.commit()
    conn.close()
    print("\nMigration completed!")

def verify_migration(db_path='db/trading_bot.db'):
    """
    Verifies that all market data tables have the vix_value column.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    tables = ['market_data_1m', 'market_data_5m', 'market_data_15m']
    
    print("\nVerifying migration:")
    for table in tables:
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'vix_value' in columns:
                print(f"✓ {table}: vix_value column present")
            else:
                print(f"✗ {table}: vix_value column missing")
                
        except Exception as e:
            print(f"✗ {table}: Error checking table - {e}")
    
    conn.close()

if __name__ == "__main__":
    print("Running database migration to add VIX columns...")
    migrate_add_vix_column()
    verify_migration() 
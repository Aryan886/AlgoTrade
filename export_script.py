import sqlite3
import pandas as pd
import os

def export_all_tables_to_csv(db_path='db/trading_bot.db', export_dir='exports'):
    # Create export directory if not exists
    os.makedirs(export_dir, exist_ok=True)

    # Connect to the database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Fetch all user-defined tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [row[0] for row in cursor.fetchall()]

    print(f"Found tables: {tables}")

    for table in tables:
        try:
            df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
            export_path = os.path.join(export_dir, f"{table}.csv")
            df.to_csv(export_path, index=False)
            print(f"Exported '{table}' to '{export_path}'")
        except Exception as e:
            print(f"Failed to export {table}: {e}")

    conn.close()
    print("\nAll tables exported successfully!!!!")

# Run the export
if __name__ == "__main__":
    export_all_tables_to_csv()

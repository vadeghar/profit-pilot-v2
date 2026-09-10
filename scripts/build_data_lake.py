import duckdb
import os
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

_db_raw = os.getenv("DUCKDB_PATH", os.path.join("data", "market_data.duckdb"))
# Relative paths are resolved against the project root, not the CWD,
# so the scripts work from any working directory.
db_path = _db_raw if os.path.isabs(_db_raw) or os.path.splitdrive(_db_raw)[0] \
    else os.path.join(PROJECT_ROOT, _db_raw)
con = duckdb.connect(db_path)

# Point to the directory containing your zipped CSV files
zip_path = r"D:\ProfitPilot\data\options\*.zip"

print("Indexing Nifty Options Zip Files into DuckDB...")

# Auto-detect CSV schemas inside zip archives
con.execute(f"""
    CREATE TABLE IF NOT EXISTS nifty_options AS 
    SELECT * FROM read_csv_auto('{zip_path.replace('\\', '/')}');
""")

print("Data Lake Built Successfully!")
print("Sample Data:")
print(con.execute("SELECT * FROM nifty_options LIMIT 5").df())
con.close()
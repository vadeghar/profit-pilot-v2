import os
import glob
import shutil
import tempfile
import zipfile

import duckdb
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

DATA_DIR = os.path.join(PROJECT_ROOT, "data", "raw")
_db_raw = os.getenv("DUCKDB_PATH", os.path.join("data", "market_data.duckdb"))
# Relative paths are resolved against the project root, not the CWD,
# so the scripts work from any working directory.
DB_PATH = _db_raw if os.path.isabs(_db_raw) or os.path.splitdrive(_db_raw)[0] \
    else os.path.join(PROJECT_ROOT, _db_raw)


def init_database(con):
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
    except Exception as e:
        print(f"Warning: could not load spatial extension ({e}) - continuing without it.")
    con.execute("DROP TABLE IF EXISTS options_ticks")
    con.execute("""
        CREATE TABLE options_ticks (
            trade_time TIMESTAMP,
            trade_date DATE,
            expiry_date DATE,
            strike_price INT,
            option_type VARCHAR(2),
            price DOUBLE,
            volume BIGINT,
            open_interest BIGINT,
            iv DOUBLE,
            delta DOUBLE,
            gamma DOUBLE,
            theta DOUBLE,
            vega DOUBLE
        )
    """)
    print("Database table 'options_ticks' initialized successfully.")


# Filenames inside the zips that hold NIFTY spot (index) candles rather than
# option-chain data. These are excluded from options_ticks ingestion.
SPOT_FILENAMES = {"nifty_spot.csv", "nifty spot.csv", "spot.csv"}


def extract_csvs_to_staging(zip_path, staging_dir):
    """Extract every CSV entry from a zip to `staging_dir`, preserving the
    archive's internal folder structure so the folder/date remains readable.

    Skips spot/index files (e.g. nifty_spot.csv) -- those are ingested
    separately into the `nifty_spot` table with their full OHLC columns.
    """
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            if not member.filename.lower().endswith(".csv"):
                continue
            if os.path.basename(member.filename).lower() in SPOT_FILENAMES:
                continue
            # member.filename uses forward slashes; normalise to the OS separator
            rel = member.filename.replace("/", os.sep)
            dest = os.path.join(staging_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


INSERT_SQL = """
    INSERT INTO options_ticks (
        trade_time,
        trade_date,
        expiry_date,
        strike_price,
        option_type,
        price,
        volume,
        open_interest
    )
    SELECT
        timestamp::TIMESTAMP AS trade_time,
        timestamp::DATE AS trade_date,
        COALESCE(
            strptime(regexp_extract(filename, '[/\\\\](\\d{8})[/\\\\]', 1), '%Y%m%d')::DATE,
            timestamp::DATE
        ) AS expiry_date,
        strike::INT AS strike_price,
        option_type,
        close::DOUBLE AS price,
        volume::BIGINT AS volume,
        oi::BIGINT AS open_interest
    FROM read_csv_auto(?, filename=True, union_by_name=True)
"""


def ingest_all_zips(con, data_dir):
    zip_files = glob.glob(os.path.join(data_dir, "*.zip"))

    if not zip_files:
        print(f"No ZIP files found in {data_dir}")
        return

    print(f"Found {len(zip_files)} ZIP file(s) to process...")

    for zip_path in zip_files:
        zip_name = os.path.basename(zip_path)
        print(f"Ingesting: {zip_name} ...")

        # DuckDB's virtual /vsizip/ reader is unreliable on Windows (returns
        # "No files found"), so we stage the CSVs to a temp dir first and read
        # them from the local filesystem with read_csv_auto + union_by_name.
        staging_dir = tempfile.mkdtemp(prefix="ingest_stage_", dir=data_dir)
        try:
            n_csvs = extract_csvs_to_staging(zip_path, staging_dir)
            if n_csvs == 0:
                print(f"  No CSV files found inside {zip_name}; skipping.")
                continue

            csv_glob = os.path.join(staging_dir, "**", "*.csv").replace("\\", "/")
            try:
                con.execute(INSERT_SQL, [csv_glob])
                print(f"  Successfully ingested {n_csvs} CSV file(s) from {zip_name}")
            except Exception as e:
                print(f"  Error ingesting {zip_name}: {e}")
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)


def main():
    con = duckdb.connect(DB_PATH)
    try:
        init_database(con)
        ingest_all_zips(con, DATA_DIR)
        print("\nETL Process Completed Successfully!")
    finally:
        con.close()


if __name__ == "__main__":
    main()
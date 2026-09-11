import os
import glob
import shutil
import tempfile
import zipfile
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import sleep

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

WORKERS = 2
logger = logging.getLogger(__name__)


def init_database(con):
    try:
        con.execute("INSTALL spatial; LOAD spatial;")
    except Exception as e:
        print(f"Warning: could not load spatial extension ({e}) - continuing without it.")
    # Older ingestions may have created options_ticks without OHLC columns.
    # Keep this migration additive so the repair path below works on them too.
    for column in ("open", "high", "low"):
        con.execute(f"ALTER TABLE options_ticks ADD COLUMN IF NOT EXISTS {column} DOUBLE")
    # con.execute("DROP TABLE IF EXISTS options_ticks")
    # con.execute("""
    #     CREATE TABLE IF NOT EXISTS options_ticks (
    #         trade_time TIMESTAMP,
    #         trade_date DATE,
    #         expiry_date DATE,
    #         strike_price INT,
    #         option_type VARCHAR(2),
    #         price DOUBLE,
    #         volume BIGINT,
    #         open_interest BIGINT,
    #         iv DOUBLE,
    #         delta DOUBLE,
    #         gamma DOUBLE,
    #         theta DOUBLE,
    #         vega DOUBLE
    #     )
    # """)
    # print("Database table 'options_ticks' initialized successfully.")


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
        open,
        high,
        low,
        close,
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
        open::DOUBLE AS open,
        high::DOUBLE AS high,
        low::DOUBLE AS low,
        close::DOUBLE AS close,
        volume::BIGINT AS volume,
        oi::BIGINT AS open_interest
    FROM read_csv_auto(?, filename=True, union_by_name=True)
"""


NORMALIZED_CSV_SQL = """
    SELECT
        timestamp::TIMESTAMP AS trade_time,
        timestamp::DATE AS trade_date,
        COALESCE(
            strptime(regexp_extract(filename, '[/\\\\](\\d{8})[/\\\\]', 1), '%Y%m%d')::DATE,
            timestamp::DATE
        ) AS expiry_date,
        strike::INT AS strike_price,
        option_type,
        open::DOUBLE AS open,
        high::DOUBLE AS high,
        low::DOUBLE AS low,
        close::DOUBLE AS close,
        volume::BIGINT AS volume,
        oi::BIGINT AS open_interest
    FROM read_csv_auto(?, filename=True, union_by_name=True)
"""


def ingest_csv(con, csv_path):
    """Repair matching rows, then insert rows not already present.

    The match includes the complete option candle identity. COALESCE makes the
    repair idempotent and protects already-populated values from replacement.
    """
    con.execute("CREATE OR REPLACE TEMP TABLE incoming_options AS "
                + NORMALIZED_CSV_SQL, [csv_path])
    try:
        missing_before = con.execute("""
            SELECT COUNT(*)
            FROM options_ticks t
            JOIN incoming_options i
              ON t.trade_time = i.trade_time
             AND t.expiry_date = i.expiry_date
             AND t.strike_price = i.strike_price
             AND t.option_type = i.option_type
            WHERE (t.open IS NULL AND i.open IS NOT NULL)
               OR (t.high IS NULL AND i.high IS NOT NULL)
               OR (t.low IS NULL AND i.low IS NOT NULL)
        """).fetchone()[0]
        con.execute("""
            UPDATE options_ticks AS t
            SET open = COALESCE(t.open, i.open),
                high = COALESCE(t.high, i.high),
                low = COALESCE(t.low, i.low),
                close = COALESCE(t.close, i.close)
            FROM incoming_options AS i
            WHERE t.trade_time = i.trade_time
              AND t.expiry_date = i.expiry_date
              AND t.strike_price = i.strike_price
              AND t.option_type = i.option_type
        """)
        con.execute("""
            INSERT INTO options_ticks (
                trade_time, trade_date, expiry_date, strike_price, option_type,
                open, high, low, close, volume, open_interest
            )
            SELECT i.trade_time, i.trade_date, i.expiry_date, i.strike_price,
                   i.option_type, i.open, i.high, i.low, i.close, i.volume,
                   i.open_interest
            FROM incoming_options i
            ANTI JOIN options_ticks t
              ON t.trade_time = i.trade_time
             AND t.expiry_date = i.expiry_date
             AND t.strike_price = i.strike_price
             AND t.option_type = i.option_type
        """)
        return missing_before
    finally:
        con.execute("DROP TABLE incoming_options")


def ingest_csv_worker(csv_path, worker_id):
    """Process one CSV using an isolated DuckDB connection.

    DuckDB permits concurrent connections, but concurrent updates can briefly
    conflict. Retry those short-lived conflicts instead of losing a sheet.
    """
    name = os.path.basename(csv_path)
    for attempt in range(1, 4):
        con = duckdb.connect(DB_PATH)
        try:
            repaired = ingest_csv(con, csv_path)
            logger.info("worker-%d completed %s (repaired=%d)",
                        worker_id, name, repaired)
            return repaired
        except Exception as exc:
            message = str(exc).lower()
            transient = any(x in message for x in
                            ("conflict", "busy", "locked", "transaction"))
            if not transient or attempt == 3:
                logger.exception("worker-%d failed %s", worker_id, name)
                raise
            logger.warning("worker-%d retrying %s after transient DuckDB "
                           "conflict (%d/3)", worker_id, name, attempt)
            sleep(attempt)
        finally:
            con.close()


def print_ohlc_summary(con):
    row = con.execute("""
        SELECT COUNT(*) AS total_rows,
               COUNT(*) FILTER (WHERE open IS NULL) AS empty_open,
               COUNT(*) FILTER (WHERE close IS NULL) AS empty_close,
               COUNT(*) FILTER (WHERE high IS NULL) AS empty_high,
               COUNT(*) FILTER (WHERE low IS NULL) AS empty_low
        FROM options_ticks
    """).fetchone()
    print("\\nOPTIONS_TICKS EMPTY VALUE SUMMARY")
    print(f"  total rows : {row[0]}")
    print(f"  empty open : {row[1]}")
    print(f"  empty close : {row[2]}")
    print(f"  empty high : {row[3]}")
    print(f"  empty low  : {row[4]}")


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

            try:
                csv_files = glob.glob(os.path.join(staging_dir, "**", "*.csv"), recursive=True)
                repaired = 0
                with ThreadPoolExecutor(max_workers=WORKERS,
                                        thread_name_prefix="options-ingest") as pool:
                    futures = {
                        pool.submit(ingest_csv_worker, path, i % WORKERS + 1): path
                        for i, path in enumerate(csv_files)
                    }
                    for completed, future in enumerate(as_completed(futures), 1):
                        repaired += future.result()
                        logger.info("%s progress: %d/%d sheets complete",
                                    zip_name, completed, len(futures))
                print(f"  Successfully processed {n_csvs} CSV file(s) from {zip_name}; "
                      f"repaired {repaired} matching row(s)")
            except Exception as e:
                print(f"  Error ingesting {zip_name}: {e}")
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    con = duckdb.connect(DB_PATH)
    try:
        init_database(con)
        ingest_all_zips(con, DATA_DIR)
        print_ohlc_summary(con)
        print("\nETL Process Completed Successfully!")
    finally:
        con.close()


if __name__ == "__main__":
    main()

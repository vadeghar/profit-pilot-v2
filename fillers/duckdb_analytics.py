"""
duckdb_analytics.py
-------------------
Analytics / inspection utilities for the local DuckDB market data warehouse
(market_data.duckdb).

Usage:
    python duckdb_analytics.py                 # run the full report
    python duckdb_analytics.py --db path.duckdb
    python duckdb_analytics.py --table options_ticks
    python duckdb_analytics.py --preview       # show sample rows per table
    python duckdb_analytics.py --csv out_dir   # also dump report tables to CSV

No other files in the codebase are modified by this script.
"""

import argparse
import os
import sys
from datetime import datetime

import duckdb

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
# DEFAULT_DB_PATH = os.path.join(BASE_DIR, "market_data.duckdb")
#  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_data.duckdb")

DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "market_data.duckdb")


# --------------------------------------------------------------------------- #
# Connection helpers
# --------------------------------------------------------------------------- #
# def connect(db_path: str) -> duckdb.DuckDBPyConnection:
#     if not os.path.exists(db_path):
#         raise FileNotFoundError(f"DuckDB file not found: {db_path}")
#     # read_only=True so analytics never risks mutating the warehouse
#     return duckdb.connect(database=db_path, read_only=True)

def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    is_remote = db_path.startswith("http://") or db_path.startswith("https://")
    
    if is_remote:
        # Pass :memory: so DuckDB doesn't invoke OS file checks
        con = duckdb.connect(database=":memory:")
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute(f"ATTACH '{db_path}' AS remote_db (READ_ONLY);")
        con.execute("USE remote_db;")
        return con
    else:
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"DuckDB file not found: {db_path}")
        return duckdb.connect(database=db_path, read_only=True)


def get_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    ).fetchall()
    return [r[0] for r in rows]


def get_views(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        """
        SELECT view_name
        FROM duckdb_views()
        WHERE internal = false
        ORDER BY view_name
        """
    ).fetchall()
    return [r[0] for r in rows]



# --------------------------------------------------------------------------- #
# Individual analytics sections
# --------------------------------------------------------------------------- #
def db_overview(con: duckdb.DuckDBPyConnection, db_path: str) -> None:
    if db_path.startswith("http://") or db_path.startswith("https://"):
        size_bytes = 0  # Or fetch remote size using `requests.head()` if needed
    else:
        size_bytes = os.path.getsize(db_path)
    # size_bytes = os.path.getsize(db_path)
    version = con.execute("SELECT version()").fetchone()[0]
    print("\n" + "=" * 80)
    print("DATABASE OVERVIEW")
    print("=" * 80)
    print(f"File        : {db_path}")
    print(f"Size        : {size_bytes / (1024 * 1024):.2f} MB")
    print(f"DuckDB ver  : {version}")
    print(f"Generated   : {datetime.now():%Y-%m-%d %H:%M:%S}")


def summary_counts(con: duckdb.DuckDBPyConnection, tables: list[str]) -> None:
    print("\n" + "=" * 80)
    print("TABLE COUNTS (tables + row counts)")
    print("=" * 80)
    print(f"Total tables: {len(tables)}")

    print(f"\n{'table':<30}{'rows':>15}{'date columns':>18}")
    print("-" * 65)
    for t in tables:
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        date_cols = get_date_columns(con, t)
        print(f"{t:<30}{n:>15,}{', '.join(date_cols) or '-':>18}")


def get_date_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    rows = con.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'main' AND table_name = ?
          AND (upper(data_type) LIKE '%DATE%'
               OR upper(data_type) LIKE '%TIMESTAMP%')
        ORDER BY ordinal_position
        """, [table]).fetchall()
    return [r[0] for r in rows]


def table_data_ranges(con: duckdb.DuckDBPyConnection, tables: list[str]) -> None:
    """Print row count and min/max for every date-like column in every table."""
    print("\n" + "=" * 80)
    print("TABLE DATA COVERAGE (date/timestamp columns)")
    print("=" * 80)
    print(f"{'table':<28}{'column':<24}{'from':<24}{'to':<24}")
    print("-" * 100)
    for table in tables:
        columns = get_date_columns(con, table)
        if not columns:
            print(f"{table:<28}{'-':<24}{'no date-like column':<24}")
            continue
        for column in columns:
            q_table, q_column = f'"{table}"', f'"{column}"'
            lo, hi = con.execute(
                f'SELECT MIN({q_column}), MAX({q_column}) FROM {q_table}'
            ).fetchone()
            print(f"{table:<28}{column:<24}{str(lo):<24}{str(hi):<24}")


def table_quality_summary(con: duckdb.DuckDBPyConnection, tables: list[str]) -> None:
    """Show total rows and rows containing any NULL, useful for completeness checks."""
    print("\n" + "=" * 80)
    print("TABLE DATA QUALITY")
    print("=" * 80)
    print(f"{'table':<30}{'rows':>15}{'rows with NULL':>20}{'complete %':>15}")
    print("-" * 82)
    for table in tables:
        cols = con.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = 'main' AND table_name = ?
               ORDER BY ordinal_position""", [table]).fetchall()
        names = [f'"{r[0]}"' for r in cols]
        if not names:
            continue
        null_predicate = " OR ".join(f"{c} IS NULL" for c in names)
        total, null_rows = con.execute(
            f'SELECT COUNT(*), COUNT(*) FILTER (WHERE {null_predicate}) FROM "{table}"'
        ).fetchone()
        complete = ((total - null_rows) * 100.0 / total) if total else 100.0
        print(f"{table:<30}{total:>15,}{null_rows:>20,}{complete:>14.2f}%")


def table_columns(con: duckdb.DuckDBPyConnection, tables: list[str]) -> None:
    for t in tables:
        cols = con.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = ?
            ORDER BY ordinal_position
            """,
            [t],
        ).fetchall()
        print(f"\n--- {t} ({len(cols)} columns) " + "-" * (50 - len(t)))
        for name, dtype, nullable in cols:
            print(f"  {name:<24}{dtype:<14}{nullable}")


def table_summary(con: duckdb.DuckDBPyConnection, t: str) -> None:
    """Generic column-wise summary: nulls, distinct, min, max (type-aware)."""
    cols = con.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'main' AND table_name = ?
        ORDER BY ordinal_position
        """,
        [t],
    ).fetchall()

    print("\n" + "=" * 80)
    print(f"TABLE SUMMARY: {t}")
    print("=" * 80)
    print(f"{'column':<24}{'type':<14}{'nulls':>10}{'distinct':>10}  min / max")
    print("-" * 110)
    for name, dtype in cols:
        q = f'"{name}"'
        nulls = con.execute(f'SELECT COUNT(*) - COUNT({q}) FROM "{t}"').fetchone()[0]
        distinct = con.execute(f'SELECT COUNT(DISTINCT {q}) FROM "{t}"').fetchone()[0]
        minmax = ""
        if any(x in dtype.upper() for x in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "DATE", "TIMESTAMP")):
            lo, hi = con.execute(f'SELECT MIN({q}), MAX({q}) FROM "{t}"').fetchone()
            minmax = f"{lo} / {hi}"
        print(f"{name:<24}{dtype:<14}{nulls:>10,}{distinct:>10,}  {minmax}")


def table_preview(con: duckdb.DuckDBPyConnection, t: str, limit: int = 5) -> None:
    print(f"\n--- {t}: sample rows (limit {limit}) ---")
    try:
        df = con.execute(f'SELECT * FROM "{t}" LIMIT {limit}').fetch_df()
        print(df.to_string(index=False))
    except Exception as e:
        print(f"  (preview failed: {e})")


def extra_analytics(con: duckdb.DuckDBPyConnection, tables: list[str]) -> None:
    """Domain-specific highlights for the known market-data tables."""
    print("\n" + "=" * 80)
    print("MARKET DATA HIGHLIGHTS")
    print("=" * 80)

    if "options_ticks" in tables:
        try:
            row = con.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT expiry_date),
                       MIN(trade_time), MAX(trade_time),
                       COUNT(DISTINCT trade_date),
                       COUNT(DISTINCT strike_price)
                FROM options_ticks
                """
            ).fetchone()
            print(f"options_ticks : {row[0]:,} ticks | {row[1]} expiries | "
                  f"{row[2]} -> {row[3]} | {row[4]} trading days | {row[5]} strikes")
        except Exception as e:
            print(f"options_ticks : (could not compute: {e})")

    for t, date_col in (("nifty_spot", "trade_date"),
                        ("expiry_calendar", "expiry_date"),
                        ("holiday_calendar", "holiday_date")):
        if t in tables:
            try:
                row = con.execute(
                    f'SELECT COUNT(*), MIN("{date_col}"), MAX("{date_col}") FROM "{t}"'
                ).fetchone()
                print(f"{t:<15}: {row[0]:,} rows | {row[1]} -> {row[2]}")
            except Exception as e:
                print(f"{t:<15}: (could not compute: {e})")


def export_csv(con: duckdb.DuckDBPyConnection, tables: list[str], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # full column catalog report
    cols = con.execute(
        """
        SELECT table_name, ordinal_position, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'main'
        ORDER BY table_name, ordinal_position
        """
    ).fetch_df()
    cols.to_csv(os.path.join(out_dir, f"columns_{stamp}.csv"), index=False)

    # per-table sample dumps
    for t in tables:
        df = con.execute(f'SELECT * FROM "{t}" LIMIT 10000').fetch_df()
        safe = t.replace("/", "_")
        df.to_csv(os.path.join(out_dir, f"{safe}_sample_{stamp}.csv"), index=False)
    print(f"\nCSV exports written to: {os.path.abspath(out_dir)}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="DuckDB market data analytics")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to duckdb file")
    parser.add_argument("--table", help="Only analyze this table")
    parser.add_argument("--csv", metavar="DIR", help="Export report tables to CSV in DIR")
    parser.add_argument("--preview", action="store_true", help="Show sample rows per table")
    args = parser.parse_args()

    con = connect(args.db)
    try:
        db_overview(con, args.db)

        tables = get_tables(con)
        views = get_views(con)
        print(f"Tables: {tables}")
        if views:
            print(f"Views : {views}")

        if args.table:
            if args.table not in tables:
                print(f"Table '{args.table}' not found. Available: {tables}")
                return 1
            tables = [args.table]

        summary_counts(con, tables)
        table_data_ranges(con, tables)
        table_quality_summary(con, tables)
        table_columns(con, tables)

        for t in tables:
            table_summary(con, t)
            if args.preview:
                table_preview(con, t)

        extra_analytics(con, tables)

        if args.csv:
            export_csv(con, tables, args.csv)

        print("\nDone.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())

"""
Database Connection Provider
Manages DuckDB connections for the API.
Supports both local files and remote URLs (GitHub Releases, S3, etc).
"""
import os
import tempfile
import duckdb
from fastapi import HTTPException

# Track whether httpfs is loaded for remote connections
_httpfs_loaded = False


def _get_db_path() -> str:
    """Resolve the database path from env var or default."""
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _db_raw = os.getenv("DUCKDB_PATH", os.path.join("data", "market_data.duckdb"))
    # Return as-is if it's a remote URL or an absolute local path
    if _db_raw.startswith("http://") or _db_raw.startswith("https://"):
        return _db_raw
    return _db_raw if os.path.isabs(_db_raw) or os.path.splitdrive(_db_raw)[0] \
        else os.path.join(PROJECT_ROOT, _db_raw)


def _is_remote(path: str) -> bool:
    """Check if the database path is a remote URL."""
    return path.startswith("http://") or path.startswith("https://")


def _load_httpfs(con: duckdb.DuckDBPyConnection) -> None:
    """Load httpfs extension for remote database access."""
    global _httpfs_loaded
    if not _httpfs_loaded:
        # Some server/container environments run with HOME unset. DuckDB then
        # cannot determine where to cache extensions and fails with:
        # "Can't find the home directory at ''". Allow an explicit directory,
        # otherwise use a writable temp location.
        home_dir = os.getenv("DUCKDB_HOME") or os.path.join(
            tempfile.gettempdir(), "profit-pilot-duckdb"
        )
        os.makedirs(home_dir, exist_ok=True)
        escaped_home = home_dir.replace("'", "''")
        con.execute(f"SET home_directory = '{escaped_home}'")
        con.execute("INSTALL httpfs; LOAD httpfs;")
        _httpfs_loaded = True


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """
    Create a DuckDB connection (local or remote).

    For local files: connects directly with read_only mode.
    For remote URLs: uses :memory: + httpfs to attach the remote database.

    Args:
        read_only: Whether to open in read_only mode (default: True).

    Returns:
        duckdb.DuckDBPyConnection: Configured database connection.

    Raises:
        HTTPException: 500 error if connection fails.
    """
    DB_PATH = _get_db_path()
    try:
        if _is_remote(DB_PATH):
            con = duckdb.connect(database=":memory:")
            _load_httpfs(con)
            con.execute(f"ATTACH '{DB_PATH}' AS remote_db (READ_ONLY);")
            con.execute("USE remote_db;")
            return con
        else:
            return duckdb.connect(DB_PATH, read_only=read_only)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database connection error: {str(e)}"
        )


def get_db_connection() -> duckdb.DuckDBPyConnection:
    """
    Returns a read-only connection to the DuckDB instance.

    Returns:
        duckdb.DuckDBPyConnection: Read-only database connection.

    Raises:
        HTTPException: 500 error if connection fails.
    """
    return connect(read_only=True)


def get_db_path() -> str:
    """
    Returns the configured database path.

    Returns:
        str: Path or URL to the DuckDB database.
    """
    return _get_db_path()

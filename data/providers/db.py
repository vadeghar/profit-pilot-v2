"""
Database Connection Provider
Manages DuckDB connections for the API
"""
import os
import duckdb
from fastapi import HTTPException

# Navigate to project root (parent of data/ directory)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_db_raw = os.getenv("DUCKDB_PATH", os.path.join("data", "market_data.duckdb"))
DB_PATH = _db_raw if os.path.isabs(_db_raw) or os.path.splitdrive(_db_raw)[0] \
    else os.path.join(PROJECT_ROOT, _db_raw)


def get_db_connection():
    """
    Returns a read-only connection to the persistent DuckDB instance.
    Read-only mode prevents database locks when accessing data concurrently.
    
    Returns:
        duckdb.DuckDBPyConnection: Read-only database connection
        
    Raises:
        HTTPException: 500 error if connection fails
    """
    try:
        return duckdb.connect(DB_PATH, read_only=True)
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Database connection error: {str(e)}"
        )


def get_db_path():
    """
    Returns the configured database path.
    
    Returns:
        str: Path to the DuckDB database file
    """
    return DB_PATH

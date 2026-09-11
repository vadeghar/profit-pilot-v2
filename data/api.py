"""
ProfitPilot Data Bridge API
============================

Local REST API exposing normalized DuckDB market data and metadata.
All endpoints are served from a single FastAPI application.

Endpoints:
    System:
        - /health          - API health check

    Metadata (unchanged):
        - /meta/expiries   - Expiry calendar
        - /meta/holidays   - Trading holidays

    Data (one consolidated aggregated-OHLCV endpoint per type):
        - /equity          - Aggregated equity OHLCV candles (per symbol)
        - /spot            - Aggregated NIFTY spot OHLCV candles
        - /vix             - Aggregated VIX OHLCV candles
        - /options         - Aggregated option OHLCV candles (strike/expiry/type)

Interval aggregation:
    Every data endpoint aggregates the raw 1-minute candles into OHLCV buckets
    via the required `interval` query param, and caps the response to the 800
    most recent buckets.

    Supported values: ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, TEN_MINUTE,
    FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, ONE_DAY, WEEK, MONTH.

    Applicable to symbols (equity and NIFTY spot) plus VIX and options.
    Institutional data is NOT OHLCV candle data and is excluded.

Usage:
    uvicorn data.api:app --host 0.0.0.0 --port 8000 --reload

Interactive documentation (Scalar):
    - /docs            - Scalar API Reference (primary, replaces Swagger UI/ReDoc)
    - /scalar          - Alias to the Scalar API Reference
    - /openapi.json    - Auto-generated OpenAPI schema consumed by Scalar

The Scalar client includes a built-in "Try it out" REST client that reads the
live /openapi.json schema, so every endpoint can be executed from the browser.

Bucket alignment:
    Intraday intervals (ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, TEN_MINUTE,
    FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR) are aligned so the FIRST candle
    of a session always starts at 09:15 (NSE market open). The day/week/month
    intervals keep their calendar boundaries.

toDate behaviour:
    If `toDate` is omitted, every available candle from `fromDate` onward is
    returned (still capped at the 800-candle response limit).

Authentication:
    An `X-Authentication` header is declared as an OpenAPI security scheme
    (returned in the schema and shown in Scalar's Auth panel). It is currently
    expected to be EMPTY and NO validation is performed — reserved for a future
    authentication enhancement.
"""
import os
import urllib.parse
from fastapi import FastAPI
from dotenv import load_dotenv

from scalar_fastapi import add_scalar_reference, Theme

from .providers.db import get_db_connection, get_db_path
from .routers import spot, vix, options, meta, equity

# Load environment variables from project root .env
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# Create FastAPI application.
# The default Swagger UI (docs_url) and ReDoc (redoc_url) are disabled because
# Scalar now serves the interactive documentation on /docs instead.
app = FastAPI(
    title="ProfitPilot Data Bridge API",
    description="Local REST bridge exposing normalized DuckDB market data and metadata",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
)


# ---------------------------------------------------------------------------
# OpenAPI customization: X-Authentication security scheme (future enhancement)
# ---------------------------------------------------------------------------
# Declare an api-key header security scheme so it appears in the generated
# OpenAPI schema (and therefore in Scalar's "Auth" panel + the Try-it client).
# It is intentionally NOT enforced: no FastAPI Security dependency is wired, so
# requests will always succeed regardless of the header value (expected empty).
_original_openapi = app.openapi


def _custom_openapi():
    """Augment the auto-generated OpenAPI with the X-Authentication scheme."""
    schema = _original_openapi()
    schema.setdefault("components", {}).setdefault("securitySchemes", {})[
        "XAuthHeader"
    ] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-Authentication",
        "description": (
            "Future authentication header. Currently expected to be empty; "
            "no validation is performed."
        ),
    }
    security = schema.setdefault("security", [])
    if not any(isinstance(item, dict) and "XAuthHeader" in item for item in security):
        security.append({"XAuthHeader": []})
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


# Health check endpoint
@app.get("/health", tags=["System"])
def health_check():
    """
    Verifies API status and connectivity to DuckDB.
    
    Returns:
        dict: Health status with total row counts
    """
    con = get_db_connection()
    try:
        ticks_count = con.execute("SELECT COUNT(*) FROM options_ticks").fetchone()[0]
        spot_count = con.execute("SELECT COUNT(*) FROM nifty_spot").fetchone()[0]
        return {
            "status": "ok",
            "total_option_ticks": ticks_count,
            "total_spot_candles": spot_count
        }
    finally:
        con.close()


# Include routers
app.include_router(spot.router)
app.include_router(vix.router)
app.include_router(options.router)
app.include_router(meta.router)
app.include_router(equity.router)

# ---------------------------------------------------------------------------
# Scalar interactive API documentation
# ---------------------------------------------------------------------------
# ``add_scalar_reference`` fills in ``openapi_url`` from the app automatically,
# so Scalar dynamically reads the live /openapi.json schema. The built-in
# "Try it out" client executes requests straight from the browser.
#
# The ``authentication`` config tells Scalar to send an ``X-Authentication``
# header (empty by default - no validation), and ``persist_auth`` remembers it
# across page reloads.
_SCALAR_AUTH = {
    "preferredSecurityScheme": "XAuthHeader",
    "apiKey": {
        "name": "X-Authentication",
        "in": "header",
        "token": "",
    },
}


def _logo_data_uri(fill: str) -> str:
    """Build an inline SVG "Profit Pilot" wordmark as a data URI."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='150' height='40' "
        "viewBox='0 0 150 40'>"
        "<text x='8' y='26' font-family='Arial, sans-serif' font-size='20' "
        f"font-weight='600' fill='{fill}'>Profit Pilot</text></svg>"
    )
    return "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(svg)


# Scalar theming: swap the default logo for the "Profit Pilot" wordmark.
_SCALAR_OVERRIDES = {
    "metaData": {
        "theme": {
            "logo": {
                "dark": _logo_data_uri("#ffffff"),
                "light": _logo_data_uri("#1b1b1b"),
            }
        }
    }
}

add_scalar_reference(
    app,
    route="/docs",
    title="ProfitPilot Data Bridge API — API Documentation",
    theme=Theme.KEPLER,
    dark_mode=True,
    layout="modern",
    persist_auth=True,
    authentication=_SCALAR_AUTH,
    overrides=_SCALAR_OVERRIDES,
)

# Convenience alias on /scalar (same Scalar reference).
add_scalar_reference(
    app,
    route="/scalar",
    title="ProfitPilot Data Bridge API — Scalar Reference",
    theme=Theme.KEPLER,
    dark_mode=True,
    layout="modern",
    persist_auth=True,
    authentication=_SCALAR_AUTH,
    overrides=_SCALAR_OVERRIDES,
)

if __name__ == "__main__":
    import uvicorn
    # Bind to 0.0.0.0 so external machines on your local network can query it
    uvicorn.run("data.api:app", host="0.0.0.0", port=8000, reload=True)

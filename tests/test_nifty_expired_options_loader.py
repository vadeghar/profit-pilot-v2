from datetime import datetime

from tools.breeze.nifty_expired_options_loader import BreezeNiftyLoader, SAFE_CHUNK_DAYS
from utils.timezone import IST


class LoaderClient:
    def __init__(self):
        self.calls = []

    def get_historical_data_v2(self, **kwargs):
        self.calls.append(kwargs)
        return {"Status": 200, "Success": [{
            "datetime": kwargs["from_date"], "open": 1, "high": 2,
            "low": 1, "close": 2, "volume": 10,
        }]}


def test_loader_chunks_writes_manifest_and_deduplicates(tmp_path):
    client = LoaderClient()
    loader = BreezeNiftyLoader(client=client, root=tmp_path / "nifty", rate_seconds=0)
    entry = loader.fetch_option(
        "2024-02-29", 26500,
        datetime(2024, 1, 26, 9, 15, tzinfo=IST),
        datetime(2024, 1, 26, 15, 30, tzinfo=IST),
    )
    assert entry.status == "READY" and entry.row_count == 1
    assert len(client.calls) == 1
    assert client.calls[0]["interval"] == "1minute"
    assert client.calls[0]["expiry_date"].startswith("2024-02-29T06:00:00")
    assert (tmp_path / "nifty" / entry.path).exists()
    assert (tmp_path / "nifty" / "manifest.json").exists()

    before = len(client.calls)
    again = loader.fetch_option(
        "2024-02-29", 26500,
        datetime(2024, 1, 26, 9, 15, tzinfo=IST),
        datetime(2024, 1, 26, 15, 30, tzinfo=IST),
    )
    assert again.sha256 == entry.sha256
    assert len(client.calls) == before


def test_loader_safe_two_day_chunks_are_used(tmp_path):
    client = LoaderClient()
    loader = BreezeNiftyLoader(client=client, root=tmp_path / "nifty", rate_seconds=0)
    loader.fetch_option(
        "2024-02-29", 26500,
        datetime(2024, 1, 22, 9, 15, tzinfo=IST),
        datetime(2024, 1, 26, 15, 30, tzinfo=IST),
    )
    # The end timestamp is a partial third two-day window after the cursor
    # advances by one second to avoid overlap.
    assert len(client.calls) == 3
    assert all(call["to_date"] > call["from_date"] for call in client.calls)
    assert SAFE_CHUNK_DAYS == 2
import json

from tools.breeze.nifty_expired_options_probe import _shape, _request_log


def test_probe_shape_redacts_to_metadata():
    response = {"Status": 200, "Error": None, "Success": [
        {"datetime": "2024-01-30T09:46:00.000Z", "open": 1, "high": 2,
         "low": 0.5, "close": 1.5, "volume": 10, "open_interest": 20},
    ]}
    result = _shape(response)
    assert result["row_count"] == 1
    assert result["first_timestamp"] == result["last_timestamp"]
    assert "close" in result["row_keys"]
    assert "open_interest" in result["row_keys"]
    assert "1.5" not in json.dumps(result)


def test_request_log_contains_no_auth_fields():
    result = _request_log({"stock_code": "NIFTY", "strike_price": "22100"})
    encoded = json.dumps(result).lower()
    assert "api_key" not in encoded
    assert "secret" not in encoded
    assert "session" not in encoded
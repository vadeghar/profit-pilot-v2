"""Tests for merging strategy flags and status into the dashboard catalog."""
from platform_config import strategy_meta


def _setup(tmp_path, monkeypatch, flags_yaml: str):
    fp = tmp_path / "flags.yaml"
    fp.write_text(flags_yaml, encoding="utf-8")
    monkeypatch.setattr(strategy_meta, "FLAGS_PATH", fp)


def test_flags_and_default_status(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "a:\n  horizon: [Intraday]\n  segment: [Derivative]\n")
    cat = {"a": {"historical_stats": {"return_pct": "x"}}, "unlisted": {}}
    strategy_meta.enrich_catalog(cat)
    assert cat["a"]["flags"]["horizon"] == ["Intraday"] and cat["a"]["flags"]["segment"] == ["Derivative"]
    assert cat["a"]["flags"]["style"] == []
    assert cat["a"]["status"] == "experimental" and cat["unlisted"]["status"] == "experimental"
    assert cat["a"]["historical_stats"] == {"return_pct": "x"}  # left alone


def test_status_override_and_reason(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "a:\n  status_override: deprecated\n  status_reason: paused by the owner\n")
    cat = {"a": {}}
    strategy_meta.enrich_catalog(cat)
    assert cat["a"]["status"] == "deprecated" and cat["a"]["status_reason"] == "paused by the owner"


def test_repo_flags_cover_every_catalog_strategy():
    import web_app
    flags = strategy_meta.load_flags()
    assert set(flags) == set(web_app.STRATEGY_CATALOG)
    for sid in flags:
        for group in ("horizon", "segment", "instrument", "direction", "hedging", "style"):
            assert flags[sid].get(group), f"{sid} missing {group}"
    assert flags["index_oi_momentum"]["status_override"] == "deprecated"

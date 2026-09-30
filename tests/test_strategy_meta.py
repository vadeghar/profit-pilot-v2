"""Tests for merging strategy flags + audit results into the dashboard catalog."""
import json

from platform_config import strategy_meta


def _setup(tmp_path, monkeypatch, flags_yaml: str, audit: dict):
    fp, ap = tmp_path / "flags.yaml", tmp_path / "audit.json"
    fp.write_text(flags_yaml, encoding="utf-8")
    ap.write_text(json.dumps(audit), encoding="utf-8")
    monkeypatch.setattr(strategy_meta, "FLAGS_PATH", fp)
    monkeypatch.setattr(strategy_meta, "AUDIT_PATH", ap)


def test_verdicts_map_to_status_and_stats(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "a:\n  horizon: [Intraday]\n  segment: [Derivative]\nb:\n  horizon: [Short term]\n",
           {"generated": "2026-09-30", "strategies": {
               "a": {"verdict": "KEEP", "why": "ok", "trades": 40, "win_rate": 0.5, "profit_factor": 1.6,
                     "return_pct": 0.21, "max_dd_pct": 0.08, "net_pnl": 21000},
               "b": {"verdict": "DEPRECATE", "why": "loses money", "trades": 10, "win_rate": 0.3,
                     "profit_factor": 0.7, "return_pct": -0.05, "max_dd_pct": 0.1, "net_pnl": -5000}}})
    cat = {"a": {"historical_stats": {"return_pct": "made up"}}, "b": {}}
    strategy_meta.enrich_catalog(cat)
    assert cat["a"]["status"] == "active" and cat["b"]["status"] == "deprecated"
    assert cat["a"]["flags"]["horizon"] == ["Intraday"] and cat["a"]["flags"]["segment"] == ["Derivative"]
    assert cat["a"]["flags"]["style"] == []
    assert cat["a"]["historical_stats"] == {"return_pct": "+21.0% net", "win_rate": "50.0%",
                                            "max_dd": "8.0%", "sharpe": "PF 1.60"}
    assert cat["b"]["status_reason"] == "loses money"


def test_status_override_wins_over_audit(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "a:\n  status_override: active\n  status_reason: pinned by operator\n",
           {"strategies": {"a": {"verdict": "DEPRECATE", "why": "x"}}})
    cat = {"a": {}}
    strategy_meta.enrich_catalog(cat)
    assert cat["a"]["status"] == "active" and cat["a"]["status_reason"] == "pinned by operator"


def test_unaudited_strategy_is_experimental(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "{}\n", {})
    cat = {"new": {"historical_stats": {"return_pct": "x"}}}
    strategy_meta.enrich_catalog(cat)
    assert cat["new"]["status"] == "experimental"
    assert cat["new"]["historical_stats"] == {"return_pct": "x"}


def test_repo_flags_cover_every_catalog_strategy():
    flags = strategy_meta.load_flags()
    expected = {"mcx_trend_rider", "ema_crossover", "rsi", "breakout", "index_oi_momentum", "nifty_no_brainer",
                "four_indicator_system", "equity_swing_vcp", "lorentzian_ml"}
    assert expected <= set(flags)
    for sid in expected:
        for group in ("horizon", "segment", "instrument", "direction", "hedging", "style"):
            assert flags[sid].get(group), f"{sid} missing {group}"

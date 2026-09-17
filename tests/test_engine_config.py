import dataclasses

import pytest

from trading_bot.config import Config
from trading_bot.engine.config import MODES, EngineConfig, broker_config


def _base_broker_config(dry_run: bool) -> Config:
    return Config(
        api_key="k", client_code="c", pin="p", totp_secret="JBSWY3DPEHPK3PXP",
        dry_run=dry_run, local_ip="127.0.0.1", public_ip="127.0.0.1", mac_address="00-00-00-00-00-00",
    )


def test_defaults_are_shadow_and_dry(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("TECH_"):
            monkeypatch.delenv(name)
    cfg = EngineConfig.from_env()
    assert cfg.mode == "SHADOW"
    assert cfg.dry_run is True
    assert cfg.live_trading_enabled is False
    assert cfg.can_place_live_orders is False
    assert cfg.underlyings == ("NIFTY", "BANKNIFTY", "SENSEX")
    assert cfg.align_weights == {"1d": 0.25, "30m": 0.30, "5m": 0.30, "1m": 0.15}


@pytest.mark.parametrize("mode", MODES)
def test_only_fully_armed_live_can_place_orders(monkeypatch, mode):
    monkeypatch.setenv("TECH_MODE", mode)
    # Two of three switches flipped - still must NOT be live.
    monkeypatch.setenv("TECH_LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("TECH_DRY_RUN", "true")
    assert EngineConfig.from_env().can_place_live_orders is False
    monkeypatch.setenv("TECH_DRY_RUN", "false")
    monkeypatch.setenv("TECH_LIVE_TRADING_ENABLED", "false")
    assert EngineConfig.from_env().can_place_live_orders is False
    # All three, only in LIVE.
    monkeypatch.setenv("TECH_LIVE_TRADING_ENABLED", "true")
    assert EngineConfig.from_env().can_place_live_orders is (mode == "LIVE")


def test_broker_config_forces_dry_run_outside_armed_live(monkeypatch):
    monkeypatch.setenv("TECH_MODE", "PAPER")
    monkeypatch.setenv("TECH_DRY_RUN", "false")  # someone "helpfully" flipped it
    monkeypatch.setenv("TECH_LIVE_TRADING_ENABLED", "true")
    engine = EngineConfig.from_env()
    # even a base config that says dry_run=False gets overridden
    bcfg = broker_config(engine, base=_base_broker_config(dry_run=False))
    assert bcfg.dry_run is True


def test_broker_config_allows_live_only_when_armed(monkeypatch):
    monkeypatch.setenv("TECH_MODE", "LIVE")
    monkeypatch.setenv("TECH_DRY_RUN", "false")
    monkeypatch.setenv("TECH_LIVE_TRADING_ENABLED", "true")
    engine = EngineConfig.from_env()
    bcfg = broker_config(engine, base=_base_broker_config(dry_run=True))
    assert bcfg.dry_run is False
    # and nothing else on the base config was disturbed
    assert dataclasses.replace(bcfg, dry_run=True) == _base_broker_config(dry_run=True)


def test_invalid_mode_rejected(monkeypatch):
    monkeypatch.setenv("TECH_MODE", "YOLO")
    with pytest.raises(RuntimeError, match="TECH_MODE"):
        EngineConfig.from_env()


def test_alignment_weights_must_sum_to_one(monkeypatch):
    monkeypatch.setenv("TECH_ALIGN_W_DAILY", "0.5")
    monkeypatch.setenv("TECH_ALIGN_W_30M", "0.5")
    monkeypatch.setenv("TECH_ALIGN_W_5M", "0.5")
    monkeypatch.setenv("TECH_ALIGN_W_1M", "0.5")
    with pytest.raises(RuntimeError, match="sum to 1.0"):
        EngineConfig.from_env()


def test_time_and_list_parsing(monkeypatch):
    monkeypatch.setenv("TECH_EOD_CUTOFF", "15:10")
    monkeypatch.setenv("TECH_UNDERLYINGS", " nifty , sensex ")
    monkeypatch.setenv("TECH_HOLIDAYS", "2026-10-02, 2026-11-14")
    cfg = EngineConfig.from_env()
    assert cfg.eod_cutoff.hour == 15 and cfg.eod_cutoff.minute == 10
    assert cfg.underlyings == ("NIFTY", "SENSEX")
    assert cfg.holidays == ("2026-10-02", "2026-11-14")

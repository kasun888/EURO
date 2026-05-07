from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('DATA_DIR', '/data')).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS_PATH = BASE_DIR / 'settings.json'
EXAMPLE_SETTINGS_PATH = BASE_DIR / 'settings.json.example'  # fallback
SETTINGS_FILE = DATA_DIR / 'settings.json'
SECRETS_JSON_PATH = BASE_DIR / 'secrets.json'

# run-once guard — ensure_persistent_settings only syncs once per
# process lifetime.  Previously it re-ran on every load_settings() call
# because writing SETTINGS_FILE changed its mtime and invalidated the cache.
_settings_synced: bool = False


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            with path.open('r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as exc:
        logger.warning('Failed to read %s: %s', path, exc)
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def ensure_persistent_settings() -> Path:
    global _settings_synced
    if _settings_synced:
        return SETTINGS_FILE  # already ran this process — skip

    # Always read the bundled defaults shipped with the code.
    # also try settings.json.example as a fallback in case
    # settings.json was excluded by .gitignore and not deployed.
    default_settings = _read_json(DEFAULT_SETTINGS_PATH, {})
    if not isinstance(default_settings, dict):
        default_settings = {}

    if not default_settings and EXAMPLE_SETTINGS_PATH.exists():
        logger.warning(
            'settings.json not found at %s — falling back to settings.json.example.',
            DEFAULT_SETTINGS_PATH,
        )
        default_settings = _read_json(EXAMPLE_SETTINGS_PATH, {})
        if not isinstance(default_settings, dict):
            default_settings = {}

    # if neither file could be read, do NOT overwrite the
    # volume with an empty dict. Log a warning and leave the volume as-is.
    if not default_settings:
        logger.warning(
            'Bundled settings.json not found or empty at %s — '
            'volume settings left unchanged.',
            DEFAULT_SETTINGS_PATH,
        )
        _settings_synced = True
        return SETTINGS_FILE

    # ALWAYS overwrite the volume settings with the bundled
    # settings.json on every startup (first time only per process).
    # The Railway volume stores trade state — not configuration.
    if SETTINGS_FILE.exists():
        old_settings = _read_json(SETTINGS_FILE, {})
        old_name = old_settings.get('bot_name', 'unknown') if isinstance(old_settings, dict) else 'unknown'
    else:
        old_name = 'none'

    _write_json(SETTINGS_FILE, default_settings)
    _settings_synced = True
    new_name = default_settings.get('bot_name', 'unknown')
    if old_name != new_name:
        logger.info('Settings synced on startup: %s → %s', old_name, new_name)
    else:
        logger.info('Settings synced on startup: %s (refreshed from bundle)', new_name)
    return SETTINGS_FILE


# ── load_settings cache ──────────────────────────────────────────────────────
# Avoids re-reading disk on every call. Cache invalidated when file mtime
# changes — manual edits to settings.json take effect on the next cycle.
_settings_cache: dict = {}
_settings_mtime: float = 0.0


def load_settings() -> dict:
    global _settings_cache, _settings_mtime
    ensure_persistent_settings()

    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except OSError:
        mtime = 0.0

    if _settings_cache and mtime == _settings_mtime:
        return _settings_cache  # file unchanged — skip disk read

    settings = _read_json(SETTINGS_FILE, {})
    if not isinstance(settings, dict):
        settings = {}

    original_keys = set(settings.keys())

    settings.setdefault('bot_name', 'Fiber Scalp v2.1')
    settings.setdefault('enabled', True)
    settings.setdefault('cycle_minutes', 5)
    settings.setdefault('db_retention_days', 90)
    settings.setdefault('db_cleanup_hour_sgt', 0)
    settings.setdefault('db_cleanup_minute_sgt', 15)
    settings.setdefault('db_vacuum_weekly', True)
    settings.setdefault('calendar_fetch_interval_min', 60)
    settings.setdefault('calendar_retry_after_min', 15)

    # ── Persistent defaults — applied on startup if not in volume settings ───
    settings.setdefault('spread_limits', {'London': 4, 'US': 5, 'Tokyo': 4})
    settings.setdefault('max_trades_day', 20)
    settings.setdefault('max_losing_trades_day', 3)
    settings.setdefault('daily_risk_cap_usd', 120)
    settings.setdefault('max_trades_london', 10)
    settings.setdefault('max_trades_us', 10)
    settings.setdefault('max_losing_trades_session', 4)
    # Signal engine
    settings.setdefault('orb_fresh_minutes',         60)
    settings.setdefault('orb_aging_minutes',         120)
    settings.setdefault('min_rr_ratio',              1.6)   # 1.70x RR for EUR/USD
    # H1 trend filter
    settings.setdefault('h1_filter_enabled',        True)
    settings.setdefault('h1_filter_mode',           'score_aware')  # score_aware: score 4 needs alignment; score 5/6 allow neutral but block opposite
    settings.setdefault('h1_ema_period',            21)
    settings.setdefault('ema_fast_period',           9)
    settings.setdefault('ema_slow_period',           21)
    settings.setdefault('orb_formation_minutes',     15)
    settings.setdefault('calendar_prune_days_ahead', 21)
    settings.setdefault('startup_dedup_seconds',     90)
    settings.setdefault('atr_period',                14)
    settings.setdefault('m5_candle_count',           40)
    # Session windows
    settings.setdefault('london_session_start_hour', 16)
    settings.setdefault('london_session_end_hour',   20)
    settings.setdefault('us_session_start_hour',     99)  # disabled — 0% WR
    settings.setdefault('us_session_end_hour',       99)  # disabled
    settings.setdefault('us_session_early_end_hour',  3)
    settings.setdefault('dead_zone_start_hour',       4)
    settings.setdefault('dead_zone_end_hour',         7)
    # Report schedule
    settings.setdefault('daily_report_hour_sgt',      7)
    settings.setdefault('daily_report_minute_sgt',   50)
    settings.setdefault('weekly_report_hour_sgt',     8)
    settings.setdefault('weekly_report_minute_sgt',   0)
    settings.setdefault('weekly_export_hour_sgt',     8)
    settings.setdefault('weekly_export_minute_sgt',   5)
    settings.setdefault('monthly_report_hour_sgt',    8)
    settings.setdefault('monthly_report_minute_sgt',  0)
    # Tokyo session
    settings.setdefault('tokyo_session_start_hour',   8)
    settings.setdefault('tokyo_session_end_hour',    15)
    settings.setdefault('max_trades_tokyo',          10)
    # Global concurrent-trade cap
    settings.setdefault('max_total_open_trades',      1)
    # TP2 reference multiplier shown in trade opened Telegram alert
    settings.setdefault('tp2_rr_reference',           3.0)
    # minimum units after margin guard — reject micro-orders gracefully
    settings.setdefault('min_trade_units',           1000)
    settings.setdefault('max_units',                 20000)
    settings.setdefault('score_risk_usd',             {'4': 30, '5': 40, '6': 50})
    settings.setdefault('position_partial_usd',       30)  # legacy fallback for score 4
    settings.setdefault('position_full_usd',          40)  # legacy fallback for score 5
    settings.setdefault('telegram_min_score_alert',   4)  # suppress WATCHING below this score
    # EUR/USD fixed pip SL/TP — pip_value_usd static $10.00 (USD-quoted pair)
    settings.setdefault('pair_sl_tp', {
        'EUR_USD': {'sl_pips': 18, 'tp_pips': 30, 'pip_value_usd': 10.0, 'be_trigger_pips': 20},
    })

    if set(settings.keys()) != original_keys:
        _write_json(SETTINGS_FILE, settings)

    _settings_cache = settings
    _settings_mtime = mtime
    return settings


def save_settings(settings: dict) -> None:
    _write_json(SETTINGS_FILE, settings)
    logger.info('Saved settings -> %s', SETTINGS_FILE)


def load_secrets() -> dict:
    """Load secrets with environment variables taking priority over secrets.json."""
    file_secrets: dict = {}
    if SECRETS_JSON_PATH.exists():
        loaded = _read_json(SECRETS_JSON_PATH, {})
        if isinstance(loaded, dict):
            file_secrets = loaded

    return {
        'OANDA_API_KEY':    os.environ.get('OANDA_API_KEY')    or file_secrets.get('OANDA_API_KEY',    ''),
        'OANDA_ACCOUNT_ID': os.environ.get('OANDA_ACCOUNT_ID') or file_secrets.get('OANDA_ACCOUNT_ID', ''),
        'TELEGRAM_TOKEN':   os.environ.get('TELEGRAM_TOKEN')   or file_secrets.get('TELEGRAM_TOKEN',   ''),
        'TELEGRAM_CHAT_ID': os.environ.get('TELEGRAM_CHAT_ID') or file_secrets.get('TELEGRAM_CHAT_ID', ''),
        'DATA_DIR':         str(DATA_DIR),
    }


def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}

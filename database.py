"""
circuit.py — Circuit Breaker + Win-Stop + Smart Flip
======================================================
From EURO-USD bot:
  - 2 consecutive SL hits → check H4 trend direction
  - If H4 flipped → resume immediately in new direction
  - If H4 same → pause 2 days (choppy market)
  - 1 win per day → stop trading (protect the profit)

Used by bot.py after each trade closes.
"""

import logging
import requests
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

from config_loader import DATA_DIR
_STATE_FILE = DATA_DIR / "circuit_state.json"


def _load() -> dict:
    from state_utils import load_json
    return load_json(_STATE_FILE, {})


def _save(state: dict):
    from state_utils import save_json
    save_json(_STATE_FILE, state)


# ── H4 direction check ────────────────────────────────────────────────────────

def get_h4_direction(api_key: str, base_url: str) -> str | None:
    """
    Check current H4 trend via EMA50 with 3-bar confirmation.
    Returns "BUY", "SELL", or None if unclear.
    """
    try:
        import requests as _req
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        # FIX H4-RETRY: use retry session, not bare requests.get
        _retries = Retry(total=3, backoff_factor=0.5,
                         status_forcelist=[429, 500, 502, 503, 504],
                         allowed_methods=["GET"])
        _s = _req.Session()
        _s.mount("https://", HTTPAdapter(max_retries=_retries))

        headers = {"Authorization": f"Bearer {api_key}"}
        url     = f"{base_url}/v3/instruments/EUR_USD/candles"
        params  = {"count": "55", "granularity": "H4", "price": "M"}
        r       = _s.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return None
        candles = [x for x in r.json()["candles"] if x["complete"]]
        closes  = [float(x["mid"]["c"]) for x in candles]
        if len(closes) < 52:
            return None

        # EMA50
        seed = sum(closes[:50]) / 50
        ema  = seed
        mult = 2 / 51
        for c in closes[50:]:
            ema = (c - ema) * mult + ema

        # 3-bar consistency
        last3 = closes[-3:]
        if all(c > ema for c in last3):
            return "BUY"
        elif all(c < ema for c in last3):
            return "SELL"
        return None
    except Exception as e:
        log.warning("get_h4_direction error: %s", e)
        return None


# ── Win-stop ──────────────────────────────────────────────────────────────────

def is_win_stop_active(today: str) -> bool:
    """
    Returns True if we already got 1 win today and win_stop is triggered.
    Uses circuit state file.
    """
    state = _load()
    return state.get("win_stop_date") == today and state.get("wins_today", 0) >= 1


def record_win(today: str, alert=None):
    """Call after a TP hit. Sets win-stop for today."""
    state = _load()
    if state.get("win_stop_date") != today:
        state["wins_today"] = 0
        state["win_stop_date"] = today
    state["wins_today"] = state.get("wins_today", 0) + 1
    state["consec_losses"] = 0
    _save(state)
    if alert and state["wins_today"] == 1:
        alert.send(
            "✅ WIN-STOP TRIGGERED\n"
            "First win of the day secured.\n"
            "No more new trades today — protecting profit.\n"
            "Resumes tomorrow at 08:00 SGT."
        )
    log.info("Win recorded. wins_today=%d — win-stop active for %s",
             state["wins_today"], today)


# ── Circuit breaker ───────────────────────────────────────────────────────────

def is_circuit_breaker_active(settings: dict | None = None) -> tuple[bool, str]:
    """
    Returns (active: bool, reason: str).
    FIX CB-FLAG: respects circuit_breaker_enabled setting.
    """
    s = settings or {}
    if not s.get("circuit_breaker_enabled", True):
        return False, ""   # explicitly disabled in settings
    state = _load()
    pause = state.get("pause_until")
    if not pause:
        return False, ""
    try:
        remaining = (datetime.fromisoformat(pause) -
                     datetime.now(timezone.utc)).total_seconds()
        if remaining > 0:
            days = round(remaining / 86400, 1)
            return True, f"Circuit breaker — {days}d remaining"
        else:
            # Expired — clear it
            state.pop("pause_until", None)
            _save(state)
            log.info("Circuit breaker expired — resuming")
            return False, ""
    except Exception:
        state.pop("pause_until", None)
        _save(state)
        return False, ""


def record_sl(direction: str, api_key: str, base_url: str,
              settings: dict, alert=None):
    """
    Call after each SL hit. Increments consecutive loss count.
    If count reaches threshold → smart flip check → pause or resume.
    """
    state = _load()
    consec = state.get("consec_losses", 0) + 1
    # Read PREVIOUS direction BEFORE overwriting — needed for smart flip comparison
    prev_direction = state.get("last_trade_direction", "")
    state["consec_losses"]        = consec
    state["last_trade_direction"] = direction
    _save(state)

    threshold  = int(settings.get("circuit_breaker_consec_sl", 2))
    pause_days = int(settings.get("circuit_breaker_pause_days", 2))

    if consec < threshold:
        log.info("SL recorded. consec_losses=%d (threshold=%d)", consec, threshold)
        return

    # ── Smart flip detection (from EURO-USD bot) ───────────────────────────
    last_dir   = prev_direction   # direction of the PREVIOUS trade (before this SL)
    h4_dir_now = get_h4_direction(api_key, base_url)
    log.info("Smart flip check — last=%s H4_now=%s", last_dir, h4_dir_now)

    if h4_dir_now and last_dir and h4_dir_now != last_dir:
        # Trend flipped — resume in new direction immediately
        state["consec_losses"] = 0
        state.pop("pause_until", None)
        _save(state)
        log.info("H4 FLIPPED %s→%s — resuming immediately", last_dir, h4_dir_now)
        if alert:
            alert.send(
                f"🔄 TREND FLIP DETECTED\n"
                f"H4: {last_dir} → {h4_dir_now}\n"
                f"Resuming in new direction immediately.\n"
                f"Market shifted — not choppy."
            )
    else:
        # Same direction or unclear — genuine chop, pause
        pause_until = datetime.now(timezone.utc) + timedelta(days=pause_days)
        state["pause_until"]    = pause_until.isoformat()
        state["consec_losses"]  = 0
        _save(state)
        log.warning("CIRCUIT BREAKER — H4 unchanged (%s). Pausing %d days.",
                    h4_dir_now, pause_days)
        if alert:
            alert.send(
                f"⛔ CIRCUIT BREAKER\n"
                f"{consec} consecutive SL hits.\n"
                f"H4 direction unchanged ({h4_dir_now or 'unclear'}).\n"
                f"Pausing {pause_days} days — choppy market.\n"
                f"Resumes automatically."
            )


def reset_consec_losses():
    """
    Reset consecutive loss counter.
    NOTE: record_win() already calls this internally.
    Only call this directly after a timeout-close or manual intervention.
    FIX DBL-RESET: do NOT call this after record_win() — it's redundant.
    """
    state = _load()
    state["consec_losses"] = 0
    _save(state)

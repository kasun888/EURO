"""
OANDA — EUR/USD Multi-Session Scalp Bot  (Strategy V7-PLUS)
============================================================
Pair:     EUR/USD only
Size:     74,000 units
SL:       7 pips   ≈ SGD 70
TP:       10 pips  ≈ SGD 100  [R:R 1.43]
Max dur:  45 minutes

SESSIONS (all active):
  Asian   01:00–06:00 UTC  =  09:00–14:00 SGT
  London  07:00–12:00 UTC  =  15:00–20:00 SGT
  NY      13:00–16:00 UTC  =  21:00–00:00 SGT

SIGNAL (4 layers):
  L0  H4 EMA50       + last 3 bars same side (trend consistency)
  L1  H4 ATR(14)     > 6 pip (trending market)
  L2  H1 EMA20+EMA50 price above/below BOTH + ATR > 4.5p
  L3  M15 EMA9/EMA21 ongoing trend + RSI 38-62 + ATR > 4.5p
  L4  M5 close vs EMA9 + body >=45%

RULES:
  - Max 1 trade per session (3 possible per day)
  - 15 min cooldown after any loss
  - 45 min hard close
  - News filter: skip 30 min before/after high-impact EUR/USD events
  - No trades Friday after 14:00 UTC
  - All Telegram alerts in SGD with live balance
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytz

from signals         import SignalEngine
from oanda_trader    import OandaTrader
from telegram_alert  import TelegramAlert
from calendar_filter import EconomicCalendar as CalendarFilter

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

sg_tz  = pytz.timezone("Asia/Singapore")
utc_tz = pytz.UTC
signals = SignalEngine()

# ── TRADE PARAMETERS ─────────────────────────────────────────────────
TRADE_SIZE      = 74_000
SL_PIPS         = 7
TP_PIPS         = 10
MAX_DURATION    = 45
MAX_PER_SESSION = 1     # 1 quality trade per session
COOLDOWN_MIN    = 15
USD_SGD         = 1.35

# ── SESSIONS (all zones) ─────────────────────────────────────────────
SESSIONS = [
    {
        "label":      "Asian",
        "utc_start":  1,
        "utc_end":    6,
        "sgt_label":  "09:00-14:00 SGT",
        "max_spread": 2.0,
    },
    {
        "label":      "London",
        "utc_start":  7,
        "utc_end":    12,
        "sgt_label":  "15:00-20:00 SGT",
        "max_spread": 1.5,
    },
    {
        "label":      "NY",
        "utc_start":  13,
        "utc_end":    16,
        "sgt_label":  "21:00-00:00 SGT",
        "max_spread": 1.5,
    },
]

ASSET = {
    "instrument": "EUR_USD",
    "asset":      "EURUSD",
    "emoji":      "EU",
    "pip":        0.0001,
    "precision":  5,
}

DEFAULT_SETTINGS = {"signal_threshold": 4, "demo_mode": True}
_SETTINGS_PATH   = Path(__file__).parent / "settings.json"


def load_settings():
    try:
        with open(_SETTINGS_PATH) as f:
            DEFAULT_SETTINGS.update(json.load(f))
    except FileNotFoundError:
        with open(_SETTINGS_PATH, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)
    return DEFAULT_SETTINGS


def get_active_session():
    """Return the currently active session dict, or None if outside all sessions."""
    now_utc = datetime.now(utc_tz)
    h = now_utc.hour
    # No trades Friday after 14:00 UTC
    if now_utc.weekday() == 4 and h >= 14:
        return None
    for s in SESSIONS:
        if s["utc_start"] <= h < s["utc_end"]:
            return s
    return None


def set_cooldown(state):
    state["cooldown_until"] = datetime.now(timezone.utc).isoformat()
    log.info("Cooldown set — " + str(COOLDOWN_MIN) + " min")


def in_cooldown(state):
    cd = state.get("cooldown_until")
    if not cd:
        return False
    try:
        elapsed = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(cd)).total_seconds() / 60
        return elapsed < COOLDOWN_MIN
    except Exception:
        return False


def cooldown_remaining(state):
    cd = state.get("cooldown_until")
    if not cd:
        return 0
    try:
        elapsed = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(cd)).total_seconds() / 60
        return max(0, int(COOLDOWN_MIN - elapsed))
    except Exception:
        return "?"


def detect_sl_tp_hits(state, trader, alert):
    name = ASSET["instrument"]
    if name not in state.get("open_times", {}):
        return
    if trader.get_position(name):
        return

    try:
        url  = (trader.base_url + "/v3/accounts/" + trader.account_id +
                "/trades?state=CLOSED&instrument=" + name + "&count=1")
        data = requests.get(url, headers=trader.headers,
                            timeout=10).json().get("trades", [])
        if not data:
            return

        pnl        = float(data[0].get("realizedPL", "0"))
        pnl_sgd    = round(pnl * USD_SGD, 2)
        wins       = state.get("wins", 0)
        losses     = state.get("losses", 0)
        live_bal   = trader.get_balance()
        bal_sgd    = round(live_bal * USD_SGD, 2)

        if pnl < 0:
            set_cooldown(state)
            state["losses"]        = losses + 1
            state["consec_losses"] = state.get("consec_losses", 0) + 1
            alert.send(
                "SL HIT - LOSS\n"
                + ASSET["emoji"] + " EUR/USD\n"
                "Loss:      SGD -" + str(abs(pnl_sgd)) + "\n"
                "Balance:   SGD " + str(bal_sgd) + "\n"
                "Cooldown " + str(COOLDOWN_MIN) + " min\n"
                "W/L today: " + str(wins) + "/" + str(state["losses"])
            )
        else:
            state["wins"]          = wins + 1
            state["consec_losses"] = 0
            alert.send(
                "TP HIT - WIN\n"
                + ASSET["emoji"] + " EUR/USD\n"
                "Profit:    SGD +" + str(pnl_sgd) + "\n"
                "Balance:   SGD " + str(bal_sgd) + "\n"
                "W/L today: " + str(state["wins"]) + "/" + str(losses)
            )
    except Exception as e:
        log.warning("SL/TP detect error: " + str(e))

    state.get("open_times", {}).pop(name, None)


def run_bot(state):
    settings = load_settings()
    now_utc  = datetime.now(utc_tz)
    now_sg   = datetime.now(sg_tz)
    today    = now_sg.strftime("%Y%m%d")
    alert    = TelegramAlert()
    calendar = CalendarFilter()

    log.info("Scan at " + now_sg.strftime("%H:%M:%S SGT") +
             "  (" + now_utc.strftime("%H:%M UTC") + ")")

    # ── Session gate ─────────────────────────────────────────────────
    session = get_active_session()
    if not session:
        log.info("Outside all sessions — sleeping")
        return

    log.info("Session: " + session["label"] +
             " (" + session["sgt_label"] + ")" +
             " | Max spread: " + str(session["max_spread"]) + "p")

    # ── Login ─────────────────────────────────────────────────────────
    trader = OandaTrader(demo=settings["demo_mode"])
    if not trader.login():
        log.warning("Login failed — skipping scan")
        return

    current_balance = trader.get_balance()
    if "start_balance" not in state or state["start_balance"] == 0.0:
        state["start_balance"] = current_balance

    # ── Session open alert (once per session per day) ─────────────────
    # FIX: check utc_start <= hour < utc_start+1 so alert fires exactly at open
    alert_key = session["label"] + "_open_" + today
    if (not state.get("session_alerted", {}).get(alert_key) and
            now_utc.hour == session["utc_start"]):
        state.setdefault("session_alerted", {})[alert_key] = True
        bal_sgd    = round(current_balance * USD_SGD, 2)
        start_sgd  = round(state.get("start_balance", current_balance) * USD_SGD, 2)
        daily_usd  = round(current_balance - state.get("start_balance", current_balance), 2)
        daily_sgd  = round(daily_usd * USD_SGD, 2)
        daily_sign = "+" if daily_sgd >= 0 else ""
        wins       = state.get("wins", 0)
        losses     = state.get("losses", 0)
        alert.send(
            session["label"] + " Session Open!\n"
            + session["sgt_label"] + "\n"
            "-----------------\n"
            "Balance:    SGD " + str(bal_sgd) + "\n"
            "Day start:  SGD " + str(start_sgd) + "\n"
            "Daily P&L:  SGD " + daily_sign + str(daily_sgd) + "\n"
            "W/L today:  " + str(wins) + "/" + str(losses) + "\n"
            "-----------------\n"
            "TP=" + str(TP_PIPS) + "p (SGD " +
            str(round(TRADE_SIZE * TP_PIPS * ASSET["pip"] * USD_SGD, 0)) +
            ") | SL=" + str(SL_PIPS) + "p (SGD " +
            str(round(TRADE_SIZE * SL_PIPS * ASSET["pip"] * USD_SGD, 0)) +
            ")"
        )

    detect_sl_tp_hits(state, trader, alert)

    name = ASSET["instrument"]

    # ── 45-min hard close ────────────────────────────────────────────
    pos = trader.get_position(name)
    if pos:
        try:
            trade_id, open_str = trader.get_open_trade_id(name)
            if trade_id and open_str:
                open_utc = datetime.fromisoformat(
                    open_str.replace("Z", "+00:00"))
                mins = (datetime.now(pytz.utc) -
                        open_utc).total_seconds() / 60
                log.info(name + ": open " + str(round(mins, 1)) + " min")
                if mins >= MAX_DURATION:
                    pnl     = trader.check_pnl(pos)
                    pnl_sgd = round(pnl * USD_SGD, 2)
                    trader.close_position(name)
                    state.get("open_times", {}).pop(name, None)
                    if pnl < 0:
                        set_cooldown(state)
                    live_bal_sgd = round(trader.get_balance() * USD_SGD, 2)
                    alert.send(
                        "45-MIN TIMEOUT\n"
                        + ASSET["emoji"] + " EUR/USD\n"
                        "Closed at " + str(round(mins, 1)) + " min\n"
                        "PnL:     SGD " + ("+" if pnl_sgd >= 0 else "") +
                        str(pnl_sgd) + "\n"
                        "Balance: SGD " + str(live_bal_sgd)
                    )
        except Exception as e:
            log.warning("Duration check error: " + str(e))
        return

    # ── Per-session trade limit ───────────────────────────────────────
    session_key    = session["label"] + "_" + today
    session_trades = state.get("session_trades", {}).get(session_key, 0)
    if session_trades >= MAX_PER_SESSION:
        log.info(session["label"] + " limit reached — done for this session")
        return

    # ── Cooldown ─────────────────────────────────────────────────────
    if in_cooldown(state):
        log.info("Cooldown — " + str(cooldown_remaining(state)) + " min left")
        return

    # ── Price & spread ────────────────────────────────────────────────
    price, bid, ask = trader.get_price(name)
    if price is None:
        log.warning("Cannot get price — skipping")
        return

    spread_pip = (ask - bid) / ASSET["pip"]
    if spread_pip > session["max_spread"] + 0.05:
        log.info("Spread " + str(round(spread_pip, 2)) + "p > max " +
                 str(session["max_spread"]) + "p — skip")
        return

    # ── News filter ──────────────────────────────────────────────────
    news_active, news_reason = calendar.is_news_time(name)
    if news_active:
        news_key = name + "_news_" + now_sg.strftime("%Y%m%d%H")
        if not state.get("news_alerted", {}).get(news_key):
            state.setdefault("news_alerted", {})[news_key] = True
            alert.send("NEWS BLOCK\n" + ASSET["emoji"] +
                       " EUR/USD\n" + news_reason + "\nSkipping trade")
        log.info("News block: " + news_reason)
        return

    # ── Signal scan ──────────────────────────────────────────────────
    threshold = settings.get("signal_threshold", 4)
    score, direction, details = signals.analyze(
        asset=ASSET["asset"], state=state)
    log.info(name + ": score=" + str(score) + "/" + str(threshold) +
             " dir=" + direction + " | " + details)

    if score < threshold or direction == "NONE":
        log.info(name + ": no setup — waiting for alignment")
        return

    # ── Place trade ──────────────────────────────────────────────────
    sl_sgd = round(TRADE_SIZE * SL_PIPS * ASSET["pip"] * USD_SGD, 2)
    tp_sgd = round(TRADE_SIZE * TP_PIPS * ASSET["pip"] * USD_SGD, 2)

    result = trader.place_order(
        instrument=name,
        direction=direction,
        size=TRADE_SIZE,
        stop_distance=SL_PIPS,
        limit_distance=TP_PIPS,
    )

    if result["success"]:
        state["trades"] = state.get("trades", 0) + 1
        state.setdefault("session_trades", {})[session_key] = session_trades + 1
        state.setdefault("open_times", {})[name] = now_sg.isoformat()

        price, _, _ = trader.get_price(name)
        cur_bal_sgd = round(current_balance * USD_SGD, 2)
        alert.send(
            "NEW TRADE!  [" + session["label"] + " Session]\n"
            + ASSET["emoji"] + " EUR/USD\n"
            "Direction: " + direction + "\n"
            "Entry:     " + str(round(price, ASSET["precision"])) + "\n"
            "-----------------\n"
            "SL:        " + str(SL_PIPS) + " pips = SGD " + str(sl_sgd) + "\n"
            "TP:        " + str(TP_PIPS) + " pips = SGD " + str(tp_sgd) + "\n"
            "-----------------\n"
            "Balance:   SGD " + str(cur_bal_sgd) + "\n"
            "Spread:    " + str(round(spread_pip, 2)) + "p | Score: " +
            str(score) + "/4\n"
            "Max hold:  45 min"
        )
        log.info(name + ": PLACED " + direction +
                 " TP=SGD" + str(tp_sgd) + " SL=SGD" + str(sl_sgd))
    else:
        set_cooldown(state)
        log.warning(name + ": order failed — " + str(result.get("error", "")))

    log.info("Scan complete.")


# ── Standalone single-scan entry (used by GitHub Actions) ────────────
if __name__ == "__main__":
    import json
    from pathlib import Path

    STATE_FILE = Path(__file__).parent / "bot_state.json"

    # Load persisted state (GitHub Actions uploads/downloads this as artifact)
    state = {}
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            log.info("State loaded from " + str(STATE_FILE))
        except Exception as e:
            log.warning("State load error: " + str(e) + " — using fresh state")

    # Reset date key if new day (SGT)
    sg_tz_local = pytz.timezone("Asia/Singapore")
    today = datetime.now(sg_tz_local).strftime("%Y%m%d")
    if state.get("date") != today:
        log.info("New day — resetting daily state")
        prev_cooldown   = state.get("cooldown_until")
        prev_open_times = state.get("open_times", {})
        state = {
            "date":            today,
            "trades":          0,
            "start_balance":   0.0,
            "wins":            0,
            "losses":          0,
            "consec_losses":   0,
            "session_trades":  {},
            "cooldown_until":  None,
            "open_times":      {},
            "news_alerted":    {},
            "session_alerted": {},
        }
        if prev_cooldown:
            state["cooldown_until"] = prev_cooldown
        if prev_open_times:
            state["open_times"] = prev_open_times

    run_bot(state=state)

    # Save state back for next run
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        log.info("State saved to " + str(STATE_FILE))
    except Exception as e:
        log.warning("State save error: " + str(e))

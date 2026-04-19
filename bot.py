"""
OANDA — EUR/USD London + NY Session Scalp Bot
==============================================
Pair:    EUR/USD only
Size:    74,000 units
SL:      13 pips
TP:      26 pips  [2:1 R:R]
Max dur: 30 minutes

WHY THESE WINDOWS FOR EUR/USD (SGT = UTC+8):
  EUR/USD daily range is driven by two major liquidity events:
  - London Open  (08:00–12:00 UTC) = 15:00–19:00 SGT
    → ECB Frankfurt opens, EUR liquidity highest, cleanest trends form
    → Average 30–50 pip moves in first 2 hours
  - NY Session   (13:00–17:00 UTC) = 20:00–00:59 SGT (next day SGT midnight cap)
    → US data releases, Fed-related flows, USD pairs most active
    → EUR/USD second-best window, overlaps London close momentum

  SKIPPED: Asian session (00:00–07:00 UTC = 07:00–14:00 SGT)
    → EUR/USD averages only 20–30 pips in Asian session
    → Spread typically widens, false breakouts common
    → Not enough range to reliably hit 26 pip TP

Two trading windows per day (SGT):
  Window 1 — London Open : 15:00–19:00 SGT  (max spread: 1.2 pip)
  Window 2 — NY Session  : 20:00–00:00 SGT  (max spread: 1.5 pip)

Rules:
  - No trade limit per window
  - No daily trade limit
  - Signal: 4/4 required (H4 trend + H1 stack + M15 impulse + M5 RSI entry)

  - SL hit in window → cooldown 30 min then skip rest of that window
  - Max duration: 30 min then force-close
"""

import os, json, time, logging, requests
from datetime import datetime, timezone
from pathlib import Path
import pytz

from signals         import SignalEngine
from oanda_trader    import OandaTrader
from telegram_alert  import TelegramAlert
from calendar_filter import EconomicCalendar as CalendarFilter

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

sg_tz   = pytz.timezone("Asia/Singapore")
signals = SignalEngine()

TRADE_SIZE   = 74000
SL_PIPS      = 13
TP_PIPS      = 26
MAX_DURATION = 30
USD_SGD      = 1.35

# Two windows optimized for EUR/USD liquidity profile
ASSETS = {
    "EUR_USD": {
        "instrument": "EUR_USD",
        "asset":      "EURUSD",
        "emoji":      "🇪🇺",
        "pip":        0.0001,
        "precision":  5,
        "stop_pips":  SL_PIPS,
        "tp_pips":    TP_PIPS,
        "sessions": [
            {"start": 15, "end": 19, "max_spread": 1.2, "label": "London"},
            {"start": 20, "end": 24, "max_spread": 1.5, "label": "NY"},
        ],
    },
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


def get_active_session(hour):
    """Return active session config or None. Hour 24 is treated as 0."""
    cfg = ASSETS["EUR_USD"]
    for s in cfg["sessions"]:
        # NY window: start=20, end=24 — hour 0 is midnight, treat as end
        if s["start"] <= hour < s["end"]:
            return s
    return None


def is_in_session(hour, cfg):
    for s in cfg["sessions"]:
        if s["start"] <= hour < s["end"]:
            return True
    return False


def window_key(session_label, date_str):
    return "window_" + date_str + "_" + session_label


def set_cooldown(state, name):
    if "cooldowns" not in state:
        state["cooldowns"] = {}
    state["cooldowns"][name] = datetime.now(timezone.utc).isoformat()
    log.info(name + " cooldown 30 min")


def in_cooldown(state, name):
    cd = state.get("cooldowns", {}).get(name)
    if not cd:
        return False
    try:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(cd)).total_seconds() / 60
        return elapsed < 30
    except:
        return False


def cooldown_remaining(state, name):
    cd = state.get("cooldowns", {}).get(name)
    if not cd:
        return 0
    try:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(cd)).total_seconds() / 60
        return max(0, int(30 - elapsed))
    except:
        return "?"


def _login_fail_key(now):
    slot = now.hour * 2 + (1 if now.minute >= 30 else 0)
    return now.strftime("%Y%m%d") + "_" + str(slot)


def detect_sl_tp_hits(state, trader, alert):
    if "open_times" not in state:
        return
    for name in list(state["open_times"].keys()):
        if trader.get_position(name):
            continue
        try:
            url  = (trader.base_url + "/v3/accounts/" + trader.account_id +
                    "/trades?state=CLOSED&instrument=" + name + "&count=1")
            data = requests.get(url, headers=trader.headers, timeout=10).json().get("trades", [])
            if data:
                pnl     = float(data[0].get("realizedPL", "0"))
                pnl_sgd = round(pnl * USD_SGD, 2)
                emoji   = ASSETS.get(name, {}).get("emoji", "")
                wins    = state.get("wins", 0)
                losses  = state.get("losses", 0)
                if pnl < 0:
                    set_cooldown(state, name)
                    state["losses"]        = losses + 1
                    state["consec_losses"] = state.get("consec_losses", 0) + 1
                    alert.send(
                        "🔴 SL HIT\n" + emoji + " " + name + "\n"
                        "Loss:  $" + str(round(pnl, 2)) + " USD\n"
                        "     ≈ SGD -" + str(abs(pnl_sgd)) + "\n"
                        "⏳ Cooldown 30 min\n"
                        "W/L: " + str(wins) + "/" + str(state["losses"])
                    )
                else:
                    state["wins"]          = wins + 1
                    state["consec_losses"] = 0
                    alert.send(
                        "✅ TP HIT\n" + emoji + " " + name + "\n"
                        "Profit: $+" + str(round(pnl, 2)) + " USD\n"
                        "      ≈ SGD +" + str(pnl_sgd) + "\n"
                        "W/L: " + str(state["wins"]) + "/" + str(losses)
                    )
        except Exception as e:
            log.warning("SL/TP detect error " + name + ": " + str(e))
        del state["open_times"][name]


def run_bot(state):
    settings = load_settings()
    now      = datetime.now(sg_tz)
    hour     = now.hour
    today    = now.strftime("%Y%m%d")
    alert    = TelegramAlert()
    calendar = CalendarFilter()

    log.info("Scan at " + now.strftime("%H:%M:%S SGT"))

    # Check active session
    session = get_active_session(hour)
    if not session:
        log.info("Outside trading windows (" + str(hour) + "h SGT) — next: 15:00 (London) or 20:00 (NY) SGT")
        return

    log.info("Window: " + session["label"] + " | Max spread: " + str(session["max_spread"]) + " pip")


    # Login
    trader = OandaTrader(demo=settings["demo_mode"])
    if not trader.login():
        fail_key = _login_fail_key(now)
        if not state.get("login_fail_alerted", {}).get(fail_key):
            if "login_fail_alerted" not in state:
                state["login_fail_alerted"] = {}
            state["login_fail_alerted"][fail_key] = True
            api_key    = os.environ.get("OANDA_API_KEY", "")
            account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
            alert.send(
                "❌ Login FAILED\n"
                "Key: " + (api_key[:8] + "****" if api_key else "MISSING") + "\n"
                "Account: " + (account_id if account_id else "MISSING") + "\n"
                "Check Railway logs."
            )
        else:
            log.warning("Login failed — alert already sent this 30-min window, suppressed")
        return

    current_balance = trader.get_balance()
    if "start_balance" not in state or state["start_balance"] == 0.0:
        state["start_balance"] = current_balance

    detect_sl_tp_hits(state, trader, alert)

    # ── 30-MIN HARD CLOSE ─────────────────────────────────────────────
    for name in ASSETS:
        pos = trader.get_position(name)
        if not pos:
            continue
        try:
            trade_id, open_str = trader.get_open_trade_id(name)
            if not trade_id or not open_str:
                continue
            open_utc = datetime.fromisoformat(open_str.replace("Z", "+00:00"))
            mins     = (datetime.now(pytz.utc) - open_utc).total_seconds() / 60
            log.info(name + ": open " + str(round(mins, 1)) + " min")
            if mins >= MAX_DURATION:
                pnl     = trader.check_pnl(pos)
                pnl_sgd = round(pnl * USD_SGD, 2)
                trader.close_position(name)
                state.get("open_times", {}).pop(name, None)
                alert.send(
                    "⏰ 30-MIN TIMEOUT\n"
                    + ASSETS[name]["emoji"] + " " + name + "\n"
                    "Closed at " + str(round(mins, 1)) + " min\n"
                    "PnL: $" + str(round(pnl, 2)) + " USD " + ("✅" if pnl >= 0 else "🔴") + "\n"
                    "   ≈ SGD " + str(pnl_sgd)
                )
        except Exception as e:
            log.warning("Duration check " + name + ": " + str(e))

    # ── SCAN + TRADE ──────────────────────────────────────────────────
    threshold = settings.get("signal_threshold", 4)

    for name, cfg in ASSETS.items():

        pos = trader.get_position(name)
        if pos:
            pnl_sgd = round(trader.check_pnl(pos) * USD_SGD, 2)
            dirn    = "BUY" if int(float(pos.get("long", {}).get("units", 0))) > 0 else "SELL"
            log.info(name + ": " + dirn + " open SGD " + str(pnl_sgd))
            continue

        if in_cooldown(state, name):
            log.info(name + ": cooldown " + str(cooldown_remaining(state, name)) + "min")
            continue

        price, bid, ask = trader.get_price(name)
        if price is None:
            log.warning(name + ": price error"); continue

        spread = (ask - bid) / cfg["pip"]
        if spread > session["max_spread"] + 0.05:
            log.info(name + ": spread " + str(round(spread, 2)) + "p — skip (max " + str(session["max_spread"]) + "p)")
            continue

        # News filter
        news_active, news_reason = calendar.is_news_time(name)
        if news_active:
            alert_key = name + "_news_" + now.strftime("%Y%m%d%H")
            if not state.get("news_alerted", {}).get(alert_key):
                if "news_alerted" not in state:
                    state["news_alerted"] = {}
                state["news_alerted"][alert_key] = True
                alert.send("⚠️ NEWS BLOCK\n" + cfg["emoji"] + " " + name + "\n" + news_reason + "\nSkipping")
            log.info(name + ": news — " + news_reason)
            continue

        # Signal check — requires 4/4 for EUR/USD (state passed for L2→L3 memory)
        score, direction, details = signals.analyze(asset=cfg["asset"], state=state)
        log.info(name + ": score=" + str(score) + "/" + str(threshold) +
                 " dir=" + direction + " | " + details)

        if score < threshold or direction == "NONE":
            log.info(name + ": no setup — waiting")
            continue

        # ── Place trade ───────────────────────────────────────────────
        sl_sgd = round(TRADE_SIZE * SL_PIPS * cfg["pip"] * USD_SGD, 2)
        tp_sgd = round(TRADE_SIZE * TP_PIPS * cfg["pip"] * USD_SGD, 2)

        result = trader.place_order(
            instrument=name, direction=direction, size=TRADE_SIZE,
            stop_distance=SL_PIPS, limit_distance=TP_PIPS
        )
        if result["success"]:
            state["trades"] = state.get("trades", 0) + 1
            if "open_times" not in state:
                state["open_times"] = {}
            state["open_times"][name] = now.isoformat()


            price, _, _ = trader.get_price(name)
            alert.send(
                "🔄 NEW TRADE! [" + session["label"] + " Window]\n"
                + cfg["emoji"] + " " + name + "\n"
                "Direction: " + direction + "\n"
                "Score:     " + str(score) + "/4 ✅\n"
                "Size:      74,000 units\n"
                "Entry:     " + str(round(price, cfg["precision"])) + "\n"
                "SL:        " + str(SL_PIPS) + " pips ≈ SGD " + str(sl_sgd) + "\n"
                "TP:        " + str(TP_PIPS) + " pips ≈ SGD " + str(tp_sgd) + "\n"
                "Max Time:  30 min\n"
                "Spread:    " + str(round(spread, 2)) + "p\n"
                "Signals:   " + details
            )
            log.info(name + ": PLACED " + direction + " SL=" + str(sl_sgd) + " TP=" + str(tp_sgd))
        else:
            set_cooldown(state, name)
            log.warning(name + ": order failed — " + str(result.get("error", "")))

    log.info("Scan complete.")

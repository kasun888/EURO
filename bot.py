"""
OANDA — EUR/USD Bot v5
========================
Pair:    EUR/USD only
Size:    50,000 units
SL:      13 pips
TP:      26 pips  [R:R 2:1]
Max dur: 45 minutes

SESSIONS (SGT = UTC+8):
  London  15:00–19:00 SGT  max spread 1.5p
  NY      20:00–00:00 SGT  max spread 1.5p

v5 CHANGES:
  ✅ SL 13 pip / TP 26 pip (2:1 R:R)
  ✅ Windows: 15:00-19:00 + 20:00-00:00 SGT
  ✅ No trade limit (WIN-STOP removed — trade as many valid setups as appear)
  ✅ Circuit breaker kept (2 SL hits → 2 day pause / smart flip detection)
  ✅ L1 loosened in signals.py for more frequency
  ✅ 24h day handling — NY window crosses midnight, handled via hour check
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

TRADE_SIZE   = 50000
MAX_DURATION = 45

# ── v5: 13 SL / 26 TP / 2:1 R:R ────────────────────────────────────
SL_PIPS = 13
TP_PIPS = 26

# Both sessions share same SL/TP
SESSION_TP_SL = {
    "London": {"tp": TP_PIPS, "sl": SL_PIPS},
    "NY":     {"tp": TP_PIPS, "sl": SL_PIPS},
}

# SGD pip value: EUR/USD 1 pip per 10k units ≈ SGD 1.35 (at ~1.35 USDSGD)
SGD_PER_PIP_PER_10K = 1.35   # 50k units = SGD 6.75/pip

ASSETS = {
    "EUR_USD": {
        "instrument": "EUR_USD",
        "asset":      "EURUSD",
        "emoji":      "🇪🇺",
        "pip":        0.0001,
        "precision":  5,
        "stop_pips":  SL_PIPS,
        "tp_pips":    TP_PIPS,
        # v5 windows: 15:00-19:00 London | 20:00-00:00 NY
        # NY 20:00–00:00 = hours 20,21,22,23 (midnight handled as hour<1 in get_active_session)
        "sessions": [
            {"start": 15, "end": 19, "max_spread": 1.5, "label": "London"},
            {"start": 20, "end": 24, "max_spread": 1.5, "label": "NY"},    # 24 = midnight boundary
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


def usd_to_sgd(amount):
    """Account is natively SGD — balance/PnL from OANDA API is already in SGD."""
    return round(amount, 2)


def get_h4_direction():
    """Check current H4 trend for smart flip detection after consecutive SL hits."""
    try:
        api_key  = os.environ.get("OANDA_API_KEY", "")
        base_url = "https://api-fxpractice.oanda.com"
        headers  = {"Authorization": "Bearer " + api_key}
        url      = base_url + "/v3/instruments/EUR_USD/candles"
        params   = {"count": "55", "granularity": "H4", "price": "M"}
        r        = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return None
        candles  = [x for x in r.json()["candles"] if x["complete"]]
        closes   = [float(x["mid"]["c"]) for x in candles]
        if len(closes) < 52:
            return None
        seed = sum(closes[:50]) / 50
        ema  = seed
        mult = 2 / 51
        for c in closes[50:]:
            ema = (c - ema) * mult + ema
        last3 = closes[-3:]
        if all(c > ema for c in last3):
            return "BUY"
        elif all(c < ema for c in last3):
            return "SELL"
        return None
    except Exception as e:
        log.warning("get_h4_direction error: " + str(e))
        return None


def get_active_session(hour):
    """
    Returns active session config or None.
    NY window is 20:00–00:00 SGT, so hour 0 (midnight) is still NY.
    """
    cfg = ASSETS["EUR_USD"]
    for s in cfg["sessions"]:
        start = s["start"]
        end   = s["end"]
        # Handle NY midnight boundary: end=24 means hours 20,21,22,23
        if end == 24:
            if start <= hour <= 23:
                return s
        else:
            if start <= hour < end:
                return s
    return None


def is_in_session(hour, cfg):
    for s in cfg["sessions"]:
        start = s["start"]
        end   = s["end"]
        if end == 24:
            if start <= hour <= 23:
                return True
        else:
            if start <= hour < end:
                return True
    return False


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
    """Detect closed trades and fire TP/SL alerts."""
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
                trade       = data[0]
                pnl_usd     = float(trade.get("realizedPL", "0"))
                pnl_sgd     = usd_to_sgd(pnl_usd)
                open_price  = float(trade.get("price", 0))
                close_price = float(trade.get("averageClosePrice", open_price))
                balance_sgd = usd_to_sgd(trader.get_balance())
                wins        = state.get("wins", 0)
                losses      = state.get("losses", 0)

                state["daily_pnl"] = state.get("daily_pnl", 0.0) + pnl_usd

                if pnl_usd < 0:
                    set_cooldown(state, name)
                    state["losses"]        = losses + 1
                    consec = state.get("consec_losses", 0) + 1
                    state["consec_losses"] = consec
                    alert.send_sl_hit(pnl_usd, pnl_sgd, balance_sgd,
                                      state["wins"], state["losses"],
                                      open_price, close_price)

                    # ── SMART FLIP / CIRCUIT BREAKER ──────────────────────
                    if consec >= 2:
                        from datetime import timedelta
                        last_dir   = state.get("last_trade_direction", "")
                        h4_dir_now = get_h4_direction()
                        log.info("Smart flip — last=" + last_dir + " H4=" + str(h4_dir_now))
                        if h4_dir_now and last_dir and h4_dir_now != last_dir:
                            state["consec_losses"] = 0
                            state.pop("pause_until", None)
                            log.info("H4 FLIPPED " + last_dir + "→" + h4_dir_now + " — resuming")
                            alert.send(
                                "🔄 TREND FLIP DETECTED\n"
                                "H4: " + last_dir + " → " + h4_dir_now + "\n"
                                "Resuming immediately in new direction.\n"
                                "No pause — market shifted, not choppy."
                            )
                        else:
                            pause_dt = datetime.now(timezone.utc) + timedelta(days=2)
                            state["pause_until"] = pause_dt.isoformat()
                            state["consec_losses"] = 0
                            log.warning("CIRCUIT BREAKER — same H4 dir, pausing 2 days")
                            alert.send(
                                "⛔ CIRCUIT BREAKER\n"
                                "2 SL hits, H4 direction unchanged (" + str(h4_dir_now) + ").\n"
                                "Pausing 2 days — ranging/choppy market.\n"
                                "Resumes automatically."
                            )
                else:
                    state["wins"]          = wins + 1
                    state["consec_losses"] = 0
                    alert.send_tp_hit(pnl_usd, pnl_sgd, balance_sgd,
                                      state["wins"], state["losses"],
                                      open_price, close_price)
        except Exception as e:
            log.warning("SL/TP detect error " + name + ": " + str(e))


def check_session_open_alerts(state, alert, trader, now, today):
    """Send session open alert once per window per day."""
    hour    = now.hour
    windows = [
        {"start": 15, "label": "London", "hours": "15:00–19:00 SGT"},
        {"start": 20, "label": "NY",     "hours": "20:00–00:00 SGT"},
    ]
    for w in windows:
        if hour == w["start"]:
            akey = "session_open_" + today + "_" + w["label"]
            if not state.get("session_alerted", {}).get(akey):
                if "session_alerted" not in state:
                    state["session_alerted"] = {}
                state["session_alerted"][akey] = True

                state["session_trades_" + w["label"]] = 0
                state["session_pnl_" + w["label"]]    = 0.0

                try:
                    balance_usd = trader.get_balance() if trader.login() else state.get("start_balance", 0)
                except:
                    balance_usd = state.get("start_balance", 0)
                balance_sgd = usd_to_sgd(balance_usd)

                alert.send_session_open(
                    session_label=w["label"],
                    session_hours=w["hours"],
                    balance_sgd=balance_sgd,
                    trades_today=state.get("trades", 0),
                    wins=state.get("wins", 0),
                    losses=state.get("losses", 0),
                )


def check_session_close_alerts(state, alert, trader, now, today):
    """Send session close alert when a window ends."""
    hour    = now.hour
    windows = [
        {"end": 19, "label": "London"},
        {"end": 0,  "label": "NY"},      # NY closes at midnight (00:00)
    ]
    for w in windows:
        if hour == w["end"] and now.minute == 0:
            akey = "session_close_" + today + "_" + w["label"]
            if not state.get("session_alerted", {}).get(akey):
                if "session_alerted" not in state:
                    state["session_alerted"] = {}
                state["session_alerted"][akey] = True
                try:
                    balance_usd = trader.get_balance() if trader.login() else state.get("start_balance", 0)
                except:
                    balance_usd = state.get("start_balance", 0)
                balance_sgd     = usd_to_sgd(balance_usd)
                session_pnl_sgd = usd_to_sgd(state.get("session_pnl_" + w["label"], 0.0))
                alert.send_session_close(
                    session_label=w["label"],
                    balance_sgd=balance_sgd,
                    session_trades=state.get("session_trades_" + w["label"], 0),
                    session_pnl_sgd=session_pnl_sgd,
                    wins=state.get("wins", 0),
                    losses=state.get("losses", 0),
                )


def run_bot(state):
    settings = load_settings()
    now      = datetime.now(sg_tz)
    hour     = now.hour
    today    = now.strftime("%Y%m%d")
    alert    = TelegramAlert()
    calendar = CalendarFilter()

    log.info("Scan at " + now.strftime("%H:%M:%S SGT"))

    trader_for_alerts = OandaTrader(demo=settings["demo_mode"])
    check_session_open_alerts(state, alert, trader_for_alerts, now, today)
    check_session_close_alerts(state, alert, trader_for_alerts, now, today)

    session = get_active_session(hour)
    if not session:
        log.info("Outside trading windows (" + str(hour) + "h SGT) — London 15-19 | NY 20-00")
        return

    log.info("Window: " + session["label"] + " | Max spread: " + str(session["max_spread"]) + " pip")

    trader = OandaTrader(demo=settings["demo_mode"])
    if not trader.login():
        fail_key = _login_fail_key(now)
        if not state.get("login_fail_alerted", {}).get(fail_key):
            if "login_fail_alerted" not in state:
                state["login_fail_alerted"] = {}
            state["login_fail_alerted"][fail_key] = True
            api_key    = os.environ.get("OANDA_API_KEY", "")
            account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
            alert.send_login_fail(
                api_key_hint=api_key[:8] + "****" if api_key else "MISSING",
                account_id=account_id
            )
        else:
            log.warning("Login failed — alert already sent this 30-min window")
        return

    current_balance_usd = trader.get_balance()
    current_balance_sgd = usd_to_sgd(current_balance_usd)

    if "start_balance" not in state or state["start_balance"] == 0.0:
        state["start_balance"] = current_balance_usd

    detect_sl_tp_hits(state, trader, alert)

    # ── HARD CLOSE (MAX_DURATION) ──────────────────────────────────────
    for name in ASSETS:
        if name not in state.get("open_times", {}):
            continue

        pos = trader.get_position(name)
        if not pos:
            log.info(name + ": no open position — clearing open_times")
            state.get("open_times", {}).pop(name, None)
            continue

        try:
            trade_id, open_str = trader.get_open_trade_id(name)
            if not trade_id or not open_str:
                log.info(name + ": no open trade ID — clearing open_times")
                state.get("open_times", {}).pop(name, None)
                continue

            open_utc = datetime.fromisoformat(open_str.replace("Z", "+00:00"))
            mins     = (datetime.now(pytz.utc) - open_utc).total_seconds() / 60
            log.info(name + ": open " + str(round(mins, 1)) + " min")

            if mins >= MAX_DURATION:
                pnl_usd = trader.check_pnl(pos)
                pnl_sgd = usd_to_sgd(pnl_usd)
                result  = trader.close_position(name)
                state.get("open_times", {}).pop(name, None)
                log.info(name + ": timeout close attempted — open_times cleared")

                if result.get("success"):
                    alert.send_timeout_close(
                        minutes=mins,
                        pnl_usd=pnl_usd,
                        pnl_sgd=pnl_sgd,
                        balance_sgd=current_balance_sgd,
                    )
                else:
                    log.warning(name + ": timeout close API failed — " + str(result.get("error", "")))
        except Exception as e:
            state.get("open_times", {}).pop(name, None)
            log.warning("Duration check " + name + ": " + str(e) + " — open_times cleared")

    # ── CIRCUIT BREAKER CHECK ────────────────────────────────────────
    pause_until = state.get("pause_until")
    if pause_until:
        try:
            remaining = (datetime.fromisoformat(pause_until) -
                         datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                days_left = round(remaining / 86400, 1)
                log.info("Circuit breaker active — " + str(days_left) + " days remaining")
                return
            else:
                state.pop("pause_until", None)
                log.info("Circuit breaker expired — resuming")
        except Exception:
            state.pop("pause_until", None)
            log.info("Circuit breaker cleared (stale) — resuming")

    # ── FRIDAY CUTOFF ────────────────────────────────────────────────
    import calendar as cal_mod
    if now.weekday() == 4 and now.hour >= 23:
        log.info("Friday 23:00 SGT+ — no new trades (weekend risk).")
        return

    # ── v5: NO TRADE LIMIT — trade all valid setups ──────────────────
    # WIN-STOP removed. Bot trades every valid signal in session windows.
    # Circuit breaker (2 SL hits) is the only safety stop.
    log.info("v5: No trade limit — scanning all setups")

    # ── SCAN + TRADE ───────────────────────────────────────────────────
    threshold = settings.get("signal_threshold", 4)

    for name, cfg in ASSETS.items():

        pos = trader.get_position(name)
        if pos:
            pnl_sgd = usd_to_sgd(trader.check_pnl(pos))
            dirn    = "BUY" if int(float(pos.get("long", {}).get("units", 0))) > 0 else "SELL"
            log.info(name + ": " + dirn + " open | Unrealised SGD " + str(pnl_sgd))
            continue

        if in_cooldown(state, name):
            log.info(name + ": cooldown " + str(cooldown_remaining(state, name)) + "min")
            continue

        price, bid, ask = trader.get_price(name)
        if price is None:
            log.warning(name + ": price error")
            continue

        spread = (ask - bid) / cfg["pip"]
        if spread > session["max_spread"] + 0.05:
            log.info(name + ": spread " + str(round(spread, 2)) + "p — skip (max " + str(session["max_spread"]) + "p)")
            continue

        news_active, news_reason = calendar.is_news_time(name)
        if news_active:
            alert_key = name + "_news_" + now.strftime("%Y%m%d%H")
            if not state.get("news_alerted", {}).get(alert_key):
                if "news_alerted" not in state:
                    state["news_alerted"] = {}
                state["news_alerted"][alert_key] = True
                alert.send_news_block(name, news_reason)
            log.info(name + ": news — " + news_reason)
            continue

        result = signals.analyze(asset=cfg["asset"], state=state)
        if len(result) == 4:
            score, direction, details, layer_breakdown = result
        else:
            score, direction, details = result
            layer_breakdown = {}

        log.info(name + ": score=" + str(score) + "/" + str(threshold) +
                 " dir=" + direction + " | " + details)

        if score < threshold or direction == "NONE":
            log.info(name + ": no setup — waiting (score " + str(score) + "/" + str(threshold) + ")")
            continue

        # ── Place trade ────────────────────────────────────────────────
        sess_tpsl = SESSION_TP_SL.get(session["label"], {"tp": TP_PIPS, "sl": SL_PIPS})
        use_tp    = sess_tpsl["tp"]
        use_sl    = sess_tpsl["sl"]
        sl_sgd    = round((TRADE_SIZE / 10000) * use_sl * SGD_PER_PIP_PER_10K, 2)
        tp_sgd    = round((TRADE_SIZE / 10000) * use_tp * SGD_PER_PIP_PER_10K, 2)

        result_order = trader.place_order(
            instrument=name, direction=direction, size=TRADE_SIZE,
            stop_distance=use_sl, limit_distance=use_tp
        )
        if result_order["success"]:
            state["trades"] = state.get("trades", 0) + 1
            if "open_times" not in state:
                state["open_times"] = {}
            state["open_times"][name] = now.isoformat()
            state["last_trade_direction"] = direction

            sess_key = "session_trades_" + session["label"]
            state[sess_key] = state.get(sess_key, 0) + 1

            price_now, _, _ = trader.get_price(name)
            entry_price = price_now if price_now else price

            alert.send_trade_open(
                direction=direction,
                entry_price=entry_price,
                sl_pips=use_sl,
                tp_pips=use_tp,
                sl_sgd=sl_sgd,
                tp_sgd=tp_sgd,
                spread=spread,
                score=score,
                session_label=session["label"],
                layer_breakdown=layer_breakdown,
                balance_sgd=current_balance_sgd,
                trades_today=state["trades"],
            )
            log.info(name + ": PLACED " + direction + " SL=" + str(use_sl) + "p TP=" + str(use_tp) + "p | SGD SL=" + str(sl_sgd) + " TP=" + str(tp_sgd))
        else:
            set_cooldown(state, name)
            log.warning(name + ": order failed — " + str(result_order.get("error", "")))

    log.info("Scan complete.")

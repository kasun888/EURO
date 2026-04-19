"""
Railway Entry Point - OANDA EUR/USD London+NY Session Scalp Bot
================================================================
Windows (SGT):
  15:00–19:00 SGT — London Open (EUR/USD prime window)
  20:00–00:00 SGT — NY Session  (USD flows, second best)

FIX LOG (inherited from GBP bot):
  FIX-01: Login FAILED alert suppressed outside session windows
  FIX-02: Login fail alert deduplicated per 30-min window
  FIX-03: Startup Telegram sent so you know bot is alive
  FIX-04: Session open alert sent once per window per day
  FIX-05: Crash loop protection — 30s sleep on unhandled exception
  FIX-06: No trade limit enforced in bot.py

EUR/USD CHANGES:
  - Signal threshold: 4/4 (vs 3/3 for GBP — more confirmation needed)
  - Windows shifted to London 15:00-19:00 + NY 20:00-00:00 SGT
  - Day reset handles midnight NY window correctly
"""

import os, time, logging, traceback
from datetime import datetime
import pytz

from bot            import run_bot, ASSETS, is_in_session
from oanda_trader   import OandaTrader
from telegram_alert import TelegramAlert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger(__name__)

INTERVAL_MINUTES = 5
sg_tz            = pytz.timezone("Asia/Singapore")
STATE            = {}


def get_today_key():
    return datetime.now(sg_tz).strftime("%Y%m%d")


def fresh_day_state(today_str, balance):
    return {
        "date":               today_str,
        "trades":             0,
        "start_balance":      balance,
        "daily_pnl":          0.0,
        "stopped":            False,
        "wins":               0,
        "losses":             0,
        "consec_losses":      0,
        "cooldowns":          {},
        "open_times":         {},
        "news_alerted":       {},
        "session_alerted":    {},
        "login_fail_alerted": {},
    }


def check_env_vars():
    api_key    = os.environ.get("OANDA_API_KEY", "")
    account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
    tg_token   = os.environ.get("TELEGRAM_TOKEN", "")
    tg_chat    = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not api_key or not account_id:
        log.error("=" * 50)
        log.error("❌ MISSING OANDA ENV VARS!")
        log.error("   OANDA_API_KEY    : " + ("SET ✅" if api_key    else "MISSING ❌"))
        log.error("   OANDA_ACCOUNT_ID : " + ("SET ✅" if account_id else "MISSING ❌"))
        log.error("=" * 50)
        return False

    log.info("Env vars OK | Key: " + api_key[:8] + "**** | Account: " + account_id)
    if not tg_token or not tg_chat:
        log.warning("Telegram not configured — no alerts will be sent")
    return True


def is_any_session_now():
    now  = datetime.now(sg_tz)
    hour = now.hour
    return any(is_in_session(hour, cfg) for cfg in ASSETS.values())


def check_session_open_alerts(alert):
    """Send one alert when each window opens for the day."""
    now   = datetime.now(sg_tz)
    hour  = now.hour
    today = now.strftime("%Y%m%d")

    windows = [
        {"start": 15, "label": "London", "desc": "15:00–19:00 SGT"},
        {"start": 20, "label": "NY",     "desc": "20:00–00:00 SGT"},
    ]

    for w in windows:
        if hour == w["start"]:
            akey = "session_open_" + today + "_" + w["label"]
            if not STATE.get("session_alerted", {}).get(akey):
                if "session_alerted" not in STATE:
                    STATE["session_alerted"] = {}
                STATE["session_alerted"][akey] = True
                balance = STATE.get("start_balance", 0.0)
                alert.send(
                    "🔔 " + w["label"] + " Window Open!\n"
                    "⏰ " + now.strftime("%H:%M SGT") + " (" + w["desc"] + ")\n"
                    "Balance: $" + str(round(balance, 2)) + "\n"
                    "Scanning EUR/USD..."
                )


def main():
    global STATE

    log.info("=" * 50)
    log.info("🚀 Railway Bot Started - OANDA EUR/USD London+NY Scalp")
    log.info("Window 1: 15:00–19:00 SGT (London) | Window 2: 20:00–00:00 SGT (NY)")
    log.info("EUR/USD | SL=13pip | TP=26pip | Signal: 4/4 | No trade limit")
    log.info("=" * 50)

    if not check_env_vars():
        log.error("Missing env vars — sleeping 60s then exiting")
        time.sleep(60)
        return

    alert = TelegramAlert()
    alert.send(
        "🚀 EUR/USD Bot Started!\n"
        "Pair: EUR/USD\n"
        "SL: 13 pip | TP: 26 pip | 2:1 R:R\n"
        "Signal: 4/4 (H4+H1+M15+M5)\n"
        "Window 1: 15:00–19:00 SGT (London)\n"
        "Window 2: 20:00–00:00 SGT (NY)\n"
        "No trade limit"
    )

    while True:
        try:
            now   = datetime.now(sg_tz)
            today = now.strftime("%Y%m%d")
            log.info("⏰ " + now.strftime("%Y-%m-%d %H:%M SGT"))

            # Day reset
            if STATE.get("date") != today:
                log.info("📅 New day! Fetching balance...")
                try:
                    trader  = OandaTrader(demo=True)
                    balance = trader.get_balance() if trader.login() else 0.0
                except Exception as e:
                    log.warning("Balance fetch error: " + str(e))
                    balance = 0.0
                log.info("📅 New day! Balance: $" + str(round(balance, 2)))
                STATE = fresh_day_state(today, balance)

            # Session open alerts
            check_session_open_alerts(alert)

            run_bot(state=STATE)

        except Exception as e:
            log.error("❌ Bot error: " + str(e))
            log.error(traceback.format_exc())
            time.sleep(30)

        log.info("💤 Sleeping " + str(INTERVAL_MINUTES) + " mins...")
        time.sleep(INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()

"""
Railway Entry Point — EUR/USD Bot v5
======================================
Windows (SGT):
  15:00–19:00 SGT — London Open
  20:00–00:00 SGT — NY Session

v5 CHANGES:
  ✅ SL: 13 pip | TP: 26 pip | 2:1 R:R
  ✅ Signal: 4/4 (H4+H1+M15+M5) — H1 L1 loosened for more setups
  ✅ No trade limit
  ✅ Startup message updated
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
# FIX6: load persisted state from disk on startup (survives Railway restarts)
from bot import load_state, save_state as _save_state
_disk_state = load_state()
STATE = _disk_state if _disk_state else {}


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


def main():
    global STATE

    log.info("=" * 50)
    log.info("🚀 EUR/USD Bot v5 Started!")
    log.info("Pair: EUR/USD | SL: 13pip | TP: 26pip | R:R 2:1")
    log.info("Signal: 4/4 (H4+H1+M15+M5) | No trade limit")
    log.info("Window 1: 15:00–19:00 SGT (London)")
    log.info("Window 2: 20:00–00:00 SGT (NY)")
    log.info("=" * 50)

    if not check_env_vars():
        log.error("Missing env vars — sleeping 60s then exiting")
        time.sleep(60)
        return

    alert = TelegramAlert()
    alert.send(
        "🚀 EUR/USD Bot v5 Started!\n"
        "──────────────────────\n"
        "Pair: EUR/USD\n"
        "SL: 13 pip | TP: 26 pip | 2:1 R:R\n"
        "Signal: 4/4 (H4+H1+M15+M5)\n"
        "Window 1: 15:00–19:00 SGT (London)\n"
        "Window 2: 20:00–00:00 SGT (NY)\n"
        "No trade limit\n"
        "v5: H1 L1 loosened for more setups"
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
                    from bot import load_settings as _ls
                    _demo = _ls().get("demo_mode", True)
                    trader  = OandaTrader(demo=_demo)  # FIX-BUG2: respect settings.json demo_mode
                    balance = trader.get_balance() if trader.login() else 0.0
                except Exception as e:
                    log.warning("Balance fetch error: " + str(e))
                    balance = 0.0
                log.info("📅 New day! Balance: $" + str(round(balance, 2)))
                STATE = fresh_day_state(today, balance)

            run_bot(state=STATE)

        except Exception as e:
            log.error("❌ Bot error: " + str(e))
            log.error(traceback.format_exc())
            time.sleep(30)

        log.info("💤 Sleeping " + str(INTERVAL_MINUTES) + " mins...")
        time.sleep(INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()

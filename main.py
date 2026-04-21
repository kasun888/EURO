"""
Railway Entry Point — EUR/USD Multi-Session Scalp Bot V4
=========================================================
Sessions:
  Asian/London  06:00–09:00 UTC  (14:00–17:00 SGT)
  London        09:00–12:00 UTC  (17:00–20:00 SGT)
  NY            13:00–16:00 UTC  (21:00–00:00 SGT)
Strategy: Triple-Confirm Trend Scalp
  SL=8 pips | TP=12 pips | R:R 1.5 | Max 45 min | 2 trades/day

Scans every 5 minutes via Railway cron.
Sends Telegram alerts on trade open / TP / SL / timeout.
"""

import os
import time
import logging
import traceback
from datetime import datetime

import pytz

from bot            import run_bot, ASSET, get_active_session
from oanda_trader   import OandaTrader
from telegram_alert import TelegramAlert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger(__name__)

INTERVAL_MINUTES = 5
sg_tz            = pytz.timezone("Asia/Singapore")
STATE            = {}


def get_today():
    return datetime.now(sg_tz).strftime("%Y%m%d")


def fresh_day_state(today_str, balance):
    return {
        "date":            today_str,
        "trades":          0,
        "start_balance":   balance,
        "wins":            0,
        "losses":          0,
        "consec_losses":   0,
        "cooldown_until":  None,
        "daily_trades":    {},
        "open_times":      {},
        "news_alerted":    {},
        "session_alerted": {},
    }


def check_env():
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


def main():
    global STATE

    log.info("=" * 55)
    log.info("🚀 EUR/USD Bot V4 Started — All Sessions")
    log.info("Asian/London: 06-09 UTC (14-17 SGT)")
    log.info("London:       09-12 UTC (17-20 SGT)")
    log.info("NY:           13-16 UTC (21-00 SGT)")
    log.info("SL=8p | TP=12p | R:R=1.5 | Max 45 min | 2 trades/day")
    log.info("=" * 55)

    if not check_env():
        log.error("Missing env vars — sleeping 60s then exiting")
        time.sleep(60)
        return

    alert = TelegramAlert()
    alert.send(
        "🚀 EUR/USD Bot V4 Started!\n"
        "Strategy: Triple-Confirm Trend Scalp\n"
        "Pair:     EUR/USD\n"
        "SL: 8 pip | TP: 12 pip | R:R: 1.5\n"
        "Sessions: Asian/London 14-17 SGT\n"
        "          London       17-20 SGT\n"
        "          NY           21-00 SGT\n"
        "Max:      2 trades/day | 45 min hold"
    )

    while True:
        try:
            now   = datetime.now(sg_tz)
            today = now.strftime("%Y%m%d")

            log.info("⏰ " + now.strftime("%Y-%m-%d %H:%M SGT"))

            # Day reset
            if STATE.get("date") != today:
                log.info("📅 New day — resetting state")
                try:
                    trader  = OandaTrader(demo=True)
                    balance = trader.get_balance() if trader.login() else 0.0
                except Exception as e:
                    log.warning("Balance fetch error: " + str(e))
                    balance = 0.0
                log.info("Balance: $" + str(round(balance, 2)))
                STATE = fresh_day_state(today, balance)

            run_bot(state=STATE)

        except Exception as e:
            log.error("❌ Bot error: " + str(e))
            log.error(traceback.format_exc())
            time.sleep(30)

        log.info("💤 Sleeping " + str(INTERVAL_MINUTES) + " min...")
        time.sleep(INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()

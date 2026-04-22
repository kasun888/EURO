"""
Railway Entry Point — EUR/USD Multi-Session Scalp Bot V7-PLUS
==============================================================
Sessions: Asian (01-06 UTC) | London (07-12 UTC) | NY (13-16 UTC)
Strategy: V7-PLUS — 4-layer trend confirm
  SL=7 pips (approx SGD 70) | TP=10 pips (approx SGD 100) | R:R 1.43
  Max 1 trade/session | 45 min hold | 15 min cooldown after loss

All Telegram alerts show SGD amounts and live balance.
"""

import os
import time
import logging
import traceback
from datetime import datetime

import pytz

from bot            import run_bot, ASSET, get_active_session, SESSIONS
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
        "session_trades":  {},
        "cooldown_until":  None,
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
        log.error("MISSING OANDA ENV VARS!")
        log.error("   OANDA_API_KEY    : " + ("SET" if api_key    else "MISSING"))
        log.error("   OANDA_ACCOUNT_ID : " + ("SET" if account_id else "MISSING"))
        log.error("=" * 50)
        return False

    log.info("Env vars OK | Key: " + api_key[:8] + "**** | Account: " + account_id)
    if not tg_token or not tg_chat:
        log.warning("Telegram not configured — no alerts will be sent")
    return True


def main():
    global STATE

    session_labels = " | ".join(
        s["label"] + " " + s["sgt_label"] for s in SESSIONS
    )
    log.info("=" * 60)
    log.info("EUR/USD Bot V7-PLUS Started")
    log.info("Sessions: " + session_labels)
    log.info("SL=7p (approx SGD 70) | TP=10p (approx SGD 100) | R:R=1.43")
    log.info("Max 1 trade/session | 45 min hold | 15 min cooldown")
    log.info("=" * 60)

    if not check_env():
        log.error("Missing env vars — sleeping 60s then exiting")
        time.sleep(60)
        return

    alert = TelegramAlert()
    alert.send(
        "EUR/USD Bot V7-PLUS Started!\n"
        "Strategy: 4-Layer Multi-Session Scalp\n"
        "Pair:     EUR/USD\n"
        "SL: 7 pip (approx SGD 70)\n"
        "TP: 10 pip (approx SGD 100)\n"
        "R:R: 1.43\n"
        "Sessions: Asian | London | NY\n"
        "Max: 1 trade/session | 45 min hold"
    )

    while True:
        try:
            now   = datetime.now(sg_tz)
            today = now.strftime("%Y%m%d")

            log.info("  " + now.strftime("%Y-%m-%d %H:%M SGT"))

            # Day reset
            if STATE.get("date") != today:
                log.info("New day — resetting state")
                try:
                    trader  = OandaTrader(demo=True)
                    balance = trader.get_balance() if trader.login() else 0.0
                except Exception as e:
                    log.warning("Balance fetch error: " + str(e))
                    balance = 0.0
                log.info("Balance: SGD " + str(round(balance * 1.35, 2)))
                # Preserve cooldown across midnight reset (in case trade is still open)
                prev_cooldown = STATE.get("cooldown_until")
                prev_open_times = STATE.get("open_times", {})
                STATE = fresh_day_state(today, balance)
                if prev_cooldown:
                    STATE["cooldown_until"] = prev_cooldown
                if prev_open_times:
                    STATE["open_times"] = prev_open_times

            run_bot(state=STATE)

        except Exception as e:
            log.error("Bot error: " + str(e))
            log.error(traceback.format_exc())
            time.sleep(30)

        log.info("Sleeping " + str(INTERVAL_MINUTES) + " min...")
        time.sleep(INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()

"""
Telegram Alert System — EUR/USD Bot v5
Full alert methods for all trade events.
"""
import os
import requests
import logging

log = logging.getLogger(__name__)


class TelegramAlert:
    def __init__(self):
        self.token   = os.environ.get("TELEGRAM_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    def send(self, message: str):
        if not self.token or not self.chat_id:
            log.warning("Telegram not configured — TELEGRAM_TOKEN or TELEGRAM_CHAT_ID missing")
            return False
        try:
            url  = f"https://api.telegram.org/bot{self.token}/sendMessage"
            text = f"🤖 EUR/USD v5\n{'─'*22}\n{message}"
            data = {"chat_id": self.chat_id, "text": text}
            r    = requests.post(url, data=data, timeout=10)
            if r.status_code == 200:
                log.info("Telegram sent!")
                return True
            log.warning(f"Telegram error {r.status_code}: {r.text[:200]}")
            return False
        except Exception as e:
            log.error(f"Telegram error: {e}")
            return False

    def send_trade_open(self, direction, entry_price, sl_pips, tp_pips,
                        sl_sgd, tp_sgd, spread, score, session_label,
                        layer_breakdown=None, balance_sgd=0, trades_today=0):
        arrow = "📈 BUY" if direction == "BUY" else "📉 SELL"
        msg = (
            f"{arrow} EUR/USD\n"
            f"Entry:   {entry_price:.5f}\n"
            f"SL:      {sl_pips} pip (SGD {sl_sgd})\n"
            f"TP:      {tp_pips} pip (SGD {tp_sgd})\n"
            f"R:R:     2:1\n"
            f"Spread:  {round(spread, 2)} pip\n"
            f"Signal:  {score}/4\n"
            f"Session: {session_label}\n"
            f"Balance: SGD {balance_sgd}\n"
            f"Trades:  #{trades_today} today"
        )
        self.send(msg)

    def send_tp_hit(self, pnl_usd, pnl_sgd, balance_sgd, wins, losses,
                   open_price, close_price):
        msg = (
            f"✅ TP HIT — EUR/USD\n"
            f"P&L:     +SGD {abs(pnl_sgd)}\n"
            f"Entry:   {open_price:.5f}\n"
            f"Exit:    {close_price:.5f}\n"
            f"Balance: SGD {balance_sgd}\n"
            f"W/L:     {wins}W / {losses}L"
        )
        self.send(msg)

    def send_sl_hit(self, pnl_usd, pnl_sgd, balance_sgd, wins, losses,
                   open_price, close_price):
        msg = (
            f"❌ SL HIT — EUR/USD\n"
            f"P&L:     -SGD {abs(pnl_sgd)}\n"
            f"Entry:   {open_price:.5f}\n"
            f"Exit:    {close_price:.5f}\n"
            f"Balance: SGD {balance_sgd}\n"
            f"W/L:     {wins}W / {losses}L"
        )
        self.send(msg)

    def send_timeout_close(self, minutes, pnl_usd, pnl_sgd, balance_sgd):
        sign = "+" if pnl_sgd >= 0 else "-"
        msg = (
            f"⏱️ TIMEOUT CLOSE — EUR/USD\n"
            f"Duration: {round(minutes, 1)} min\n"
            f"P&L:      {sign}SGD {abs(pnl_sgd)}\n"
            f"Balance:  SGD {balance_sgd}"
        )
        self.send(msg)

    def send_session_open(self, session_label, session_hours, balance_sgd,
                          trades_today, wins, losses):
        msg = (
            f"🔔 {session_label} Window Open!\n"
            f"⏰ {session_hours}\n"
            f"Balance: SGD {balance_sgd}\n"
            f"Today:   {trades_today} trades | {wins}W {losses}L\n"
            f"Scanning EUR/USD..."
        )
        self.send(msg)

    def send_session_close(self, session_label, balance_sgd, session_trades,
                           session_pnl_sgd, wins, losses):
        sign = "+" if session_pnl_sgd >= 0 else ""
        msg = (
            f"🔕 {session_label} Window Closed\n"
            f"Trades:  {session_trades}\n"
            f"P&L:     {sign}SGD {session_pnl_sgd}\n"
            f"Balance: SGD {balance_sgd}\n"
            f"W/L:     {wins}W / {losses}L"
        )
        self.send(msg)

    def send_login_fail(self, api_key_hint, account_id):
        msg = (
            f"⚠️ OANDA Login Failed!\n"
            f"API Key: {api_key_hint}\n"
            f"Account: {account_id}\n"
            f"Check Railway env vars."
        )
        self.send(msg)

    def send_news_block(self, instrument, reason):
        msg = (
            f"📰 NEWS BLACKOUT\n"
            f"Pair:   {instrument}\n"
            f"Reason: {reason}\n"
            f"Pausing ±30 min around event."
        )
        self.send(msg)

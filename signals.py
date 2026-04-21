"""
Signal Engine — Strategy V4 "Triple-Confirm Trend Scalp"
=========================================================
Pair:     EUR/USD ONLY
Sessions: All 3 active windows (handled by bot.py session gate)
  06:00–09:00 UTC  Asian/London overlap  (14:00–17:00 SGT)
  09:00–12:00 UTC  London peak           (17:00–20:00 SGT)
  13:00–16:00 UTC  NY open               (21:00–00:00 SGT)

TP:  12 pips  |  SL: 8 pips  |  R:R: 1.5
Max hold: 45 min

SIGNAL LOGIC (4 layers):
  L0  H4  EMA50      → macro direction (BUY / SELL)
  L1  H4  ATR(14)    → >6 pips (trending, not flat)
  L2  H1  EMA20+EMA50→ price on correct side of BOTH EMAs
       H1  RSI(14)   → 30–70 (not extreme)
       H1  ATR(14)   → >4.5 pips (session active)
  L3  M15 EMA9/EMA21 → EMA9 above EMA21 (bull) or below (bear)
       M15 RSI(14)   → 38–62 (momentum zone, not overextended)
       M15 ATR(14)   → >4.5 pips
  L4  M5  EMA9       → close above (buy) / below (sell) EMA9
       M5  body      → ≥50% of candle range in direction
       M5  RSI(14)   → 35–65 (no overextended entry)
"""

import os
import logging
import requests

log = logging.getLogger(__name__)


class SafeFilter(logging.Filter):
    def __init__(self):
        self.api_key = os.environ.get("OANDA_API_KEY", "")

    def filter(self, record):
        if self.api_key and self.api_key in str(record.getMessage()):
            record.msg = record.msg.replace(self.api_key, "***")
        return True


log.addFilter(SafeFilter())


class SignalEngine:
    def __init__(self):
        self.api_key  = os.environ.get("OANDA_API_KEY", "")
        self.base_url = "https://api-fxpractice.oanda.com"
        self.headers  = {"Authorization": "Bearer " + self.api_key}

    # ─────────────────────────────────────────────────────────────────
    # DATA FETCHING
    # ─────────────────────────────────────────────────────────────────

    def _fetch_candles(self, instrument, granularity, count=60):
        url    = self.base_url + "/v3/instruments/" + instrument + "/candles"
        params = {"count": str(count), "granularity": granularity, "price": "M"}
        for attempt in range(3):
            try:
                r = requests.get(url, headers=self.headers,
                                 params=params, timeout=10)
                if r.status_code == 200:
                    c = [x for x in r.json()["candles"] if x["complete"]]
                    return (
                        [float(x["mid"]["c"]) for x in c],
                        [float(x["mid"]["h"]) for x in c],
                        [float(x["mid"]["l"]) for x in c],
                        [float(x["mid"]["o"]) for x in c],
                    )
                log.warning("Candle " + granularity + " attempt " +
                            str(attempt + 1) + " HTTP " + str(r.status_code))
            except Exception as e:
                log.warning("Candle fetch error: " + str(e))
        return [], [], [], []

    # ─────────────────────────────────────────────────────────────────
    # INDICATORS
    # ─────────────────────────────────────────────────────────────────

    def _ema(self, data, period):
        if not data:
            return [0.0]
        if len(data) < period:
            return [sum(data) / len(data)] * len(data)
        seed = sum(data[:period]) / period
        emas = [seed] * period
        mult = 2 / (period + 1)
        for p in data[period:]:
            emas.append((p - emas[-1]) * mult + emas[-1])
        return emas

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gains.append(max(delta, 0))
            losses.append(max(-delta, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))

    def _atr(self, highs, lows, closes, period=14):
        if len(highs) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i]  - closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    # ─────────────────────────────────────────────────────────────────
    # MAIN SIGNAL
    # ─────────────────────────────────────────────────────────────────

    def analyze(self, asset="EURUSD", state=None):
        """
        Returns (score, direction, reason_string).
        score=4 + direction != "NONE"  →  fire trade.
        Works for all sessions — session gating is handled by bot.py.
        """
        return self._v4_signal("EUR_USD")

    def _v4_signal(self, instrument):
        reasons = []

        # ── L0: H4 EMA50 macro direction ─────────────────────────────
        h4_c, h4_h, h4_l, _ = self._fetch_candles(instrument, "H4", 60)
        if len(h4_c) < 51:
            return 0, "NONE", "Not enough H4 data (" + str(len(h4_c)) + ")"

        h4_ema50 = self._ema(h4_c, 50)[-1]
        h4_price = h4_c[-1]

        if h4_price > h4_ema50:
            direction = "BUY"
            reasons.append("✅ L0 H4 BUY — price " + str(round(h4_price, 5)) +
                            " above EMA50=" + str(round(h4_ema50, 5)))
        elif h4_price < h4_ema50:
            direction = "SELL"
            reasons.append("✅ L0 H4 SELL — price " + str(round(h4_price, 5)) +
                            " below EMA50=" + str(round(h4_ema50, 5)))
        else:
            return 0, "NONE", "H4 EMA50 flat — no macro direction"

        # ── L1: H4 ATR > 6 pip — trending market filter ───────────────
        h4_atr_pip = self._atr(h4_h, h4_l, h4_c, 14) / 0.0001
        if h4_atr_pip < 6.0:
            msg = ("🚫 L1 FAIL — H4 ATR=" + str(round(h4_atr_pip, 1)) +
                   "p < 6.0p (choppy market, skip)")
            log.info(instrument + ": " + msg)
            return 1, "NONE", " | ".join(reasons) + " | " + msg
        reasons.append("✅ L1 H4 ATR=" + str(round(h4_atr_pip, 1)) + "p OK")

        # ── L2: H1 alignment — price above/below EMA20 AND EMA50 ──────
        h1_c, h1_h, h1_l, _ = self._fetch_candles(instrument, "H1", 30)
        if len(h1_c) < 10:
            return 1, "NONE", " | ".join(reasons) + " | Not enough H1 data"

        h1_ema20   = self._ema(h1_c, 20)[-1]
        h1_ema50   = self._ema(h1_c, 50)[-1]
        h1_rsi     = self._rsi(h1_c, 14)
        h1_atr_pip = self._atr(h1_h, h1_l, h1_c, 14) / 0.0001
        h1_price   = h1_c[-1]

        if h1_atr_pip < 4.5:
            msg = ("🚫 L2 FAIL — H1 ATR=" + str(round(h1_atr_pip, 1)) +
                   "p < 4.5p (session quiet)")
            return 1, "NONE", " | ".join(reasons) + " | " + msg

        if not (30 < h1_rsi < 70):
            msg = ("🚫 L2 FAIL — H1 RSI=" + str(round(h1_rsi, 1)) +
                   " extreme (skip)")
            return 1, "NONE", " | ".join(reasons) + " | " + msg

        # Price must be on correct side of BOTH EMA20 and EMA50
        bull_h1 = (h1_price > h1_ema20) and (h1_price > h1_ema50)
        bear_h1 = (h1_price < h1_ema20) and (h1_price < h1_ema50)

        if direction == "BUY" and not bull_h1:
            msg = ("L2 FAIL — H1 not above both EMAs: price=" +
                   str(round(h1_price, 5)) +
                   " EMA20=" + str(round(h1_ema20, 5)) +
                   " EMA50=" + str(round(h1_ema50, 5)))
            return 1, "NONE", " | ".join(reasons) + " | " + msg
        if direction == "SELL" and not bear_h1:
            msg = ("L2 FAIL — H1 not below both EMAs: price=" +
                   str(round(h1_price, 5)) +
                   " EMA20=" + str(round(h1_ema20, 5)) +
                   " EMA50=" + str(round(h1_ema50, 5)))
            return 1, "NONE", " | ".join(reasons) + " | " + msg

        reasons.append("✅ L2 H1 aligned EMA20=" + str(round(h1_ema20, 5)) +
                       " EMA50=" + str(round(h1_ema50, 5)) +
                       " RSI=" + str(round(h1_rsi, 1)) +
                       " ATR=" + str(round(h1_atr_pip, 1)) + "p")

        # ── L3: M15 ongoing EMA trend + tighter RSI ───────────────────
        m15_c, m15_h, m15_l, _ = self._fetch_candles(instrument, "M15", 30)
        if len(m15_c) < 10:
            return 2, "NONE", " | ".join(reasons) + " | Not enough M15 data"

        m15_ema9  = self._ema(m15_c, 9)[-1]
        m15_ema21 = self._ema(m15_c, 21)[-1]
        m15_rsi   = self._rsi(m15_c, 14)
        m15_atr_p = self._atr(m15_h, m15_l, m15_c, 14) / 0.0001

        if m15_atr_p < 4.5:
            msg = ("🚫 L3 FAIL — M15 ATR=" + str(round(m15_atr_p, 1)) +
                   "p < 4.5p")
            return 2, "NONE", " | ".join(reasons) + " | " + msg

        # Tighter RSI: 38–62 (filters out late/overextended entries)
        if not (38 < m15_rsi < 62):
            msg = ("🚫 L3 FAIL — M15 RSI=" + str(round(m15_rsi, 1)) +
                   " outside 38–62")
            return 2, "NONE", " | ".join(reasons) + " | " + msg

        m15_bull = m15_ema9 > m15_ema21
        m15_bear = m15_ema9 < m15_ema21

        if direction == "BUY" and not m15_bull:
            msg = ("L3 FAIL — M15 EMA9=" + str(round(m15_ema9, 5)) +
                   " < EMA21=" + str(round(m15_ema21, 5)))
            return 2, "NONE", " | ".join(reasons) + " | " + msg
        if direction == "SELL" and not m15_bear:
            msg = ("L3 FAIL — M15 EMA9=" + str(round(m15_ema9, 5)) +
                   " > EMA21=" + str(round(m15_ema21, 5)))
            return 2, "NONE", " | ".join(reasons) + " | " + msg

        reasons.append("✅ L3 M15 EMA9=" + str(round(m15_ema9, 5)) +
                       (" > " if m15_bull else " < ") +
                       "EMA21=" + str(round(m15_ema21, 5)) +
                       " RSI=" + str(round(m15_rsi, 1)))

        # ── L4: M5 entry — close vs EMA9 + strong body + RSI ──────────
        m5_c, m5_h, m5_l, m5_o = self._fetch_candles(instrument, "M5", 15)
        if len(m5_c) < 5:
            return 3, "NONE", " | ".join(reasons) + " | Not enough M5 data"

        m5_ema9    = self._ema(m5_c, 9)[-1]
        m5_rsi     = self._rsi(m5_c, 14)
        last_c     = m5_c[-1]
        last_o     = m5_o[-1]
        last_h     = m5_h[-1]
        last_l     = m5_l[-1]
        candle_rng = max(last_h - last_l, 0.00001)

        # M5 RSI — not overextended on entry
        if not (35 < m5_rsi < 65):
            msg = ("L4 FAIL — M5 RSI=" + str(round(m5_rsi, 1)) +
                   " outside 35–65")
            return 3, "NONE", " | ".join(reasons) + " | " + msg

        # Body ≥50% of range in direction
        bull_body = (last_c > last_o) and \
                    ((last_c - last_l) / candle_rng >= 0.50)
        bear_body = (last_c < last_o) and \
                    ((last_h - last_c) / candle_rng >= 0.50)

        # Close on correct side of M5 EMA9
        bull_ema9 = last_c > m5_ema9
        bear_ema9 = last_c < m5_ema9

        if direction == "BUY" and bull_body and bull_ema9:
            reasons.append("✅ L4 M5 BUY close=" + str(round(last_c, 5)) +
                           " > EMA9=" + str(round(m5_ema9, 5)) +
                           " body=" + str(round(
                               (last_c - last_l) / candle_rng * 100)) + "%" +
                           " RSI=" + str(round(m5_rsi, 1)))
        elif direction == "SELL" and bear_body and bear_ema9:
            reasons.append("✅ L4 M5 SELL close=" + str(round(last_c, 5)) +
                           " < EMA9=" + str(round(m5_ema9, 5)) +
                           " body=" + str(round(
                               (last_h - last_c) / candle_rng * 100)) + "%" +
                           " RSI=" + str(round(m5_rsi, 1)))
        else:
            msg = ("L4 FAIL — M5 EMA9=" + str(round(m5_ema9, 5)) +
                   " close=" + str(round(last_c, 5)) +
                   " bull_body=" + str(bull_body) +
                   " bear_body=" + str(bear_body) +
                   " bull_ema9=" + str(bull_ema9) +
                   " bear_ema9=" + str(bear_ema9) +
                   " RSI=" + str(round(m5_rsi, 1)))
            return 3, "NONE", " | ".join(reasons) + " | " + msg

        # ── ALL 4 LAYERS PASSED ───────────────────────────────────────
        log.info(instrument + ": ✅ ALL LAYERS PASSED — firing " + direction)
        return 4, direction, " | ".join(reasons)

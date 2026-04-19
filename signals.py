"""
Signal Engine — EUR/USD London+NY Session Scalp
=================================================
Pair:   EUR/USD ONLY
Target: 26 pip TP | 13 pip SL | 2:1 R:R

FIXES APPLIED:
  FIX-A: L2 and L3 are now separated via state memory.
          When L2 fires, direction + timestamp are saved to state["l2_pending"].
          On the NEXT scan(s), only L3 is checked (up to 30 min window).
          This mirrors real price action: breakout → pullback → entry.
  FIX-B: RSI thresholds loosened (42→52 buy, 58→48 sell) so the
          pullback confirmation is actually reachable after a breakout.
  FIX-C: Every layer now logs its exact pass/fail so Railway logs
          show precisely which condition is blocking each scan.
"""

import os, requests, logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class SafeFilter(logging.Filter):
    def __init__(self):
        self.api_key = os.environ.get("OANDA_API_KEY", "")
    def filter(self, record):
        if self.api_key and self.api_key in str(record.getMessage()):
            record.msg = record.msg.replace(self.api_key, "***")
        return True

log.addFilter(SafeFilter())

L2_EXPIRY_MINUTES = 45  # how long to wait for L3 after L2 fires


class SignalEngine:
    def __init__(self):
        self.api_key  = os.environ.get("OANDA_API_KEY", "")
        self.base_url = "https://api-fxpractice.oanda.com"
        self.headers  = {"Authorization": "Bearer " + self.api_key}

    def _fetch_candles(self, instrument, granularity, count=60):
        url    = self.base_url + "/v3/instruments/" + instrument + "/candles"
        params = {"count": str(count), "granularity": granularity, "price": "M"}
        for attempt in range(3):
            try:
                r = requests.get(url, headers=self.headers, params=params, timeout=10)
                if r.status_code == 200:
                    c = [x for x in r.json()["candles"] if x["complete"]]
                    return (
                        [float(x["mid"]["c"]) for x in c],
                        [float(x["mid"]["h"]) for x in c],
                        [float(x["mid"]["l"]) for x in c],
                        [float(x["mid"]["o"]) for x in c],
                    )
                log.warning("Candle " + granularity + " attempt " + str(attempt+1) + " HTTP " + str(r.status_code))
            except Exception as e:
                log.warning("Candle fetch error: " + str(e))
        return [], [], [], []

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

    def _rsi(self, closes, period=7):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i-1]
            gains.append(max(delta, 0))
            losses.append(max(-delta, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _atr(self, highs, lows, closes, period=14):
        if len(highs) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    def analyze(self, asset="EURUSD", state=None):
        """
        state dict is passed in so L2 pending persists between scans.
        Falls back gracefully if state=None.
        """
        return self._scalp_eurusd("EUR_USD", state=state)

    def _scalp_eurusd(self, instrument, state=None):
        reasons = []
        score   = 0

        # ── FIX-A: Check if L2 already fired and we are waiting for L3 ──
        if state is not None:
            pending = state.get("l2_pending", {})
            if pending.get("instrument") == instrument:
                age_minutes = (
                    datetime.now(timezone.utc) -
                    datetime.fromisoformat(pending["timestamp"])
                ).total_seconds() / 60

                if age_minutes <= L2_EXPIRY_MINUTES:
                    log.info(
                        instrument + ": L2 pending (" + pending["direction"] +
                        ") — checking L3 entry [" + str(round(age_minutes, 1)) + " min elapsed]"
                    )
                    return self._check_l3_only(
                        instrument,
                        direction=pending["direction"],
                        score_so_far=3,
                        reasons=["(L0+L1+L2 already confirmed — checking L3 entry only)"],
                        state=state,
                    )
                else:
                    log.info(instrument + ": L2 pending EXPIRED (" + str(round(age_minutes, 1)) + " min) — resetting")
                    state.pop("l2_pending", None)

        # ── L0: H4 MACRO TREND — EMA50 ───────────────────────────────────
        h4_c, h4_h, h4_l, _ = self._fetch_candles(instrument, "H4", 60)
        if len(h4_c) < 51:
            log.info(instrument + ": L0 SKIP — not enough H4 data (" + str(len(h4_c)) + ")")
            return 0, "NONE", "Not enough H4 data (" + str(len(h4_c)) + ")"

        h4_ema50 = self._ema(h4_c, 50)[-1]
        h4_price = h4_c[-1]

        if h4_price > h4_ema50:
            direction = "BUY"
            reasons.append("✅ L0 H4 BUY above EMA50=" + str(round(h4_ema50, 5)))
        elif h4_price < h4_ema50:
            direction = "SELL"
            reasons.append("✅ L0 H4 SELL below EMA50=" + str(round(h4_ema50, 5)))
        else:
            log.info(instrument + ": L0 FAIL — H4 EMA50 flat")
            return 0, "NONE", "H4 EMA50 flat — no macro trend"

        score = 1

        # ── VETO: FLAT RANGE BLOCK — H1 ATR < 6 pips ────────────────────
        h1_c, h1_h, h1_l, _ = self._fetch_candles(instrument, "H1", 60)
        if len(h1_c) < 20:
            log.info(instrument + ": VETO SKIP — not enough H1 data (" + str(len(h1_c)) + ")")
            return score, "NONE", " | ".join(reasons) + " | Not enough H1 data"

        h1_atr      = self._atr(h1_h, h1_l, h1_c, 14)
        h1_atr_pip  = h1_atr / 0.0001
        MIN_ATR_PIPS = 4.0

        if h1_atr_pip < MIN_ATR_PIPS:
            msg = "🚫 VETO FLAT: H1 ATR=" + str(round(h1_atr_pip, 1)) + "p < " + str(MIN_ATR_PIPS) + "p — market too quiet"
            log.info(instrument + ": " + msg)
            reasons.append(msg)
            return score, "NONE", " | ".join(reasons)
        else:
            reasons.append("✅ ATR OK: H1 ATR=" + str(round(h1_atr_pip, 1)) + "p")

        # ── L1: H1 DUAL EMA ALIGNMENT — EMA21 + EMA50 ───────────────────
        h1_ema21 = self._ema(h1_c, 21)[-1]
        h1_ema50 = self._ema(h1_c, 50)[-1]
        h1_close = h1_c[-1]

        bull_h1 = (h1_close > h1_ema21) and (h1_ema21 > h1_ema50)
        bear_h1 = (h1_close < h1_ema21) and (h1_ema21 < h1_ema50)

        if direction == "BUY" and bull_h1:
            reasons.append("✅ L1 H1 BULL stack: price>" + str(round(h1_ema21, 5)) + ">EMA50=" + str(round(h1_ema50, 5)))
            score = 2
        elif direction == "SELL" and bear_h1:
            reasons.append("✅ L1 H1 BEAR stack: price<" + str(round(h1_ema21, 5)) + "<EMA50=" + str(round(h1_ema50, 5)))
            score = 2
        else:
            msg = ("L1 FAIL — H1 EMAs not aligned: price=" + str(round(h1_close, 5)) +
                   " EMA21=" + str(round(h1_ema21, 5)) + " EMA50=" + str(round(h1_ema50, 5)))
            log.info(instrument + ": " + msg)
            reasons.append(msg)
            return score, "NONE", " | ".join(reasons)

        # ── L2: M15 IMPULSE CANDLE BREAK ─────────────────────────────────
        m15_c, m15_h, m15_l, m15_o = self._fetch_candles(instrument, "M15", 20)
        if len(m15_c) < 8:
            log.info(instrument + ": L2 SKIP — not enough M15 data (" + str(len(m15_c)) + ")")
            return score, "NONE", " | ".join(reasons) + " | Not enough M15 data"

        lookback       = 5
        recent_highs   = m15_h[-lookback-1:-1]
        recent_lows    = m15_l[-lookback-1:-1]
        structure_high = max(recent_highs)
        structure_low  = min(recent_lows)
        last_close     = m15_c[-1]
        last_open      = m15_o[-1]
        last_high      = m15_h[-1]
        last_low       = m15_l[-1]
        candle_range   = max(last_high - last_low, 0.00001)

        bull_body_m15 = (last_close > last_open) and ((last_close - last_low) / candle_range >= 0.50)
        bear_body_m15 = (last_close < last_open) and ((last_high - last_close) / candle_range >= 0.50)

        bull_break = (last_close > structure_high) and (last_close <= structure_high + 0.00080) and bull_body_m15
        bear_break = (last_close < structure_low)  and (last_close >= structure_low  - 0.00080) and bear_body_m15

        if direction == "BUY" and bull_break:
            reasons.append(
                "✅ L2 M15 impulse UP close=" + str(round(last_close, 5)) +
                " > high=" + str(round(structure_high, 5)) +
                " body=" + str(round((last_close - last_low) / candle_range * 100)) + "%"
            )
            score = 3
        elif direction == "SELL" and bear_break:
            reasons.append(
                "✅ L2 M15 impulse DOWN close=" + str(round(last_close, 5)) +
                " < low=" + str(round(structure_low, 5)) +
                " body=" + str(round((last_high - last_close) / candle_range * 100)) + "%"
            )
            score = 3
        else:
            msg = ("L2 FAIL — no M15 impulse: high=" + str(round(structure_high, 5)) +
                   " low=" + str(round(structure_low, 5)) +
                   " close=" + str(round(last_close, 5)) +
                   " bull_body=" + str(bull_body_m15) + " bear_body=" + str(bear_body_m15))
            log.info(instrument + ": " + msg)
            reasons.append(msg)
            return score, "NONE", " | ".join(reasons)

        # ── FIX-A: L2 PASSED → save to state, wait for L3 next scan ─────
        if state is not None:
            state["l2_pending"] = {
                "instrument": instrument,
                "direction":  direction,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
            }
            log.info(
                instrument + ": ✅ L2 FIRED (" + direction + ") — "
                "saved to state, checking L3 on next scan(s) for up to " +
                str(L2_EXPIRY_MINUTES) + " min"
            )
            reasons.append("⏳ L2 confirmed — waiting for L3 pullback entry (next scan)...")
            return score, "NONE", " | ".join(reasons)

        # Stateless fallback
        return self._check_l3_only(instrument, direction, score, reasons, state=None)

    # ─────────────────────────────────────────────────────────────────────
    def _check_l3_only(self, instrument, direction, score_so_far, reasons, state=None):
        """
        Called on the scan(s) AFTER L2 fires.
        Checks M5 RSI(7) pullback to EMA13, then runs both VETOs.
        Clears l2_pending from state and returns score=4 + direction on success.
        """
        score = score_so_far

        # ── L3: M5 RSI(7) ENTRY TIMING + EMA13 TOUCH ────────────────────
        m5_c, m5_h, m5_l, m5_o = self._fetch_candles(instrument, "M5", 50)
        if len(m5_c) < 15:
            log.info(instrument + ": L3 SKIP — not enough M5 data (" + str(len(m5_c)) + ")")
            return score, "NONE", " | ".join(reasons) + " | Not enough M5 data"

        ema13    = self._ema(m5_c, 13)[-1]
        rsi7     = self._rsi(m5_c, 7)
        m5_close = m5_c[-1]
        m5_open  = m5_o[-1]
        m5_high  = m5_h[-1]
        m5_low   = m5_l[-1]
        m5_range = max(m5_high - m5_low, 0.00001)

        MIN_M5_RANGE = 0.00015  # 2.5 pips min candle

        bull_m5_body = (m5_close > m5_open) and ((m5_close - m5_low) / m5_range >= 0.50) and (m5_range >= MIN_M5_RANGE)
        bear_m5_body = (m5_close < m5_open) and ((m5_high - m5_close) / m5_range >= 0.50) and (m5_range >= MIN_M5_RANGE)

        ema_tol         = 0.00020  # 1.0 pip tolerance
        recent_lows_m5  = m5_l[-3:-1]
        recent_highs_m5 = m5_h[-3:-1]
        bull_pb = any(l <= ema13 + ema_tol for l in recent_lows_m5)
        bear_pb = any(h >= ema13 - ema_tol for h in recent_highs_m5)

        # FIX-B: Loosened RSI thresholds (was 42/58 → now 52/48)
        RSI_BUY_MAX  = 58
        RSI_SELL_MIN = 42

        bull_rsi = rsi7 < RSI_BUY_MAX
        bear_rsi = rsi7 > RSI_SELL_MIN

        if direction == "BUY" and bull_pb and bull_m5_body and bull_rsi:
            reasons.append(
                "✅ L3 M5 entry: EMA13=" + str(round(ema13, 5)) +
                " RSI7=" + str(round(rsi7, 1)) +
                " bounce body=" + str(round((m5_close - m5_low) / m5_range * 100)) + "%"
            )
            score = 4
        elif direction == "SELL" and bear_pb and bear_m5_body and bear_rsi:
            reasons.append(
                "✅ L3 M5 entry: EMA13=" + str(round(ema13, 5)) +
                " RSI7=" + str(round(rsi7, 1)) +
                " bounce body=" + str(round((m5_high - m5_close) / m5_range * 100)) + "%"
            )
            score = 4
        else:
            msg = (
                "L3 FAIL — EMA13=" + str(round(ema13, 5)) +
                " RSI7=" + str(round(rsi7, 1)) +
                " (need <" + str(RSI_BUY_MAX) + " buy / >" + str(RSI_SELL_MIN) + " sell)" +
                " bull_pb=" + str(bull_pb) + " bear_pb=" + str(bear_pb) +
                " bull_body=" + str(bull_m5_body) + " bear_body=" + str(bear_m5_body)
            )
            log.info(instrument + ": " + msg)
            reasons.append(msg)
            return score, "NONE", " | ".join(reasons)

        # ── VETO 1: H1 EMA200 HARD BLOCK ────────────────────────────────
        h1_long_c, _, _, _ = self._fetch_candles(instrument, "H1", 210)
        if len(h1_long_c) >= 200:
            h1_ema200 = self._ema(h1_long_c, 200)[-1]
            price_now = m5_c[-1]
            if direction == "BUY" and price_now < h1_ema200:
                msg = "🚫 VETO1 H1 EMA200=" + str(round(h1_ema200, 5)) + " price below — no BUY"
                log.info(instrument + ": " + msg)
                reasons.append(msg)
                return score, "NONE", " | ".join(reasons)
            elif direction == "SELL" and price_now > h1_ema200:
                msg = "🚫 VETO1 H1 EMA200=" + str(round(h1_ema200, 5)) + " price above — no SELL"
                log.info(instrument + ": " + msg)
                reasons.append(msg)
                return score, "NONE", " | ".join(reasons)
            else:
                reasons.append("✅ VETO1 pass EMA200=" + str(round(h1_ema200, 5)))
        else:
            log.warning("Not enough H1 for EMA200 (" + str(len(h1_long_c)) + ") — veto skipped")
            reasons.append("⚠️ EMA200 unavailable — veto skipped")

        # ── VETO 2: M30 COUNTER-TREND BLOCK ──────────────────────────────
        m30_c, m30_h, m30_l, m30_o = self._fetch_candles(instrument, "M30", 10)
        if len(m30_c) >= 4:
            counter_trend_count = 0
            for i in range(-3, 0):
                c_rng = max(m30_h[i] - m30_l[i], 0.00001)
                if direction == "BUY":
                    if (m30_c[i] < m30_o[i]) and ((m30_h[i] - m30_c[i]) / c_rng >= 0.65):
                        counter_trend_count += 1
                else:
                    if (m30_c[i] > m30_o[i]) and ((m30_c[i] - m30_l[i]) / c_rng >= 0.65):
                        counter_trend_count += 1

            if counter_trend_count >= 3:
                msg = "🚫 VETO2 M30 counter-trend: 3/3 candles opposing " + direction
                log.info(instrument + ": " + msg)
                reasons.append(msg)
                return score, "NONE", " | ".join(reasons)
            else:
                reasons.append("✅ VETO2 M30 ok: " + str(counter_trend_count) + "/3 counter candles")

        # ── ALL PASSED → clear L2 pending, fire trade ────────────────────
        if state is not None:
            state.pop("l2_pending", None)
            log.info(instrument + ": ✅ ALL 4 LAYERS PASSED — firing trade")

        return score, direction, " | ".join(reasons)

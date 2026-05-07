"""
Hybrid Scalp v1.0 — Signal Engine
===================================
EUR/USD London + NY Scalper

BEST OF BOTH BOTS:
  From EURO-USD bot  — Multi-timeframe confluence (H4→H1→M15→M5)
                       Chaos filter, ATR veto, Smart flip detection
  From Fiber Scalp   — ORB time-decay bonus, CPR bias bonus,
                       Score-based risk, H1 score-aware filter,
                       Clean modular architecture

SCORING (max 6):
  L0  H4 EMA50 macro trend          +1  (direction lock)
  ATR H1 ATR > 2.5 pip              +1  (momentum gate)
  L1  H1 dual EMA aligned           +1  (intermediate trend)
  L2  M15 impulse break             +1  (entry trigger — saved, awaits L3)
  L3  M5 EMA13 pullback + RSI       +1  (precision entry)
  BON ORB break OR CPR bias         +1  (confluence bonus)

Threshold: 5/6 for highest quality, 4/6 minimum

FILTERS:
  Chaos      — daily range > 200 pip → skip (news shock)
  H1 filter  — score-aware (score 4 needs H1 alignment, 5/6 allows neutral)
  Circuit    — 2 consecutive SL → pause 2 days OR smart flip resume
  Win-stop   — 1 win per day then stop
"""

import time
import logging
from datetime import datetime, timezone

import pytz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

_SGT = pytz.timezone("Asia/Singapore")

# ── Parameters (tuned from both bots) ─────────────────────────────────────────
MIN_ATR_PIPS      = 2.5       # H1 ATR gate — Fiber Scalp loosened value
CHAOS_THRESHOLD   = 200.0     # daily range pip limit — EURO-USD bot
L2_EXPIRY_MINUTES = 90        # M15 break stays valid — Fiber Scalp value
L2_BREAK_BUFFER   = 0.00150   # 15 pip tolerance for M15 break
RSI_BUY_MAX       = 65        # M5 RSI ceiling for BUY
RSI_SELL_MIN      = 35        # M5 RSI floor for SELL
EMA_TOL           = 0.00020   # 2 pip EMA13 touch tolerance
MIN_M5_RANGE      = 0.00010   # minimum M5 candle body
ORB_FRESH_MINUTES = 60        # ORB break bonus: fresh window
ORB_AGING_MINUTES = 120       # ORB break bonus: aging window


def _make_session() -> requests.Session:
    retries = Retry(total=3, connect=3, read=3, backoff_factor=0.5,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retries)
    s = requests.Session()
    s.mount("https://", adapter)
    return s


class SignalEngine:
    def __init__(self, demo: bool = True):
        from config_loader import load_secrets
        secrets      = load_secrets()
        self.api_key = secrets.get("OANDA_API_KEY", "")
        self.base_url = ("https://api-fxpractice.oanda.com" if demo
                         else "https://api-fxtrade.oanda.com")
        self.headers  = {"Authorization": f"Bearer {self.api_key}"}
        self.session  = _make_session()

    # ── Public entry point ────────────────────────────────────────────────────

    def analyze(self, instrument: str = "EUR_USD",
                settings: dict | None = None,
                state:    dict | None = None):
        """
        Returns (score, direction, details, levels, position_usd).
        levels contains full breakdown for Telegram + database.
        """
        s = settings or {}
        return self._run(instrument, s, state)

    # ── Main pipeline ─────────────────────────────────────────────────────────

    def _run(self, instrument: str, settings: dict, state: dict | None):
        reasons = []
        layer   = {}

        # ── Check if L2 already fired — skip straight to L3 ──────────────
        if state is not None:
            pending = state.get("l2_pending", {})
            if pending.get("instrument") == instrument:
                age_min = (
                    datetime.now(timezone.utc) -
                    datetime.fromisoformat(pending["timestamp"])
                ).total_seconds() / 60
                if age_min <= L2_EXPIRY_MINUTES:
                    log.info("%s: L2 pending (%s) %.1f min — checking L3",
                             instrument, pending["direction"], age_min)
                    return self._l3_only(
                        instrument, pending["direction"],
                        score_so_far=pending.get("score", 4),    # FIX L2-SCORE: use saved score not hardcoded 3
                        reasons=["(L0+ATR+L1+L2 confirmed — checking L3 only)"],
                        state=state, settings=settings,
                        h1_trend=pending.get("h1_trend", "UNKNOWN"),  # FIX H1T-PENDING: use saved h1_trend
                    )
                else:
                    log.info("%s: L2 pending expired (%.1f min) — resetting", instrument, age_min)
                    state.pop("l2_pending", None)

        # ── L0: H4 EMA50 macro trend ──────────────────────────────────────
        h4_c, h4_h, h4_l, _ = self._candles(instrument, "H4", 60)
        if len(h4_c) < 53:
            return 0, "NONE", "Insufficient H4 data", {"L0": "⚠️ NO DATA"}, 0

        ema50_series = self._ema(h4_c, 50)
        h4_ema50     = ema50_series[-1]
        h4_price     = h4_c[-1]

        if h4_price > h4_ema50:
            direction = "BUY"
            reasons.append(f"✅ L0 H4 BUY | price={h4_price:.5f} > EMA50={h4_ema50:.5f}")
            layer["L0"] = "✅ H4 BUY"
        elif h4_price < h4_ema50:
            direction = "SELL"
            reasons.append(f"✅ L0 H4 SELL | price={h4_price:.5f} < EMA50={h4_ema50:.5f}")
            layer["L0"] = "✅ H4 SELL"
        else:
            return 0, "NONE", "H4 EMA50 flat", {"L0": "❌ FLAT"}, 0

        score = 1

        # ── ATR gate + Chaos filter ───────────────────────────────────────
        h1_c, h1_h, h1_l, _ = self._candles(instrument, "H1", 60)
        if len(h1_c) < 55:   # FIX EMA-GUARD: need 50+ candles for valid EMA50
            return (score, "NONE", " | ".join(reasons) + " | No H1 data",
                    {**layer, "ATR": "⚠️ NO DATA"}, 0)

        h1_atr_pip   = self._atr(h1_h, h1_l, h1_c, 14) / 0.0001
        today_high   = max(h1_h[-8:])
        today_low    = min(h1_l[-8:])
        daily_range  = (today_high - today_low) / 0.0001

        if daily_range > CHAOS_THRESHOLD:
            msg = f"🚫 CHAOS: range={daily_range:.0f}p > {CHAOS_THRESHOLD:.0f}p — news day"
            reasons.append(msg)
            layer["CHAOS"] = f"❌ {daily_range:.0f}p"
            return score, "NONE", " | ".join(reasons), {**layer}, 0

        if h1_atr_pip < MIN_ATR_PIPS:
            msg = f"🚫 ATR={h1_atr_pip:.1f}p < {MIN_ATR_PIPS}p — market too quiet"
            reasons.append(msg)
            layer["ATR"] = f"❌ {h1_atr_pip:.1f}p"
            return score, "NONE", " | ".join(reasons), {**layer}, 0

        reasons.append(f"✅ ATR={h1_atr_pip:.1f}p | range={daily_range:.0f}p")
        layer["ATR"] = f"✅ {h1_atr_pip:.1f}p"
        score = 2

        # ── L1: H1 dual EMA alignment ─────────────────────────────────────
        h1_ema21 = self._ema(h1_c, 21)[-1]
        h1_ema50 = self._ema(h1_c, 50)[-1]
        h1_close = h1_c[-1]

        bull_h1 = h1_close > h1_ema21 > h1_ema50
        bear_h1 = h1_close < h1_ema21 < h1_ema50

        if (direction == "BUY"  and bull_h1) or (direction == "SELL" and bear_h1):
            if direction == "BUY":
                reasons.append(f"✅ L1 H1 BULL: price>{h1_ema21:.5f}>EMA50={h1_ema50:.5f}")
            else:
                reasons.append(f"✅ L1 H1 BEAR: price<{h1_ema21:.5f}<EMA50={h1_ema50:.5f}")
            layer["L1"] = "✅ H1 aligned"
            score = 3
        else:
            reasons.append("❌ L1 H1 EMAs not aligned")
            layer["L1"] = "❌ not aligned"
            return score, "NONE", " | ".join(reasons), {**layer}, 0

        # ── H1 trend for H1 score-aware filter (returned in levels) ──────
        h1_trend = "BULLISH" if h1_close > h1_ema21 else ("BEARISH" if h1_close < h1_ema21 else "FLAT")

        # ── L2: M15 impulse break ─────────────────────────────────────────
        m15_c, m15_h, m15_l, m15_o = self._candles(instrument, "M15", 20)
        if len(m15_c) < 8:
            return (score, "NONE", " | ".join(reasons) + " | No M15 data",
                    {**layer, "L2": "⚠️ NO DATA"}, 0)

        struct_high  = max(m15_h[-6:-1])
        struct_low   = min(m15_l[-6:-1])
        last_c, last_o = m15_c[-1], m15_o[-1]
        last_h, last_l = m15_h[-1], m15_l[-1]
        c_range = max(last_h - last_l, 0.00001)
        bull_body = last_c > last_o and (last_c - last_l) / c_range >= 0.50
        bear_body = last_c < last_o and (last_h - last_c) / c_range >= 0.50
        bull_break = last_c > struct_high and last_c <= struct_high + L2_BREAK_BUFFER and bull_body
        bear_break = last_c < struct_low  and last_c >= struct_low  - L2_BREAK_BUFFER and bear_body

        if (direction == "BUY" and bull_break) or (direction == "SELL" and bear_break):
            pct = round((last_c - last_l) / c_range * 100) if direction == "BUY" else round((last_h - last_c) / c_range * 100)
            reasons.append(f"✅ L2 M15 {'BREAK UP' if direction=='BUY' else 'BREAK DOWN'} body={pct}%")
            layer["L2"] = "✅ M15 break — awaiting L3"
            score = 4

            # Save L2 state and wait for L3 next scan
            if state is not None:
                state["l2_pending"] = {
                    "instrument": instrument,
                    "direction":  direction,
                    "timestamp":  datetime.now(timezone.utc).isoformat(),
                    "h1_trend":   h1_trend,   # FIX H1T-PENDING: save so _l3_only can use it
                    "score":      score,       # FIX L2-SCORE: save actual score (4) not hardcoded 3
                }
                reasons.append(f"⏳ L2 confirmed — awaiting L3 pullback (up to {L2_EXPIRY_MINUTES}min)")
                _h1_rel = ("aligned" if (
                    (direction == "BUY"  and h1_trend == "BULLISH") or
                    (direction == "SELL" and h1_trend == "BEARISH")
                ) else ("neutral" if h1_trend in ("FLAT", "UNKNOWN") else "opposite"))
                levels = {**layer, "h1_trend": h1_trend,
                          "h1_aligned": _h1_rel == "aligned",
                          "h1_relation": _h1_rel}
                return score, "NONE", " | ".join(reasons), levels, 0
        else:
            reasons.append("❌ L2 no M15 impulse break")
            layer["L2"] = "❌ no break"
            return score, "NONE", " | ".join(reasons), {**layer}, 0

        return self._l3_only(instrument, direction, score, reasons, state, settings,
                             h1_trend=h1_trend, layer=layer)

    # ── L3 check (used both inline and from L2 pending) ───────────────────

    def _l3_only(self, instrument, direction, score_so_far, reasons, state, settings,
                 h1_trend="UNKNOWN", layer: dict | None = None):
        score  = score_so_far
        layer  = dict(layer) if layer else {}   # FIX L3-LAYER: preserve L0/ATR/L1/L2 context
        s      = settings or {}

        m5_c, m5_h, m5_l, m5_o = self._candles(instrument, "M5", 50)
        if len(m5_c) < 15:
            return (score, "NONE", " | ".join(reasons) + " | No M5 data",
                    {**layer, "L3": "⚠️ NO DATA"}, 0)

        ema13    = self._ema(m5_c, 13)[-1]
        rsi7     = self._rsi(m5_c, 7)
        m5_close = m5_c[-1]
        m5_open  = m5_o[-1]
        m5_high  = m5_h[-1]
        m5_low   = m5_l[-1]
        m5_range = max(m5_high - m5_low, 0.00001)

        bull_body = (m5_close > m5_open and (m5_close - m5_low) / m5_range >= 0.50
                     and m5_range >= MIN_M5_RANGE)
        bear_body = (m5_close < m5_open and (m5_high - m5_close) / m5_range >= 0.50
                     and m5_range >= MIN_M5_RANGE)

        recent_lows  = m5_l[-3:-1]
        recent_highs = m5_h[-3:-1]
        bull_pb  = any(l <= ema13 + EMA_TOL for l in recent_lows)
        bear_pb  = any(h >= ema13 - EMA_TOL for h in recent_highs)
        bull_rsi = rsi7 < RSI_BUY_MAX
        bear_rsi = rsi7 > RSI_SELL_MIN
        rsi_str  = f"RSI7={rsi7:.1f}"

        if direction == "BUY"  and bull_pb and bull_body and bull_rsi:
            reasons.append(f"✅ L3 M5 bounce EMA13={ema13:.5f} {rsi_str}")
            layer["L3"] = f"✅ M5 bounce {rsi_str}"
            score = 5
        elif direction == "SELL" and bear_pb and bear_body and bear_rsi:
            reasons.append(f"✅ L3 M5 bounce EMA13={ema13:.5f} {rsi_str}")
            layer["L3"] = f"✅ M5 bounce {rsi_str}"
            score = 5
        else:
            fails = []
            if direction == "BUY":
                if not bull_pb:   fails.append("no EMA touch")
                if not bull_body: fails.append("weak body")
                if not bull_rsi:  fails.append(f"RSI {rsi7:.1f}>={RSI_BUY_MAX}")
            else:
                if not bear_pb:   fails.append("no EMA touch")
                if not bear_body: fails.append("weak body")
                if not bear_rsi:  fails.append(f"RSI {rsi7:.1f}<={RSI_SELL_MIN}")
            reasons.append("❌ L3 fail — " + ", ".join(fails))
            layer["L3"] = "❌ " + ", ".join(fails)
            return score, "NONE", " | ".join(reasons), {**layer, "h1_trend": h1_trend}, 0

        # ── VETO: H1 EMA200 ───────────────────────────────────────────────
        h1_long_c, _, _, _ = self._candles(instrument, "H1", 210)
        if len(h1_long_c) >= 200:
            h1_ema200 = self._ema(h1_long_c, 200)[-1]
            price_now = m5_c[-1]
            if direction == "BUY"  and price_now < h1_ema200:
                reasons.append(f"🚫 VETO EMA200={h1_ema200:.5f} — BUY below EMA200")
                return score, "NONE", " | ".join(reasons), {**layer, "h1_trend": h1_trend}, 0
            elif direction == "SELL" and price_now > h1_ema200:
                reasons.append(f"🚫 VETO EMA200={h1_ema200:.5f} — SELL above EMA200")
                return score, "NONE", " | ".join(reasons), {**layer, "h1_trend": h1_trend}, 0
            reasons.append(f"✅ EMA200={h1_ema200:.5f} ok")

        # ── BONUS: ORB break (+1) ─────────────────────────────────────────
        orb_bonus    = self._orb_bonus(instrument, direction, s)
        orb_age_min  = getattr(self, "_last_orb_age_min", None)   # set by _orb_bonus
        if orb_bonus:
            reasons.append("✅ BON ORB break (+1)")
            layer["BON"] = "✅ ORB"
            score = min(score + 1, 6)
        else:
            # ── BONUS: CPR pivot bias (+1) ────────────────────────────────
            cpr_bonus, pivot = self._cpr_bonus(instrument, direction, m5_c[-1])
            if cpr_bonus:
                reasons.append(f"✅ BON CPR pivot={pivot:.5f} bias (+1)")
                layer["BON"] = "✅ CPR"
                score = min(score + 1, 6)
            else:
                reasons.append("⬜ BON no ORB/CPR bonus")
                layer["BON"] = "⬜ none"

        # ── Clear L2 pending — all layers passed ──────────────────────────
        if state is not None:
            state.pop("l2_pending", None)

        # ── Determine position size ───────────────────────────────────────
        position_usd = self._score_to_usd(score, s)

        # Build levels for Fiber Scalp bot.py compatibility
        pip_size   = float(s.get("pip_size", 0.0001))
        _pair_sl_tp = s.get("pair_sl_tp", {})
        _pair_cfg   = _pair_sl_tp.get(instrument, {})
        sl_pips     = int(_pair_cfg.get("sl_pips",  18))
        tp_pips     = int(_pair_cfg.get("tp_pips",  30))
        pip_val     = float(_pair_cfg.get("pip_value_usd", 10.0))
        pip_unit    = pip_val / 100_000
        sl_usd_rec  = round(sl_pips * pip_unit, 7)
        tp_usd_rec  = round(tp_pips * pip_unit, 7)
        rr_ratio    = round(tp_usd_rec / sl_usd_rec, 2) if sl_usd_rec > 0 else 0

        # H1 relation for score-aware filter in bot.py
        if h1_trend in ("UNKNOWN", "FLAT", "DISABLED"):
            h1_relation = "neutral"
        elif (h1_trend == "BULLISH" and direction == "BUY") or (h1_trend == "BEARISH" and direction == "SELL"):
            h1_relation = "aligned"
        else:
            h1_relation = "opposite"

        levels = {
            **layer,
            "score":           score,
            "position_usd":    position_usd,
            "entry":           round(m5_c[-1], 5),
            "current_price":   round(m5_c[-1], 5),   # FIX NO-CURPRICE: Fiber Scalp bot.py expects this
            "setup":           f"Hybrid L0→L3 {direction}",
            "sl_usd_rec":      sl_usd_rec,
            "tp_usd_rec":      tp_usd_rec,
            "sl_price_dist":   round(sl_pips * pip_size, 7),
            "tp_price_dist":   round(tp_pips * pip_size, 7),
            "sl_pips":         sl_pips,
            "tp_pips":         tp_pips,
            "rr_ratio":        rr_ratio,
            "pip_size":        pip_size,
            "h1_trend":        h1_trend,
            "h1_aligned":      h1_relation == "aligned",
            "h1_relation":     h1_relation,
            "orb_formed":      orb_bonus,             # FIX NO-ORB-META: Telegram signal card
            "orb_age_min":     orb_age_min,           # FIX NO-ORB-META: Telegram signal card
            "signal_blockers": [],
            "mandatory_checks": {"score_ok": score >= 4, "rr_ok": rr_ratio >= 1.6},
            "quality_checks":   {"tp_ok": True},
        }

        log.info("%s: ✅ ALL LAYERS PASSED score=%d/6 dir=%s pos=$%d",
                 instrument, score, direction, position_usd)
        return score, direction, " | ".join(reasons), levels, position_usd

    # ── ORB bonus helper ──────────────────────────────────────────────────────

    def _orb_bonus(self, instrument: str, direction: str, settings: dict) -> bool:
        """Returns True if price is beyond the session ORB in the trade direction."""
        try:
            now_sgt = datetime.now(_SGT)
            # London ORB: first 15min after session open (16:00 SGT)
            lon_open = int(settings.get("london_session_start_hour", 16))
            lon_end  = int(settings.get("london_session_end_hour", 20))  # FIX ORB-WINDOW
            us_open  = int(settings.get("us_session_start_hour", 21))
            hour     = now_sgt.hour

            if lon_open <= hour <= lon_end:   # FIX ORB-WINDOW: was `< lon_open+4`, missed hour 20
                session_start = now_sgt.replace(hour=lon_open, minute=0, second=0, microsecond=0)
            elif us_open <= hour < us_open + 3:
                session_start = now_sgt.replace(hour=us_open, minute=0, second=0, microsecond=0)
            elif hour < 4:
                # US continuation window — session opened YESTERDAY at us_open hour
                from datetime import timedelta
                session_start = (now_sgt - timedelta(days=1)).replace(
                    hour=us_open, minute=0, second=0, microsecond=0)
            else:
                return False

            age_min = (now_sgt - session_start).total_seconds() / 60
            self._last_orb_age_min = int(age_min)   # FIX NO-ORB-META: expose for levels dict
            fresh   = int(settings.get("orb_fresh_minutes", ORB_FRESH_MINUTES))
            aging   = int(settings.get("orb_aging_minutes", ORB_AGING_MINUTES))
            if age_min > aging:
                return False

            # Fetch M15 candles and find the ones from session start onward
            m15_c, m15_h, m15_l, m15_t = self._candles_with_time(instrument, "M15", 16)
            if not m15_t:
                return False
            # Convert session_start to UTC for comparison
            import pytz as _pytz
            session_start_utc = session_start.astimezone(_pytz.utc)
            # Find candles at or after session open
            session_highs, session_lows = [], []
            for i, t in enumerate(m15_t):
                try:
                    ct = datetime.fromisoformat(t.replace("Z", "+00:00"))
                    if ct >= session_start_utc:
                        session_highs.append(m15_h[i])
                        session_lows.append(m15_l[i])
                except Exception:
                    continue
            if len(session_highs) < 1:
                return False
            # Use first 2 session candles as the ORB range (or all if fewer)
            orb_slice = min(2, len(session_highs))
            orb_high = max(session_highs[:orb_slice])
            orb_low  = min(session_lows[:orb_slice])
            price    = m15_c[-1]

            return (direction == "BUY"  and price > orb_high) or \
                   (direction == "SELL" and price < orb_low)
        except Exception as e:
            log.debug("ORB bonus error: %s", e)
            return False

    # ── CPR bonus helper ──────────────────────────────────────────────────────

    def _cpr_bonus(self, instrument: str, direction: str, current_price: float):
        """Returns (True, pivot) if price is on the correct side of CPR pivot."""
        try:
            closes, highs, lows = self._candles_3(instrument, "D", 3)
            if len(closes) < 2:
                return False, 0
            ph, pl, pc = highs[-2], lows[-2], closes[-2]
            pivot = (ph + pl + pc) / 3
            bull_ok = direction == "BUY"  and current_price > pivot
            bear_ok = direction == "SELL" and current_price < pivot
            return (bull_ok or bear_ok), round(pivot, 5)
        except Exception as e:
            log.debug("CPR bonus error: %s", e)
            return False, 0

    # ── Score to position USD ─────────────────────────────────────────────────

    def _score_to_usd(self, score: int, settings: dict) -> int:
        sr = settings.get("score_risk_usd", {})
        for k in (str(score), score):
            if k in sr:
                try:
                    return max(int(sr[k]), 0)
                except (TypeError, ValueError):
                    break
        if score >= 6: return int(s.get("score_6_risk_usd", 50))   # FIX SCORE6-FALLBACK
        if score >= 5: return int(s.get("position_full_usd", 40))
        if score >= 4: return int(s.get("position_partial_usd", 30))
        return 0

    # ── OANDA candle fetcher ──────────────────────────────────────────────────

    def _candles(self, instrument: str, granularity: str, count: int = 60):
        """Returns (closes, highs, lows, opens)"""
        url    = f"{self.base_url}/v3/instruments/{instrument}/candles"
        params = {"count": str(count), "granularity": granularity, "price": "M"}
        for attempt in range(3):
            try:
                r = self.session.get(url, headers=self.headers, params=params, timeout=15)
                if r.status_code == 200:
                    complete = [c for c in r.json().get("candles", []) if c.get("complete")]
                    return (
                        [float(c["mid"]["c"]) for c in complete],
                        [float(c["mid"]["h"]) for c in complete],
                        [float(c["mid"]["l"]) for c in complete],
                        [float(c["mid"]["o"]) for c in complete],
                    )
                log.warning("Candle %s %s HTTP %s attempt %d", instrument, granularity, r.status_code, attempt+1)
            except Exception as e:
                log.warning("Candle fetch error %s %s: %s", instrument, granularity, e)
            time.sleep(1)
        return [], [], [], []

    def _candles_3(self, instrument: str, granularity: str, count: int):
        """Returns (closes, highs, lows) — 3-tuple for CPR helper."""
        c, h, l, _ = self._candles(instrument, granularity, count)
        return c, h, l

    def _candles_with_time(self, instrument: str, granularity: str, count: int = 16):
        """Returns (closes, highs, lows, times) — times are ISO strings for ORB alignment."""
        url    = f"{self.base_url}/v3/instruments/{instrument}/candles"
        params = {"count": str(count), "granularity": granularity, "price": "M"}
        for attempt in range(3):
            try:
                r = self.session.get(url, headers=self.headers, params=params, timeout=15)
                if r.status_code == 200:
                    complete = [c for c in r.json().get("candles", []) if c.get("complete")]
                    return (
                        [float(c["mid"]["c"]) for c in complete],
                        [float(c["mid"]["h"]) for c in complete],
                        [float(c["mid"]["l"]) for c in complete],
                        [c["time"] for c in complete],
                    )
                log.warning("Candle+time %s %s HTTP %s attempt %d",
                            instrument, granularity, r.status_code, attempt+1)
            except Exception as e:
                log.warning("Candle+time fetch error %s %s: %s", instrument, granularity, e)
            time.sleep(1)
        return [], [], [], []

    # ── Technical indicators ──────────────────────────────────────────────────

    def _ema(self, data: list, period: int) -> list:
        if not data or len(data) < period:
            v = sum(data) / len(data) if data else 0.0
            return [v] * max(len(data), 1)
        seed = sum(data[:period]) / period
        emas = [seed] * period
        mult = 2 / (period + 1)
        for p in data[period:]:
            emas.append((p - emas[-1]) * mult + emas[-1])
        return emas

    def _rsi(self, closes: list, period: int = 7) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        if al == 0:
            return 100.0
        return 100 - (100 / (1 + ag / al))

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> float:
        # FIX ATR-GUARD: check all three arrays, not just highs
        if (len(highs) < period + 1 or len(lows) != len(highs) or
                len(closes) != len(highs)):
            return 0.0
        trs = [
            max(highs[i] - lows[i],
                abs(highs[i]  - closes[i-1]),
                abs(lows[i]   - closes[i-1]))
            for i in range(1, len(highs))
        ]
        return sum(trs[-period:]) / period


def score_to_position_usd(score: int, settings: dict | None = None) -> int:
    """Compatibility shim for bot.py imports."""
    s  = settings or {}
    sr = s.get("score_risk_usd", {})
    for k in (str(score), score):
        if k in sr:
            try:
                return max(int(sr[k]), 0)
            except (TypeError, ValueError):
                break
    if score >= 6: return int(s.get("position_full_usd", 50))
    if score >= 5: return int(s.get("position_full_usd", 40))
    if score >= 4: return int(s.get("position_partial_usd", 30))
    return 0

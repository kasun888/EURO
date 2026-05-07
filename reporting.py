# Hybrid Scalp v1.0

**EUR/USD London + NY Scalper — Best of Fiber Scalp v2.1 + EURO-USD bot**

---

## Quick Start

### 1. Set credentials

Create `secrets.json` in the project root (never commit this):

```json
{
  "OANDA_API_KEY":    "your-oanda-api-key",
  "OANDA_ACCOUNT_ID": "your-account-id",
  "TELEGRAM_TOKEN":   "your-bot-token",
  "TELEGRAM_CHAT_ID": "your-chat-id"
}
```

Or set as environment variables (Railway / Docker):
```
OANDA_API_KEY, OANDA_ACCOUNT_ID, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
```

### 2. Test Telegram

```bash
pip install -r requirements.txt
python test_telegram.py
```

### 3. Run locally

```bash
python scheduler.py
```

### 4. Deploy to Railway

Push to GitHub → connect repo in Railway → set env vars → deploy.
The `railway.json` and `Procfile` handle the rest.

---

## Strategy

### Scoring (0–6)

| Layer | What | Points |
|---|---|---|
| L0 | H4 EMA50 macro trend clear | +1 |
| ATR | H1 ATR ≥ 2.5 pip (market moving) | +1 |
| L1 | H1 dual EMA stacked (price > EMA21 > EMA50) | +1 |
| L2 | M15 impulse candle breaks 5-bar structure | +1 |
| L3 | M5 pullback to EMA13 + RSI < 65 | +1 |
| BON | ORB break OR CPR pivot bias | +1 |

**Minimum to trade: 4/6**

### Sessions (SGT = UTC+8)

| Session | Hours | Spread limit |
|---|---|---|
| Dead zone | 04:00–15:59 | No trading |
| London | 16:00–20:59 | 1.5 pip |
| US | 21:00–03:59 | 2.0 pip |

### Risk

| Score | Risk | ~Units |
|---:|---:|---:|
| 4/6 | $30 | ~16,667 |
| 5/6 | $40 | ~22,222 (capped 20k) |
| 6/6 | $50 | ~27,778 (capped 20k) |

SL: 18 pips · TP: 30 pips · RR: 1.67x · Break-even WR: 37.5%

---

## Filters (in order)

1. Weekend / dead zone guard
2. Friday cutoff (22:00 SGT)
3. Daily loss cap (3 losses or -$120)
4. **Win-stop** — 1 win per day then stop
5. **Circuit breaker** — 2 SL → smart H4 flip check → 2 day pause
6. News filter (30 min before/after high-impact)
7. Chaos filter (daily range > 200 pip)
8. ATR gate (H1 ATR < 2.5 pip)
9. Spread guard
10. H1 score-aware filter
11. H1 EMA200 veto
12. Margin guard

---

## Smart Circuit Breaker

After **2 consecutive SL hits**:
- Checks if H4 trend has **flipped direction**
- If yes → resumes immediately in new direction
- If no → pauses 2 days (choppy market)

---

## Files

| File | Origin | Purpose |
|---|---|---|
| `signals.py` | **Hybrid** | H4→H1→M15→M5 signal engine |
| `circuit.py` | **Hybrid** | Win-stop + circuit breaker |
| `settings.json` | **Hybrid** | All configuration |
| `bot.py` | Fiber Scalp + patch | Main orchestrator |
| `scheduler.py` | Fiber Scalp | 5-min cron + health server |
| `oanda_trader.py` | Fiber Scalp | OANDA API layer |
| `telegram_alert.py` | Fiber Scalp | Telegram sender |
| `telegram_templates.py` | Fiber Scalp | Message templates |
| `reporting.py` | Fiber Scalp | Daily/weekly/monthly reports |
| `database.py` | Fiber Scalp | SQLite persistence |
| `news_filter.py` | Fiber Scalp | Economic calendar filter |
| `calendar_fetcher.py` | Fiber Scalp | Forex Factory fetcher |
| `reconcile_state.py` | Fiber Scalp | Broker state reconciliation |
| `config_loader.py` | Fiber Scalp | Settings + secrets loader |
| `state_utils.py` | Fiber Scalp | JSON state helpers |
| `logging_utils.py` | Fiber Scalp | Structured logging |
| `startup_checks.py` | Fiber Scalp | Config validation |
| `analyze_trades.py` | Fiber Scalp | CLI performance dashboard |

---

## Performance Analysis

```bash
python analyze_trades.py              # all time
python analyze_trades.py --last 30    # last 30 days
python analyze_trades.py --all        # include failed orders
```

---

## Key Settings (settings.json)

```json
{
  "demo_mode": true,          // ← change to false for live
  "signal_threshold": 4,      // minimum score to trade
  "win_stop_per_day": true,   // stop after first win
  "circuit_breaker_enabled": true,
  "circuit_breaker_consec_sl": 2,
  "circuit_breaker_pause_days": 2,
  "pair_sl_tp": {
    "EUR_USD": {
      "sl_pips": 18,
      "tp_pips": 30
    }
  }
}
```

---

## Expected Trade Frequency (May 2026 conditions)

| Day type | Trades |
|---|---|
| Strong trend day | 1–2 |
| Normal day | 1 |
| Choppy/ranging | 0–1 |
| News-heavy | 0–1 |
| Chaos (>200 pip range) | 0 |
| **Weekly total** | **3–6** |
| **Monthly total** | **12–22** |

The win-stop is the dominant frequency limiter — once the day's first trade wins, the bot protects that profit and stops.

---

## Estimated P&L (demo, 40% win rate assumption)

| Period | Trades | Expected net |
|---|---|---|
| Week | ~4 | $0 to +$80 |
| Month | ~16 | $0 to +$320 |

At 40% WR with 1.67x RR: break-even WR is 37.5%, so 40% gives slight positive expectancy.
Minimum recommended demo period: **4 weeks / 50+ closed trades** before going live.

"""reporting.py — Fiber Scalp v1.5 Telegram Performance Reports

Scheduled reports read from /data/trade_history.json; weekly export converts it to trade_history.csv before sending
on the Railway persistent volume. No archive file needed — the 90-day rolling
window covers all report periods.

Schedule (Asia/Singapore timezone, managed by scheduler.py):
  Monthly  — First Monday of each month at 08:00 SGT
  Weekly   — Every Monday at 08:00 SGT  (covers Mon–Fri prior week)
  Export   — Every Monday at 08:05 SGT  (sends trade_history.csv)
  Daily    — Mon–Fri at 07:50 SGT

Usage (called by scheduler.py):
    from reporting import send_daily_report, send_weekly_report, send_monthly_report
"""
from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytz

from state_utils import TRADE_HISTORY_FILE
from telegram_alert import TelegramAlert
from telegram_templates import msg_daily_report, msg_weekly_report, msg_monthly_report

log = logging.getLogger(__name__)
SGT = pytz.timezone("Asia/Singapore")


# ── Data loading ───────────────────────────────────────────────────────────────

def _load_history() -> list:
    """Load trade_history.json from /data. Returns [] on any error."""
    if not TRADE_HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(TRADE_HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.warning("reporting: could not read trade_history.json: %s", exc)
        return []


def _parse_ts(ts: str | None) -> datetime | None:
    """Parse a SGT timestamp string to an aware datetime, or None."""
    if not ts:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return SGT.localize(datetime.strptime(ts, fmt))
        except Exception:
            pass
    return None


def _csv_cell(value):
    """Return a CSV-safe scalar; nested dict/list values are stored as compact JSON."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value


def _write_history_csv(history: list, csv_path: Path) -> None:
    """Convert trade_history.json records to a flat CSV file for Telegram export."""
    preferred = [
        "timestamp_sgt", "mode", "instrument", "direction", "setup", "session",
        "window", "macro_session", "score", "raw_score", "news_penalty",
        "position_usd", "entry", "sl_price", "tp_price", "size",
        "cpr_width_pct", "h1_trend", "h1_aligned", "h1_relation",
        "max_pips_reached", "sl_usd", "tp_usd", "pip_size",
        "estimated_risk_usd", "estimated_reward_usd", "spread_pips",
        "stop_pips", "tp_pips", "trade_id", "status", "realized_pnl_usd",
        "breakeven_moved", "details", "levels",
    ]

    rows = [r if isinstance(r, dict) else {"value": r} for r in history]
    seen = {key for row in rows for key in row.keys()}
    fieldnames = [key for key in preferred if key in seen]
    fieldnames += sorted(key for key in seen if key not in fieldnames)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames or ["no_records"])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_cell(row.get(key, "")) for key in writer.fieldnames})


def _filled(history: list) -> list:
    """Return only FILLED trades with a realized PnL."""
    return [
        t for t in history
        if t.get("status") == "FILLED" and isinstance(t.get("realized_pnl_usd"), (int, float))
    ]


def _trades_in_window(filled: list, start: datetime, end: datetime) -> list:
    """Filter filled trades whose timestamp_sgt falls within [start, end)."""
    result = []
    for t in filled:
        dt = _parse_ts(t.get("timestamp_sgt"))
        if dt and start <= dt < end:
            result.append(t)
    return result


# ── Stats builders ─────────────────────────────────────────────────────────────

def _stats(trades: list) -> dict:
    """Compute standard stats dict from a list of filled trades."""
    if not trades:
        return {
            "count": 0, "wins": 0, "losses": 0,
            "net_pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0,
            "win_rate": 0.0, "profit_factor": None,
            "avg_r": None, "max_win_streak": 0, "max_loss_streak": 0,
            "best_trade": None, "worst_trade": None,
            "instant_sl_count": 0,
        }

    wins   = [t for t in trades if t["realized_pnl_usd"] > 0]
    losses = [t for t in trades if t["realized_pnl_usd"] < 0]

    gross_profit = sum(t["realized_pnl_usd"] for t in wins)
    gross_loss   = abs(sum(t["realized_pnl_usd"] for t in losses))
    net_pnl      = gross_profit - gross_loss
    win_rate     = round(len(wins) / len(trades) * 100, 1) if trades else 0.0
    pf           = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

    # R-multiple (uses estimated_risk_usd added by C-01 fix)
    r_vals = []
    for t in trades:
        risk = t.get("estimated_risk_usd")
        if risk and risk > 0:
            r_vals.append(round(t["realized_pnl_usd"] / risk, 2))
    avg_r = round(sum(r_vals) / len(r_vals), 2) if r_vals else None

    # Streaks
    outcomes = ["W" if t["realized_pnl_usd"] > 0 else "L" for t in trades]
    max_win_s = max_loss_s = cur = 0
    prev = None
    for o in outcomes:
        if o == prev:
            cur += 1
        else:
            cur = 1
            prev = o
        if o == "W":
            max_win_s = max(max_win_s, cur)
        else:
            max_loss_s = max(max_loss_s, cur)

    # Best and worst individual trade
    def _trade_summary(t):
        raw_time = t.get("closed_at_sgt") or t.get("timestamp_sgt") or ""
        hhmm = raw_time[11:16] if len(raw_time) >= 16 else raw_time
        return {"pnl": round(t["realized_pnl_usd"], 2), "time": hhmm}

    best_trade  = _trade_summary(max(trades, key=lambda t: t["realized_pnl_usd"]))
    worst_trade = _trade_summary(min(trades, key=lambda t: t["realized_pnl_usd"]))

    # Instant SL: a losing trade that closed within one candle (≤ cycle_minutes, ~5 min)
    def _trade_duration_min(t) -> int | None:
        open_ts  = t.get("timestamp_sgt", "")
        close_ts = t.get("closed_at_sgt", "")
        if not open_ts or not close_ts:
            return None
        try:
            from datetime import datetime
            fmt = "%Y-%m-%d %H:%M:%S"
            return int((datetime.strptime(close_ts[:19], fmt) -
                        datetime.strptime(open_ts[:19], fmt)).total_seconds() / 60)
        except Exception:
            return None

    instant_sl_count = sum(
        1 for t in losses
        if (_trade_duration_min(t) or 999) <= 5
    )

    return {
        "count":          len(trades),
        "wins":           len(wins),
        "losses":         len(losses),
        "net_pnl":        round(net_pnl, 2),
        "gross_profit":   round(gross_profit, 2),
        "gross_loss":     round(gross_loss, 2),
        "win_rate":       win_rate,
        "profit_factor":  pf,
        "avg_r":          avg_r,
        "max_win_streak": max_win_s,
        "max_loss_streak":max_loss_s,
        "best_trade":     best_trade,
        "worst_trade":    worst_trade,
        "instant_sl_count": instant_sl_count,
    }


def _session_breakdown(trades: list) -> dict[str, dict]:
    """Win rate + PnL per macro session."""
    buckets: dict[str, list] = defaultdict(list)
    for t in trades:
        sess = t.get("macro_session") or t.get("session") or "Unknown"
        buckets[sess].append(t)
    result = {}
    for sess, ts in sorted(buckets.items()):
        wins = [t for t in ts if t["realized_pnl_usd"] > 0]
        result[sess] = {
            "count":    len(ts),
            "wins":     len(wins),
            "losses":   len(ts) - len(wins),
            "win_rate": round(len(wins) / len(ts) * 100, 1),
            "net_pnl":  round(sum(t["realized_pnl_usd"] for t in ts), 2),
        }
    return result


def _setup_breakdown(trades: list) -> dict[str, dict]:
    """Win rate + PnL per setup type."""
    buckets: dict[str, list] = defaultdict(list)
    for t in trades:
        setup = t.get("setup") or "Unknown"
        buckets[setup].append(t)
    result = {}
    for setup, ts in sorted(buckets.items()):
        wins = [t for t in ts if t["realized_pnl_usd"] > 0]
        result[setup] = {
            "count":    len(ts),
            "wins":     len(wins),
            "losses":   len(ts) - len(wins),
            "win_rate": round(len(wins) / len(ts) * 100, 1),
            "net_pnl":  round(sum(t["realized_pnl_usd"] for t in ts), 2),
        }
    return result


def _score_breakdown(trades: list) -> dict[int, dict]:
    """Win rate per signal score."""
    buckets: dict[int, list] = defaultdict(list)
    for t in trades:
        score = t.get("score")
        if score is not None:
            buckets[int(score)].append(t)
    result = {}
    for score in sorted(buckets.keys()):
        ts   = buckets[score]
        wins = [t for t in ts if t["realized_pnl_usd"] > 0]
        result[score] = {
            "count":    len(ts),
            "win_rate": round(len(wins) / len(ts) * 100, 1),
        }
    return result


# ── Window helpers ─────────────────────────────────────────────────────────────


def _h1_breakdown(trades: list) -> dict | None:
    """Return H1 filter split: aligned vs counter-trend stats.
    Returns None if no trades have h1_aligned field recorded.
    """
    aligned_trades  = [t for t in trades if t.get("h1_aligned") is True]
    counter_trades  = [t for t in trades if t.get("h1_aligned") is False]

    if not aligned_trades and not counter_trades:
        return None  # h1 data not recorded (old trades)

    def _grp(grp):
        wins   = sum(1 for t in grp if (t.get("realized_pnl_usd") or 0) > 0)
        losses = sum(1 for t in grp if (t.get("realized_pnl_usd") or 0) < 0)
        net    = round(sum(t.get("realized_pnl_usd") or 0 for t in grp), 2)
        wr     = round(wins / len(grp) * 100, 1) if grp else 0.0
        return {"count": len(grp), "wins": wins, "losses": losses,
                "net_pnl": net, "win_rate": wr}

    return {
        "aligned": _grp(aligned_trades),
        "counter": _grp(counter_trades),
    }


def _prior_trading_day(now: datetime) -> tuple[datetime, datetime]:
    """Return (start, end) for the prior trading day in SGT.
    On Monday, looks back to Friday. Skips Saturday/Sunday.
    """
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day -= timedelta(days=1)
    # Step back over weekend
    while day.weekday() in (5, 6):
        day -= timedelta(days=1)
    return day, day + timedelta(days=1)


def _current_week_window(now: datetime) -> tuple[datetime, datetime]:
    """Return (Mon 00:00, now) for the current week."""
    days_since_mon = now.weekday()
    week_start = (now - timedelta(days=days_since_mon)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return week_start, now


def _prior_week_window(now: datetime) -> tuple[datetime, datetime, str]:
    """Return (Mon 00:00, Fri 23:59:59, label) for the prior Mon–Fri week."""
    days_since_mon = now.weekday()
    this_mon = (now - timedelta(days=days_since_mon)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    prior_mon = this_mon - timedelta(days=7)
    prior_fri = this_mon - timedelta(seconds=1)
    label = f"{prior_mon.strftime('%d %b')} – {prior_fri.strftime('%d %b %Y')}"
    return prior_mon, this_mon, label


def _current_month_window(now: datetime) -> tuple[datetime, datetime]:
    """Return (1st of current month 00:00, now)."""
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start, now


def _prior_month_window(now: datetime) -> tuple[datetime, datetime, str]:
    """Return (1st of prior month, 1st of current month, label)."""
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_prior = first_this - timedelta(seconds=1)
    first_prior = last_prior.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    label = first_prior.strftime("%B %Y")
    return first_prior, first_this, label


def _is_first_monday_of_month(now: datetime) -> bool:
    """True if today (SGT) is the first Monday of the calendar month."""
    return now.weekday() == 0 and now.day <= 7


# ── Report senders ─────────────────────────────────────────────────────────────

def send_daily_report() -> None:
    """Send daily performance summary at 07:50 SGT.

    Fires after US continuation closes (03:59 SGT), capturing the full
    London + US trading day. Covers:
      - Current trading day  (16:00 yesterday → 03:59 today)
      - Session breakdown    (Tokyo / London / US merged)
      - Month-to-date        (1st → now)
      - Blocked cycles breakdown
    """
    try:
        from database import Database  # local import avoids circular at module level
        now     = datetime.now(SGT)
        history = _load_history()
        filled  = _filled(history)

        # Prior day
        pd_start, pd_end   = _prior_trading_day(now)
        pd_trades          = _trades_in_window(filled, pd_start, pd_end)
        pd_stats           = _stats(pd_trades)
        pd_label           = pd_start.strftime("%A %d %b")

        # Week-to-date
        wtd_start, wtd_end = _current_week_window(now)
        wtd_trades         = _trades_in_window(filled, wtd_start, wtd_end)
        wtd_stats          = _stats(wtd_trades)

        # Month-to-date
        mtd_start, mtd_end = _current_month_window(now)
        mtd_trades         = _trades_in_window(filled, mtd_start, mtd_end)
        mtd_stats          = _stats(mtd_trades)

        # Open positions count (trades with no realized_pnl yet)
        open_count = sum(
            1 for t in _load_history()
            if t.get("status") == "FILLED" and t.get("realized_pnl_usd") is None
        )

        # Blocked cycles from DB — use UTC date prefix matching prior trading day
        blocked_spread = blocked_news = blocked_signal = 0
        try:
            db             = Database()
            utc_prefix     = pd_start.astimezone(pytz.utc).strftime("%Y-%m-%d")
            blocked_counts = db.query_blocked_cycles(utc_prefix)
            blocked_spread  = blocked_counts.get("spread_guard", 0)
            blocked_news    = blocked_counts.get("news_filter", 0)
            blocked_signal  = blocked_counts.get("signal_blocked", 0)
        except Exception as exc:
            log.warning("Could not query blocked cycles: %s", exc)

        # Previous day loss-cap flag
        try:
            from state_utils import load_json, OPS_STATE_FILE
            ops = load_json(OPS_STATE_FILE, {})
            yesterday_str = pd_start.strftime("%Y-%m-%d")
            pd_stats["ended_on_loss_cap"] = (
                ops.get("loss_cap_state") == f"loss_cap:{yesterday_str}"
            )
        except Exception:
            pass

        # Session breakdown — group by macro_session field (London / US / Tokyo)
        # US continuation (00:00-03:59) uses macro="US" so merges automatically
        session_order = [
            ("🗼 Tokyo",   "Tokyo"),
            ("🇬🇧 London", "London"),
            ("🗽 US",      "US"),
        ]
        session_stats = {}
        for label, macro_key in session_order:
            sess_trades = [t for t in pd_trades
                           if (t.get("macro_session") or t.get("window") or "") == macro_key]
            if sess_trades:
                session_stats[label] = _stats(sess_trades)

        # Day total — use pd_trades (same window as session breakdown)
        # pd_trades = full prior trading day (00:00 → 24:00 SGT)
        # Fixes: day total was using 16:00 SGT start, missing Tokyo trades
        today_stats = _stats(pd_trades)
        today_label = pd_start.strftime("%a %d %b %Y")

        msg = msg_daily_report(
            day_label       = today_label,
            day_stats       = today_stats,
            wtd_stats       = wtd_stats,
            mtd_stats       = mtd_stats,
            open_count      = open_count,
            report_time     = now.strftime("%H:%M SGT"),
            blocked_spread  = blocked_spread,
            blocked_news    = blocked_news,
            blocked_signal  = blocked_signal,
            session_stats   = session_stats,
        )
        ok = TelegramAlert().send(msg)
        if ok:
            log.info("Daily report sent.")
        else:
            log.warning("Daily report send failed.")
    except Exception as exc:
        log.exception("send_daily_report error: %s", exc)


def send_weekly_report() -> None:
    """Send weekly performance report every Monday at 08:00 SGT.

    Covers the prior Mon–Fri trading week with full breakdown.
    """
    try:
        now     = datetime.now(SGT)
        history = _load_history()
        filled  = _filled(history)

        pw_start, pw_end, pw_label = _prior_week_window(now)
        pw_trades                  = _trades_in_window(filled, pw_start, pw_end)
        pw_stats                   = _stats(pw_trades)
        sessions                   = _session_breakdown(pw_trades)
        setups                     = _setup_breakdown(pw_trades)

        # By Pair breakdown
        pw_pairs: dict = {}
        for t in pw_trades:
            instr = (t.get("instrument") or "").replace("_", "/")
            if instr not in pw_pairs:
                pw_pairs[instr] = []
            pw_pairs[instr].append(t)
        pair_stats = {k: _stats(v) for k, v in pw_pairs.items()}

        h1_stats = _h1_breakdown(pw_trades)

        msg = msg_weekly_report(
            week_label = pw_label,
            stats      = pw_stats,
            sessions   = sessions,
            setups     = setups,
            pairs      = pair_stats,
            h1_stats   = h1_stats,
            report_time= now.strftime("%H:%M SGT"),
        )
        ok = TelegramAlert().send(msg)
        if ok:
            log.info("Weekly report sent.")
        else:
            log.warning("Weekly report send failed.")
    except Exception as exc:
        log.exception("send_weekly_report error: %s", exc)



def send_weekly_export() -> None:
    """Convert trade_history.json to trade_history.csv and send it every Monday 08:05 SGT.

    Fires 5 minutes after the weekly performance report (08:00 SGT) so the
    text report arrives first, then the CSV export follows.

    The exported CSV contains all trade records including H1 trend fields:
    h1_trend, h1_aligned — used for post-trade analysis of the H1 filter.
    """
    try:
        from pathlib import Path
        import os

        data_dir     = Path(os.getenv("DATA_DIR", "/data"))
        history_file = data_dir / "trade_history.json"
        export_file  = data_dir / "trade_history.csv"
        alert        = TelegramAlert()

        if not history_file.exists():
            log.warning("send_weekly_export: trade_history.json not found.")
            alert.send("📎 Weekly export: no trade history found on volume.")
            return

        now     = datetime.now(SGT)
        history = _load_history()
        _write_history_csv(history, export_file)
        filled  = _filled(history)

        # Count H1 filter stats this week for the caption
        pw_start, pw_end, pw_label = _prior_week_window(now)
        pw_trades = _trades_in_window(filled, pw_start, pw_end)
        h1_counter  = sum(1 for t in pw_trades if not t.get("h1_aligned", True))
        h1_aligned  = sum(1 for t in pw_trades if t.get("h1_aligned", True))
        all_trades  = len(history)

        caption = (
            f"trade_history.csv — {pw_label}\n"
            f"{all_trades} total records  |  {len(filled)} filled trades\n"
            f"This week: {len(pw_trades)} trades  "
            f"({h1_aligned} H1-aligned  /  {h1_counter} counter-trend)"
        )

        ok = alert.send_document(export_file, caption=caption)
        if ok:
            log.info("Weekly CSV export sent: %d total records, %d this week.",
                     all_trades, len(pw_trades))
        else:
            log.warning("Weekly export: send_document failed.")
    except Exception as exc:
        log.exception("send_weekly_export error: %s", exc)


def send_monthly_report() -> None:
    """Send monthly performance report on the first Monday of each month at 08:00 SGT.

    Covers the prior full calendar month with session, setup, and score breakdown.
    Also shows month-over-month PnL delta when prior-prior month data exists.
    The first-Monday guard is enforced here so the scheduler can use a simple
    weekly cron without needing a complex calendar trigger.
    """
    try:
        now = datetime.now(SGT)

        if not _is_first_monday_of_month(now):
            log.info("Monthly report skipped — not first Monday of month (%s)", now.strftime("%d %b"))
            return

        filled = _filled(_load_history())

        pm_start, pm_end, pm_label = _prior_month_window(now)
        pm_trades                  = _trades_in_window(filled, pm_start, pm_end)
        pm_stats                   = _stats(pm_trades)
        sessions                   = _session_breakdown(pm_trades)
        setups                     = _setup_breakdown(pm_trades)
        scores                     = _score_breakdown(pm_trades)

        # Month-over-month delta: compare prior month PnL vs the month before that
        ppm_start = (pm_start.replace(day=1) - timedelta(days=1)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        ppm_trades = _trades_in_window(filled, ppm_start, pm_start)
        ppm_pnl    = round(sum(t["realized_pnl_usd"] for t in ppm_trades), 2) if ppm_trades else None
        mom_delta  = round(pm_stats["net_pnl"] - ppm_pnl, 2) if ppm_pnl is not None else None

        h1_stats = _h1_breakdown(pm_trades)

        msg = msg_monthly_report(
            month_label = pm_label,
            stats       = pm_stats,
            sessions    = sessions,
            setups      = setups,
            scores      = scores,
            h1_stats    = h1_stats,
            mom_delta   = mom_delta,
            prior_month_pnl = ppm_pnl,
            report_time = now.strftime("%H:%M SGT"),
        )
        ok = TelegramAlert().send(msg)
        if ok:
            log.info("Monthly report sent for %s.", pm_label)
        else:
            log.warning("Monthly report send failed.")
    except Exception as exc:
        log.exception("send_monthly_report error: %s", exc)

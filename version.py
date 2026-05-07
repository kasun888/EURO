"""Telegram message templates for Fiber Scalp v2.1
AtomicFX-style: clean, state-change only, minimal noise.
"""
from __future__ import annotations

_DIV = "─" * 22


def _dir_icon(d: str) -> str:
    return "📈" if d == "BUY" else ("📉" if d == "SELL" else "")

def _session_icon(s: str) -> str:
    u = s.upper()
    if "LONDON" in u: return "🇬🇧"
    if "US" in u:     return "🗽"
    if "TOKYO" in u:  return "🗼"
    if "ASIAN" in u or "PRE" in u: return "🌐"
    if "EUROPEAN" in u: return "☀️"
    if "DEAD" in u:   return "✈️"
    return "📊"

def _pos_label(p: int) -> str:
    if p >= 30: return f"Full ${p}"
    if p >= 20: return f"Medium ${p}"
    if p >  0:  return f"Partial ${p}"
    return "No trade"

def _pnl_icon(v: float) -> str:
    return "🟢" if v > 0 else ("🔴" if v < 0 else "⬜")

def _mini_stats(s: dict) -> str:
    if s["count"] == 0: return "No closed trades"
    return f"{s['count']} trades  {s['wins']}W/{s['losses']}L  ${s['net_pnl']:+.2f}  WR {s['win_rate']:.0f}%"

def _split_banner(banner: str) -> tuple[str, str]:
    """Extract pair from banner.
    Handles both:
      '🇬🇧 LONDON [EUR/USD]'  → ('🇬🇧 LONDON [EUR/USD]', 'EUR/USD')
      'Fiber Scalp v2.1 | EUR/USD' → ('Fiber Scalp v2.1', 'EUR/USD')
    """
    if "[" in banner and "]" in banner:
        pair = banner[banner.index("[")+1 : banner.index("]")]
        return banner.strip(), pair.strip()
    if " | " in banner:
        bot, pair = banner.rsplit(" | ", 1)
        return bot.strip(), pair.strip("[]").strip()
    return banner.strip(), ""

def _ps(dp: int) -> float:
    return 10 ** -(dp - 1)

def _ascii_bar(v: float, mx: float, w: int = 10) -> str:
    if mx <= 0: return "░" * w
    f = int(round(v / mx * w))
    return "█" * f + "░" * (w - f)


# ── 1. Signal update ──────────────────────────────────────────────────────────

def msg_signal_update(
    banner, session, direction, score, position_usd, cpr_width_pct,
    detail_lines, news_penalty=0, raw_score=None, decision="WATCHING",
    reason="", mandatory_checks=None, quality_checks=None,
    execution_checks=None, cycle_minutes=5, signal_threshold=4,
    setup="", orb_age_min=None, orb_formed=False,
    h1_trend="UNKNOWN", h1_aligned=True, h1_relation=None, h1_filter_mode="score_aware",
) -> str:
    bot, pair = _split_banner(banner)
    s_str = f"{score}/6"
    if raw_score is not None and news_penalty:
        s_str += f" (raw {raw_score}, news {news_penalty:+d})"
    di    = _dir_icon(direction)
    nline = f"⚠️  News penalty: {news_penalty:+d}\n" if news_penalty else ""

    # H1 trend line — shows relation used by the v2.0 score-aware filter
    def _h1_line():
        rel = h1_relation or ("aligned" if h1_aligned else "opposite")
        if h1_trend in ("UNKNOWN", "DISABLED") and rel == "neutral":
            return ""
        icon = "🟢" if rel == "aligned" else ("🔴" if rel == "opposite" else "⬜")
        label = "aligned" if rel == "aligned" else ("counter-trend ⚠️" if rel == "opposite" else "neutral")
        mode = " [score-aware]" if h1_filter_mode in ("score_aware", "tiered") else (" [strict]" if h1_filter_mode == "strict" else " [soft]")
        return f"H1: {icon} {h1_trend}  ({label}){mode}\n"

    if decision == "WATCHING":
        orb = ""
        if orb_formed and orb_age_min is not None:
            lbl = "fresh" if orb_age_min < 60 else ("aging" if orb_age_min < 120 else "stale")
            orb = f"ORB: {orb_age_min}min ({lbl})  |  "
        return (
            f"{banner}\n{_DIV}\n"
            f"{pair}  {di} {direction}  Score {s_str}  👁 Watching\n"
            f"Reason: {reason or 'Watching for setup'}\n"
            f"{_h1_line()}"
            f"{nline}"
            f"{_DIV}\n"
            f"{orb}CPR: {cpr_width_pct:.2f}% width\n"
            f"Next cycle in {cycle_minutes} min"
        )

    if decision == "BLOCKED":
        return (
            f"{banner}\n{_DIV}\n"
            f"{pair}  {di} {direction}  Score {s_str}  ❌ Blocked\n"
            f"Reason: {reason}\n"
            f"{_h1_line()}"
            f"{nline}"
            f"Next cycle in {cycle_minutes} min"
        )

    # READY
    spread = margin = ""
    if execution_checks:
        for lbl, ok, det in execution_checks:
            if "Spread" in lbl: spread = f"Spread: {det}  |  "
            elif "Margin" in lbl: margin = f"Margin: {det}\n"
    return (
        f"{banner}\n{_DIV}\n"
        f"{pair}  {di} {direction}  Score {s_str}  ✅ Ready\n"
        f"Window: {session}  |  CPR: {cpr_width_pct:.2f}% width\n"
        f"{_h1_line()}"
        f"{nline}"
        f"{_DIV}\n"
        f"{spread}{margin}"
        f"Next cycle in {cycle_minutes} min"
    )


# ── 2. Trade opened ───────────────────────────────────────────────────────────

def msg_trade_opened(
    banner, direction, setup, session, fill_price, signal_price,
    sl_price, tp_price, sl_usd, tp_usd, units, position_usd,
    rr_ratio, cpr_width_pct, spread_pips, score, balance, demo,
    news_penalty=0, raw_score=None, free_margin=None,
    required_margin=None, margin_mode="NORMAL", margin_usage_pct=None,
    price_dp=5, tp2_rr=3.0,
    h1_trend="UNKNOWN", h1_aligned=True, h1_relation=None,
) -> str:
    bot, pair = _split_banner(banner)
    mode = "DEMO" if demo else "LIVE"
    di   = _dir_icon(direction)
    si   = _session_icon(session)
    s_str = f"{score}/6"
    if raw_score is not None and news_penalty:
        s_str += f" (raw {raw_score})"

    pip = _ps(price_dp)
    sl_p  = round(sl_usd / pip)
    tp_p  = round(tp_usd / pip)
    tp2_p = round(sl_usd * tp2_rr / pip)
    tp2_price = round(
        fill_price + sl_usd * tp2_rr if direction == "BUY"
        else fill_price - sl_usd * tp2_rr, price_dp
    )
    units_fmt = f"{int(units):,}" if units >= 1000 else str(int(units))

    return (
        f"{banner}\n{_DIV}\n"
        f"{di} {direction} {pair} — {si} {session}\n"
        f"{_DIV}\n"
        f"◆ Entry:  {fill_price:.{price_dp}f}\n\n"
        f"✅ TP1:   {tp_price:.{price_dp}f}  (+{tp_p}p | {rr_ratio:.1f}xRR)  ← bot target\n"
        f"◻  TP2:   {tp2_price:.{price_dp}f}  (+{tp2_p}p | {tp2_rr:.1f}xRR)  ← reference\n"
        f"✗  SL:    {sl_price:.{price_dp}f}  (-{sl_p}p)\n"
        f"{_DIV}\n"
        f"Setup:   {setup}\n"
        f"Score:   {s_str}  |  Spread: {spread_pips}p\n"
        + (f"H1:      {'🟢' if (h1_relation or ('aligned' if h1_aligned else 'opposite')) == 'aligned' else ('🔴' if (h1_relation or ('aligned' if h1_aligned else 'opposite')) == 'opposite' else '⬜')} {h1_trend}  ({'aligned' if (h1_relation or ('aligned' if h1_aligned else 'opposite')) == 'aligned' else ('counter-trend ⚠️' if (h1_relation or ('aligned' if h1_aligned else 'opposite')) == 'opposite' else 'neutral')})\n"
           if h1_trend not in ('UNKNOWN', 'DISABLED') else "")
        + f"Units:   {units_fmt}  |  Risk: {_pos_label(position_usd)}  |  Mode: {mode}"
    )


# ── 3. Breakeven ──────────────────────────────────────────────────────────────

def msg_breakeven(trade_id, direction, entry, trigger_price, trigger_dist,
                  current_price, unrealized_pnl, demo, price_dp=5) -> str:
    mode = "DEMO" if demo else "LIVE"
    return (
        f"🔒 Break-Even Activated\n{_DIV}\n"
        f"{direction}  Trade #{trade_id}\n"
        f"Entry:   {entry:.{price_dp}f}  →  SL moved to entry\n"
        f"Trigger: {trigger_price:.{price_dp}f}  (now: {current_price:.{price_dp}f})\n"
        f"PnL now: ${unrealized_pnl:+.2f}  |  Mode: {mode}"
    )


# ── 4. Trade closed ───────────────────────────────────────────────────────────

def msg_trade_closed(trade_id, direction, setup, entry, close_price,
                     pnl, session, demo, duration_str="", price_dp=5,
                     max_pips_reached=None) -> str:
    mode = "DEMO" if demo else "LIVE"
    di   = _dir_icon(direction)
    pip  = _ps(price_dp)
    pips = abs(close_price - entry) / pip

    if pnl > 0:
        outcome, pip_str = "TP ✅", f"+{pips:.0f} pips"
    elif pnl < 0:
        outcome, pip_str = "SL ✗",  f"-{pips:.0f} pips"
    else:
        outcome, pip_str = "BE ➡️", "0 pips"

    dur      = f"  |  {duration_str}" if duration_str else ""
    max_line = (f"Peak:    +{max_pips_reached:.1f} pips reached\n"
                if max_pips_reached is not None and max_pips_reached > 0 else "")
    return (
        f"{di} {direction} {outcome}\n{_DIV}\n"
        f"Entry:   {entry:.{price_dp}f}  →  Close: {close_price:.{price_dp}f}\n"
        f"Move:    {pip_str}\n"
        f"PnL:     ${pnl:+.2f}{dur}\n"
        f"{max_line}"
        f"Session: {session}  |  Mode: {mode}"
    )


# ── 5. News block ─────────────────────────────────────────────────────────────

def msg_news_block(event_name, event_time_sgt, before_min, after_min) -> str:
    return (
        f"📰 News Block\n{_DIV}\n"
        f"Event:  {event_name}\n"
        f"Time:   {event_time_sgt} SGT\n"
        f"Window: -{before_min}min → +{after_min}min\n"
        f"No new entries — resuming after event"
    )


# ── 6. News penalty ───────────────────────────────────────────────────────────

def msg_news_penalty(event_names, penalty, score_after, score_before,
                     position_after, position_before) -> str:
    names = ", ".join(event_names) if event_names else "Medium event"
    pos   = (f"${position_before} → ${position_after}"
             if position_before != position_after else f"${position_after} (unchanged)")
    status = "Trading with reduced size" if position_after > 0 else "Score below threshold — watching"
    return (
        f"📰 News Penalty\n{_DIV}\n"
        f"Event:    {names}\n"
        f"Score:    {score_before}/6 → {score_after}/6  (penalty {penalty:+d})\n"
        f"Position: {pos}\n"
        f"{status}"
    )


# ── 7. Cooldown ───────────────────────────────────────────────────────────────

def msg_cooldown_started(streak, cooldown_until_sgt, session_name="",
                         day_losses=0, day_limit=3) -> str:
    remaining = max(0, day_limit - day_losses)
    sline = f"Session: {session_name}\n" if session_name else ""
    return (
        f"🧊 Cooldown Started\n{_DIV}\n"
        f"Reason:  {streak} consecutive losses\n"
        f"{sline}"
        f"Resumes: {cooldown_until_sgt} SGT\n"
        f"Day:     {day_losses}/{day_limit} losses  ({remaining} remaining)"
    )


# ── 8. Daily cap ──────────────────────────────────────────────────────────────

def msg_daily_cap(cap_type, count, limit, window="", daily_pnl=None,
                  session_name="", last_loss_time_sgt="", reset_time_sgt="") -> str:
    label  = ("Max losing trades" if cap_type == "losing_trades"
              else ("Max trades/day" if cap_type == "total_trades" else f"{window} cap"))
    footer = "Resuming next trading day" if cap_type in ("losing_trades","total_trades") else "Resuming next window"
    pline  = f"Day P&L: ${daily_pnl:+.2f}\n" if daily_pnl is not None else ""
    rline  = f"Resets:  {reset_time_sgt}\n"   if reset_time_sgt else ""
    return (
        f"🛑 Cap Reached\n{_DIV}\n"
        f"Type:  {label}\n"
        f"Count: {count}/{limit}\n"
        f"{pline}{rline}"
        f"{footer}"
    )


# ── 8b. New day ───────────────────────────────────────────────────────────────

def msg_new_day_resume(prev_day_pnl=None, prev_day_trades=0, london_open_sgt="16:00") -> str:
    prev = (f"Yesterday: {prev_day_trades} trade(s)  ${prev_day_pnl:+.2f}\n"
            if prev_day_trades > 0 and prev_day_pnl is not None else "")
    return (
        f"✅ New Trading Day\n{_DIV}\n"
        f"Daily limits reset\n"
        f"{prev}"
        f"Next session: London {london_open_sgt} SGT"
    )


# ── 8c. Session cap ───────────────────────────────────────────────────────────

def msg_session_cap(session_name, session_losses, session_limit,
                    day_losses, day_limit, next_session) -> str:
    si  = _session_icon(session_name)
    ni  = _session_icon(next_session)
    rem = max(0, day_limit - day_losses)
    return (
        f"🔶 Session Cap\n{_DIV}\n"
        f"{si} {session_name}: {session_losses}/{session_limit} losses  (paused)\n"
        f"Day: {day_losses}/{day_limit} losses  ({rem} remaining)\n"
        f"{_DIV}\n"
        f"Next: {ni} {next_session}"
    )


# ── 9. Session open ───────────────────────────────────────────────────────────

def msg_session_open(session_name, session_hours_sgt, trade_cap,
                     trades_today, daily_pnl) -> str:
    icon    = _session_icon(session_name)
    pnl_str = f"${daily_pnl:+.2f}" if trades_today > 0 else "—"
    return (
        f"{icon} {session_name} Open  {session_hours_sgt} SGT\n"
        f"{_DIV}\n"
        f"Today:  {trades_today} trade(s)  {pnl_str}  |  cap {trade_cap}\n"
        f"Scanning for EMA + ORB setups..."
    )


# ── 10. Spread skip ───────────────────────────────────────────────────────────

def msg_spread_skip(banner, session_label, spread_pips, limit_pips) -> str:
    _, pair = _split_banner(banner)
    return (
        f"⚠️  Spread Too Wide\n{_DIV}\n"
        f"{pair}  |  {session_label}\n"
        f"Spread: {spread_pips}p  |  Limit: {limit_pips}p  (+{spread_pips - limit_pips} over)\n"
        f"Waiting for spread to normalise"
    )


# ── 11. Order failed ─────────────────────────────────────────────────────────

def msg_order_failed(direction, instrument, units, error,
                     free_margin=None, required_margin=None, retry_attempted=False) -> str:
    mline = (f"Margin: free=${free_margin:.2f}  req=${required_margin:.2f}\n"
             if free_margin is not None and required_margin is not None else "")
    return (
        f"❌ Order Failed\n{_DIV}\n"
        f"{direction}  {instrument}  {int(units):,} units\n"
        f"Error:  {error}\n"
        f"{mline}"
        f"Retry:  {'attempted' if retry_attempted else 'not attempted'}\n"
        f"Check OANDA account and logs"
    )


# ── 11b. Margin adjustment ────────────────────────────────────────────────────

def msg_margin_adjustment(instrument, requested_units, adjusted_units,
                          free_margin, required_margin, reason) -> str:
    action = "Skipping trade" if adjusted_units <= 0 else "Using smaller size"
    return (
        f"⚠️  Margin Protection\n{_DIV}\n"
        f"Pair:      {instrument}\n"
        f"Requested: {int(requested_units):,}\n"
        f"Adjusted:  {int(adjusted_units):,}\n"
        f"Free Mgn:  ${free_margin:.2f}\n"
        f"Req Mgn:   ${required_margin:.2f}\n"
        f"{_DIV}\n"
        f"{action}"
    )


# ── 12. Error ─────────────────────────────────────────────────────────────────

def msg_error(error_type, detail="") -> str:
    dline = f"Detail: {detail}\n" if detail else ""
    return f"❌ Error\n{_DIV}\n{error_type}\n{dline}Check logs"


# ── 13. Friday cutoff ─────────────────────────────────────────────────────────

def msg_friday_cutoff(cutoff_hour_sgt) -> str:
    return (
        f"📅 Friday Cutoff\n{_DIV}\n"
        f"After {cutoff_hour_sgt:02d}:00 SGT — no new entries\n"
        f"Resuming Monday 16:00 SGT"
    )


# ── 14. Startup ───────────────────────────────────────────────────────────────

def msg_startup(
    version, mode, balance, min_score, cycle_minutes=5,
    max_trades_london=10, max_trades_us=10, max_trades_tokyo=10,
    max_losing_day=3, daily_risk_cap_usd=120, trading_day_start_hour=8,
    us_early_end=3, dead_zone_start=4, dead_zone_end=7,
    tokyo_start=8, tokyo_end=15, london_start=16, london_end=20,
    us_start=21, us_end=23, max_total_open=1,
    position_full_usd=40, position_partial_usd=30, score_6_risk_usd=50, session_thresholds=None,
    tg_min_score=4, h1_filter_enabled=True, h1_filter_mode="score_aware",
) -> str:
    thr     = session_thresholds or {}
    lon_thr = thr.get("London", min_score)
    us_thr  = thr.get("US",     min_score)
    tok_thr = thr.get("Tokyo",  min_score + 1)
    if not h1_filter_enabled:
        h1_line = "H1 filter: ⬜ OFF\n"
    elif h1_filter_mode in ("score_aware", "tiered"):
        h1_line = ("H1 filter: ✅ SCORE-AWARE\n"
                   "  • Score 4: H1 aligned required\n"
                   "  • Score 5/6: neutral allowed, opposite blocked\n")
    else:
        h1_line = f"H1 filter: ✅ {h1_filter_mode.upper()}\n"
    return (
        f"🚀 {version} started\n{_DIV}\n"
        f"Mode:      {mode}  |  Balance: ${balance:,.2f}\n"
        f"Pair:      EUR/USD (Fiber)\n"
        f"Strategy:  M5 EMA + ORB + CPR  |  Cycle: {cycle_minutes} min\n"
        f"Min score: {min_score}/6  |  Alerts: score ≥{tg_min_score} only\n"
        f"Risk:      ${position_partial_usd} (score 4)  |  ${position_full_usd} (score 5)  |  ${score_6_risk_usd} (score 6)\n"
        f"{h1_line}"
        f"{_DIV}\n"
        f"Sessions (SGT = UTC+8)\n"
        f"  ✈️  {dead_zone_start:02d}:00–{dead_zone_end:02d}:59  Dead zone\n"
        + ("" if (tok_thr >= 99 and dead_zone_end >= tokyo_end) else
           (f"  🚫 Tokyo      disabled\n" if tok_thr >= 99 else
           f"  🗼 {tokyo_start:02d}:00–{tokyo_end:02d}:59  Tokyo      cap {max_trades_tokyo}  score≥{tok_thr}\n"))
        + f"  🇬🇧 {london_start:02d}:00–{london_end:02d}:59  London     cap {max_trades_london}  score≥{lon_thr}\n"
        + (f"  🚫 US session   disabled\n" if us_start >= 99 else
           f"  🗽 {us_start:02d}:00–{us_end:02d}:59  US         cap {max_trades_us}  score≥{us_thr}\n")
        + (f"  🚫 US cont.    disabled\n" if us_early_end >= 99 else
           f"  🗽 00:00–{us_early_end:02d}:59  US cont.   cap {max_trades_us}  score≥{us_thr}\n")
        + f"{_DIV}\n"
        + f"Day reset: {trading_day_start_hour:02d}:00 SGT  |  Loss cap: {max_losing_day}/day  |  Risk cap: ${daily_risk_cap_usd:.0f}/day\n"
        + f"Global cap: {max_total_open} open trades"
    )


# ── 15. Daily report ─────────────────────────────────────────────────────────

def msg_daily_report(
    day_label, day_stats, wtd_stats, mtd_stats, open_count, report_time,
    blocked_spread=0, blocked_news=0, blocked_signal=0,
    session_stats=None,
) -> str:
    # No trades today
    if day_stats["count"] == 0:
        oline = f"Open now: {open_count} position(s)\n" if open_count > 0 else ""
        return (
            f"📊 Daily Summary — {day_label}\n{_DIV}\n"
            f"No trades closed today\n"
            f"{_DIV}\n"
            f"Month-to-date\n  {_mini_stats(mtd_stats)}\n"
            f"{_DIV}\n"
            f"{oline}"
            f"Report: {report_time}"
        )

    icon  = _pnl_icon(day_stats["net_pnl"])
    oline = f"Open now: {open_count} position(s)\n" if open_count > 0 else ""
    parts = []
    if blocked_spread:  parts.append(f"{blocked_spread} spread")
    if blocked_news:    parts.append(f"{blocked_news} news")
    if blocked_signal:  parts.append(f"{blocked_signal} signal")
    bline = f"Blocked:  {', '.join(parts)}\n" if parts else ""

    best  = day_stats.get("best_trade")
    worst = day_stats.get("worst_trade")
    bst   = f"  Best:     ${best['pnl']:+.2f}  ({best['time']} SGT)\n"   if best  else ""
    wst   = f"  Worst:    ${worst['pnl']:+.2f}  ({worst['time']} SGT)\n" if worst else ""
    isl   = day_stats.get("instant_sl_count", 0)
    islline = f"  ⚡ Instant SL: {isl} trade(s) ≤5min\n" if isl > 0 else ""
    fire  = " 🔥" if day_stats.get("wins", 0) >= 3 else ""

    # Session breakdown (Tokyo / London / US)
    sess_block = ""
    if session_stats:
        sess_block = f"{_DIV}\nSession breakdown\n"
        for name, s in session_stats.items():
            if s["count"] == 0:
                continue
            wr_str   = f"{s['win_rate']:.0f}%"
            wl_str   = f"{s['wins']}W/{s['losses']}L"
            pnl_str  = f"${s['net_pnl']:+.2f}"
            sess_icon = _session_icon(name)
            sess_block += f"  {sess_icon} {name:<12} {wl_str:<8} {wr_str:<6} {pnl_str}\n"

    return (
        f"📊 Daily Summary — {day_label}\n"
        f"{sess_block}"
        f"{_DIV}\n"
        f"Day total\n"
        f"  Trades:   {day_stats['count']}  ({day_stats['wins']}W{fire} / {day_stats['losses']}L)\n"
        f"  Win rate: {day_stats['win_rate']:.0f}%\n"
        f"  Net P&L:  ${day_stats['net_pnl']:+.2f}  {icon}\n"
        f"{bst}{wst}{islline}{bline}"
        f"{_DIV}\n"
        f"Month-to-date\n  {_mini_stats(mtd_stats)}\n"
        f"{_DIV}\n"
        f"{oline}"
        f"Report: {report_time}"
    )


# ── 16. Weekly report ─────────────────────────────────────────────────────────


def _h1_section(h1_stats: dict | None) -> str:
    """Render H1 filter aligned vs counter-trend breakdown block."""
    if not h1_stats:
        return ""
    a  = h1_stats.get("aligned", {})
    ct = h1_stats.get("counter", {})
    if not a and not ct:
        return ""

    lines = [f"{_DIV}\nH1 Filter [soft]\n"]

    mx = max(a.get("win_rate", 0), ct.get("win_rate", 0)) or 1

    if a.get("count", 0) > 0:
        bar = _ascii_bar(a["win_rate"], mx)
        lines.append(
            f"  Aligned    {bar} {a['win_rate']:>5.1f}%  "
            f"{a['wins']}W/{a['losses']}L  ${a['net_pnl']:+.2f}\n"
        )
    else:
        lines.append("  Aligned    — no trades\n")

    if ct.get("count", 0) > 0:
        bar = _ascii_bar(ct["win_rate"], mx)
        lines.append(
            f"  Counter ⚠️  {bar} {ct['win_rate']:>5.1f}%  "
            f"{ct['wins']}W/{ct['losses']}L  ${ct['net_pnl']:+.2f}\n"
        )
    else:
        lines.append("  Counter ⚠️  — no trades\n")

    # Recommendation line
    a_wr  = a.get("win_rate", 0)
    ct_wr = ct.get("win_rate", 0)
    ct_n  = ct.get("count", 0)
    diff  = round(a_wr - ct_wr, 1)

    if ct_n < 5:
        rec = f"  → {ct_n} counter-trend trades — need more data"
    elif diff >= 20:
        rec = f"  → Counter-trend {diff}pts lower — consider strict mode"
    elif diff >= 10:
        rec = f"  → Counter-trend {diff}pts lower — monitor closely"
    else:
        rec = f"  → H1 split similar ({diff}pts) — soft mode justified"

    lines.append(rec + "\n")
    return "".join(lines)


def msg_weekly_report(week_label, stats, sessions, setups, report_time, pairs=None, h1_stats=None) -> str:
    if stats["count"] == 0:
        return f"📅 Weekly Report — {week_label}\n{_DIV}\nNo closed trades.\nReport: {report_time}"

    icon   = _pnl_icon(stats["net_pnl"])
    pf_str = f"{stats['profit_factor']}" if stats["profit_factor"] is not None else "n/a"
    rline  = f"Avg R:       {stats['avg_r']}R\n" if stats.get("avg_r") is not None else ""
    bline  = (f"Best:        ${stats['best_trade']['pnl']:+.2f}  ({stats['best_trade']['time']} SGT)\n"
              if stats.get("best_trade") else "")
    wline  = (f"Worst:       ${stats['worst_trade']['pnl']:+.2f}  ({stats['worst_trade']['time']} SGT)\n"
              if stats.get("worst_trade") else "")

    def _sec(data):
        if not data: return ""
        mx = max(s["win_rate"] for s in data.values()) or 1
        return "".join(
            f"  {n:<10} {_ascii_bar(s['win_rate'],mx)} {s['win_rate']:>5.1f}%  {s['wins']}W/{s['losses']}L  ${s['net_pnl']:+.2f}\n"
            for n, s in data.items()
        )

    def _setup_sec(data):
        if not data: return ""
        mx = max(s["win_rate"] for s in data.values()) or 1
        return "".join(
            f"  {n[:18]:<18} {_ascii_bar(s['win_rate'],mx)} {s['win_rate']:>5.1f}%\n"
            for n, s in data.items()
        )

    pf, wr, n = stats["profit_factor"] or 0, stats["win_rate"], stats["count"]
    if n < 10:     verdict = f"⚠️ Small sample ({n} trades)"
    elif pf >= 1.3 and wr >= 48: verdict = f"✅ Healthy — PF {pf}  WR {wr}%"
    elif pf >= 1.0: verdict = f"🟡 Marginal — PF {pf}  WR {wr}%  Monitor"
    else:           verdict = f"🔴 Negative — PF {pf}  WR {wr}%  Review"

    return (
        f"📅 Weekly Report — {week_label}\n{_DIV}\n"
        f"{icon} Trades: {stats['count']}  ({stats['wins']}W / {stats['losses']}L)\n"
        f"Net P&L:     ${stats['net_pnl']:+.2f}\n"
        f"Win rate:    {wr}%\n"
        f"P.Factor:    {pf_str}\n"
        f"{rline}Streaks:     {stats['max_win_streak']}W / {stats['max_loss_streak']}L max\n"
        f"{bline}{wline}"
        f"{_DIV}\nBy Session\n{_sec(sessions)}"
        f"{_DIV}\nBy Pair\n{_sec(pairs) if pairs else ''}"
        f"{_DIV}\nBy Setup\n{_setup_sec(setups)}"
        + _h1_section(h1_stats)
        + f"{_DIV}\n{verdict}\nReport: {report_time}"
    )


# ── 17. Monthly report ────────────────────────────────────────────────────────

def msg_monthly_report(month_label, stats, sessions, setups, scores,
                       mom_delta, prior_month_pnl, report_time,
                       h1_stats=None) -> str:
    if stats["count"] == 0:
        return f"📆 Monthly Report — {month_label}\n{_DIV}\nNo closed trades.\nReport: {report_time}"

    icon   = _pnl_icon(stats["net_pnl"])
    pf_str = f"{stats['profit_factor']}" if stats["profit_factor"] is not None else "n/a"
    rline  = f"Avg R:         {stats['avg_r']}R\n" if stats.get("avg_r") is not None else ""
    mline  = ""
    if mom_delta is not None and prior_month_pnl is not None:
        di    = "🟢" if mom_delta >= 0 else "🔴"
        mline = f"vs prior:      ${prior_month_pnl:+.2f}  →  {di} {mom_delta:+.2f}\n"
    bline  = (f"Best trade:    ${stats['best_trade']['pnl']:+.2f}  ({stats['best_trade']['time']} SGT)\n"
              if stats.get("best_trade") else "")
    wline  = (f"Worst trade:   ${stats['worst_trade']['pnl']:+.2f}  ({stats['worst_trade']['time']} SGT)\n"
              if stats.get("worst_trade") else "")

    def _sec(data, w=18):
        if not data: return ""
        mx = max(s["win_rate"] for s in data.values()) or 1
        return "".join(
            f"  {n[:w]:<{w}} {_ascii_bar(s['win_rate'],mx)} {s['win_rate']:>5.1f}%  {s['wins']}W/{s['losses']}L\n"
            for n, s in data.items()
        )

    pf, wr, n = stats["profit_factor"] or 0, stats["win_rate"], stats["count"]
    if n < 20:
        verdict, rec = f"⚠️ Small sample ({n} trades)", "Collect more data before any changes."
    elif pf >= 1.3 and wr >= 48:
        verdict, rec = f"✅ Healthy — PF {pf}  WR {wr}%", "System performing well. No changes needed."
    elif pf >= 1.0:
        verdict, rec = f"🟡 Marginal — PF {pf}  WR {wr}%", "Consider raising signal_threshold by +1."
    else:
        verdict, rec = f"🔴 Negative — PF {pf}  WR {wr}%", "Review session breakdown. Pause worst session."

    return (
        f"📆 Monthly Report — {month_label}\n{_DIV}\n"
        f"{icon} Trades: {stats['count']}  ({stats['wins']}W / {stats['losses']}L)\n"
        f"Net P&L:       ${stats['net_pnl']:+.2f}\n"
        f"{mline}"
        f"Win rate:      {wr}%\n"
        f"P.Factor:      {pf_str}\n"
        f"{rline}"
        f"Gross P:       ${stats['gross_profit']:.2f}\n"
        f"Gross L:       ${stats['gross_loss']:.2f}\n"
        f"Streaks:       {stats['max_win_streak']}W / {stats['max_loss_streak']}L max\n"
        f"{bline}{wline}"
        f"{_DIV}\nBy Session\n{_sec(sessions)}"
        f"{_DIV}\nBy Setup\n{_sec(setups)}"
        f"{_DIV}\nBy Score\n{_sec(scores, w=8)}"
        + _h1_section(h1_stats)
        + f"{_DIV}\n{verdict}\n💡 {rec}\nReport: {report_time}"
    )

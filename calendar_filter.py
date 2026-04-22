"""
Economic Calendar Filter - Full Auto Version
=============================================
Uses ForexFactory live feed - updates every week automatically!
No manual updates needed - works forever!

What happens on news days:
- 30 mins BEFORE news = bot pauses trading
- During news          = bot pauses trading
- 30 mins AFTER news   = bot pauses trading
- After 30 mins        = bot resumes normally!
"""

import requests
import logging
from datetime import datetime, timedelta, timezone
import pytz

log = logging.getLogger(__name__)


class EconomicCalendar:
    def __init__(self):
        self.sg_tz   = pytz.timezone("Asia/Singapore")
        self.utc_tz  = pytz.UTC
        self._cache  = None
        self._cached_date = None

    def _fetch_events(self):
        """
        Fetch this week's events from ForexFactory.
        Free JSON feed - auto updates every week!
        Cached per day to avoid too many requests.
        """
        now_sg    = datetime.now(self.sg_tz)
        today_str = now_sg.strftime("%Y-%m-%d")

        # Return cache if same day
        if self._cached_date == today_str and self._cache is not None:
            return self._cache

        try:
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            r   = requests.get(
                url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            if r.status_code != 200:
                log.warning("Calendar API returned: " + str(r.status_code))
                return []

            all_events   = r.json()
            high_impacts = []

            for event in all_events:
                try:
                    impact   = event.get("impact", "").lower()
                    currency = event.get("currency", "")
                    title    = event.get("title", "")
                    date_str = event.get("date", "")

                    # Only HIGH impact events for USD, GBP, EUR
                    if impact != "high":
                        continue
                    if currency not in ["USD", "GBP", "EUR"]:
                        continue

                    high_impacts.append({
                        "date":     date_str,
                        "currency": currency,
                        "title":    title,
                        "impact":   "HIGH"
                    })

                except Exception as e:
                    log.warning("Event parse error: " + str(e))
                    continue

            # Cache result
            self._cache       = high_impacts
            self._cached_date = today_str

            log.info("Calendar loaded! " + str(len(high_impacts)) +
                     " high impact events this week")
            for e in high_impacts:
                log.info("  " + e["currency"] + " " + e["title"] +
                         " @ " + e["date"])

            return high_impacts

        except Exception as e:
            log.warning("Calendar fetch failed: " + str(e))
            return []

    def _get_affected_currencies(self, instrument):
        """Which currencies affect this instrument."""
        affected = ["USD"]  # USD affects everything
        if "EUR" in instrument:
            affected.append("EUR")
        if "GBP" in instrument:
            affected.append("GBP")
        if "XAU" in instrument:
            affected.extend(["EUR", "GBP"])
        return affected

    def _parse_event_utc(self, date_str):
        """
        Parse a ForexFactory date string to a UTC-aware datetime.
        Format: "2026-03-07T13:30:00-0500"  (local time + UTC offset)
        FIX: correctly convert local time to UTC using the offset.
        """
        if not date_str:
            return None

        try:
            if "T" in date_str:
                # Split at the timezone sign (last + or - after position 10)
                clean = date_str[:19]   # "2026-03-07T13:30:00"
                offset_str = date_str[19:]  # e.g. "-0500" or "+0000"

                event_dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")

                # Parse offset and convert to UTC
                if offset_str and (offset_str[0] in ("+", "-")):
                    sign = 1 if offset_str[0] == "+" else -1
                    raw  = offset_str[1:].replace(":", "")  # "0500" or "0530"
                    h    = int(raw[:2])
                    m    = int(raw[2:]) if len(raw) > 2 else 0
                    # event_dt is local time; subtract offset to get UTC
                    # e.g. local 13:30 at -0500 means UTC 18:30 => 13:30 - (-5h) = 18:30
                    offset_td = timedelta(hours=h, minutes=m) * sign
                    event_dt  = event_dt - offset_td
                # Mark as UTC
                return event_dt.replace(tzinfo=self.utc_tz)
            else:
                # Date only — use noon UTC as estimate
                event_dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
                return event_dt.replace(hour=12, tzinfo=self.utc_tz)

        except Exception as e:
            log.warning("Date parse error: " + str(e) + " for: " + date_str)
            return None

    def is_news_time(self, instrument="EUR_USD"):
        """
        Check if current time is within news blackout window.

        Returns: (is_blackout, reason)

        Timeline:
        T-30 mins -> PAUSED (preparing for news)
        T+00 mins -> NEWS RELEASED (very volatile!)
        T+30 mins -> PAUSED (market digesting news)
        T+31 mins -> RESUMED (safe to trade again!)
        """
        now_utc  = datetime.now(timezone.utc)
        affected = self._get_affected_currencies(instrument)
        events   = self._fetch_events()

        if not events:
            log.warning("Calendar unavailable - trading without news filter!")
            return False, ""

        for event in events:
            if event["currency"] not in affected:
                continue

            event_utc = self._parse_event_utc(event.get("date", ""))
            if event_utc is None:
                continue

            try:
                window_start = event_utc - timedelta(minutes=30)
                window_end   = event_utc + timedelta(minutes=30)

                if window_start <= now_utc <= window_end:
                    mins_to = int((event_utc - now_utc).total_seconds() / 60)

                    if mins_to > 0:
                        reason = (event["currency"] + " " + event["title"] +
                                  " in " + str(mins_to) + " mins!")
                    elif mins_to == 0:
                        reason = (event["currency"] + " " + event["title"] +
                                  " releasing NOW!")
                    else:
                        reason = (event["currency"] + " " + event["title"] +
                                  " released " + str(abs(mins_to)) + " mins ago")

                    log.warning("NEWS BLACKOUT: " + reason)
                    return True, reason

            except Exception as e:
                log.warning("News check error: " + str(e))
                continue

        return False, ""

    def get_today_summary(self):
        """
        Get today's high impact events for Telegram morning alert.
        """
        now_sg    = datetime.now(self.sg_tz)
        today_str = now_sg.strftime("%Y-%m-%d")
        events    = self._fetch_events()

        today_events = []
        for event in events:
            try:
                event_utc = self._parse_event_utc(event.get("date", ""))
                if event_utc is None:
                    continue
                # Convert UTC to SGT for date comparison
                event_sgt = event_utc.astimezone(self.sg_tz)
                if event_sgt.strftime("%Y-%m-%d") == today_str:
                    today_events.append((event, event_sgt))
            except Exception:
                continue

        if not today_events:
            return "No high impact news today - safe to trade!"

        lines = ["High impact news TODAY:"]
        for e, event_sgt in today_events:
            time_str = event_sgt.strftime("%H:%M SGT")
            lines.append(e["currency"] + " " + e["title"] + " @ " + time_str)
        lines.append("Bot pauses 30 mins before/after!")
        return "\n".join(lines)

    def get_week_summary(self):
        """Get full week events - useful for Monday morning alert."""
        events = self._fetch_events()
        if not events:
            return "Calendar unavailable this week"

        lines = ["High impact events this week:"]
        for e in events:
            try:
                event_utc = self._parse_event_utc(e.get("date", ""))
                if event_utc:
                    event_sgt = event_utc.astimezone(self.sg_tz)
                    date_str  = event_sgt.strftime("%Y-%m-%d %H:%M SGT")
                else:
                    date_str = e.get("date", "")[:10]
                lines.append(date_str + " " + e["currency"] + ": " + e["title"])
            except Exception:
                continue

        return "\n".join(lines) if len(lines) > 1 else "No high impact events this week"

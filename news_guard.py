"""News Guard: filter High Impact USD + cek jendela blackout (PRD S4.1)."""
from datetime import datetime, timedelta

import requests

from config import FF_CALENDAR_URL, LOCAL_TZ, NEWS_BUFFER_MINUTES


def fetch_high_impact_usd_events():
    """Unduh kalender mingguan, saring USD High Impact. Fail-open: [] saat offline."""
    try:
        res = requests.get(
            FF_CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10
        )
        res.raise_for_status()
        raw = res.json()
    except Exception as e:
        print(f"[ERROR] Gagal fetch kalender: {e}")
        return []
    events = []
    for item in raw:
        if item.get("country") != "USD" or item.get("impact") != "High":
            continue
        if not item.get("date"):
            continue
        events.append(
            {
                "title": item.get("title", "-"),
                "time_wib": datetime.fromisoformat(item["date"]).astimezone(LOCAL_TZ),
                "forecast": item.get("forecast", "-"),
                "previous": item.get("previous", "-"),
            }
        )
    return events


# Alias nama lama agar import existing tak rusak.
fetch_calendar = fetch_high_impact_usd_events


def is_in_news_blackout(events=None, buffer_minutes=NEWS_BUFFER_MINUTES):
    """True + judul jika now dalam +-buffer rilis. events=None -> fetch sendiri."""
    if events is None:
        events = fetch_high_impact_usd_events()
    now = datetime.now(LOCAL_TZ)
    for ev in events:
        # Dukung dict mentah feed maupun dict hasil fetch_high_impact_usd_events.
        if "time_wib" in ev:
            event_dt, title = ev["time_wib"], ev.get("title", "-")
        else:
            if ev.get("country") != "USD" or ev.get("impact") != "High":
                continue
            if not ev.get("date"):
                continue
            event_dt = datetime.fromisoformat(ev["date"]).astimezone(LOCAL_TZ)
            title = ev.get("title", "-")
        if event_dt - timedelta(minutes=buffer_minutes) <= now <= event_dt + timedelta(
            minutes=buffer_minutes
        ):
            return True, title
    return False, None


if __name__ == "__main__":
    evs = fetch_high_impact_usd_events()
    print(f"Ditemukan {len(evs)} event High Impact USD minggu ini:")
    for ev in evs:
        print(f"- [{ev['time_wib']:%d-%b %H:%M WIB}] {ev['title']}")
    blackout, title = is_in_news_blackout(evs)
    print(f"Blackout: {blackout}" + (f" ({title})" if blackout else " (aman)"))

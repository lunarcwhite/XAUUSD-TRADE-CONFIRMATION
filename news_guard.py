"""News Guard: filter High Impact USD + cek jendela blackout (PRD S4.1).

Fail-closed: kalender terakhir disimpan ke disk (ff_calendar_cache.json).
Bila feed gagal dan cache basi (>FEED_MAX_STALE_H jam), main wajib blokir
sinyal — jangan trading buta saat rilis berita.
"""
import json
import os
from datetime import datetime, timedelta

import requests

from config import FF_CALENDAR_URL, LOCAL_TZ, NEWS_BUFFER_MINUTES

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "ff_calendar_cache.json")
FEED_MAX_STALE_H = 48  # cache lebih tua dari ini = basi -> blokir sinyal


def fetch_high_impact_usd_events():
    """Unduh kalender mingguan, saring USD High Impact.

    Sukses -> simpan cache disk + stamp fetched_at. Gagal -> [] (pemanggil
    wajib mempertahankan cache terakhir, bukan mengosongkan).
    """
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
    if events:
        save_cache(events)
    return events


def save_cache(events):
    """Simpan events (time_wib -> ISO) + fetched_at ke disk."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "fetched_at": datetime.now(LOCAL_TZ).isoformat(),
                "events": [
                    {**e, "time_wib": e["time_wib"].isoformat()
                     if isinstance(e.get("time_wib"), datetime) else e.get("time_wib")}
                    for e in events
                ],
            }, f)
    except Exception as e:
        print(f"[ERROR] simpan cache kalender: {e}")


def load_cache():
    """Return (events, fetched_at). Events kosong + fetched None bila tak ada cache."""
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        events = []
        for e in data.get("events", []):
            try:
                e = dict(e)
                e["time_wib"] = datetime.fromisoformat(e["time_wib"]).astimezone(LOCAL_TZ)
                events.append(e)
            except Exception:
                continue
        fetched = None
        try:
            fetched = datetime.fromisoformat(data.get("fetched_at", "")).astimezone(LOCAL_TZ)
        except Exception:
            pass
        return events, fetched
    except (OSError, ValueError):
        return [], None


def is_feed_stale(fetched_at, max_age_h=FEED_MAX_STALE_H) -> bool:
    """True bila tak pernah fetch sukses atau cache lebih tua dari batas."""
    if fetched_at is None:
        return True
    try:
        age_h = (datetime.now(LOCAL_TZ) - fetched_at).total_seconds() / 3600.0
    except Exception:
        return True
    return age_h > max_age_h


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

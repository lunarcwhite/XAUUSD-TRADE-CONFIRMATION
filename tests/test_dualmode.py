"""Test ringan Dual-Mode V3.0 (stdlib unittest, tanpa MT5/jaringan).

Jalankan: python -m unittest tests.test_dualmode -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from zoneinfo import ZoneInfo

import config
import state_manager as sm
from order_manager import check_price_drift, trailing_now
from strategy_router import route
from telegram_bot import trade_expiry_seconds

TZ = ZoneInfo("Asia/Jakarta")


def _mkdf(n, tf_min, end_wib, base=2600.0):
    end_utc = end_wib.astimezone(timezone.utc)
    times = [int((end_utc - timedelta(minutes=tf_min * (n - 1 - i))).timestamp())
             for i in range(n)]
    return pd.DataFrame([{"time": t, "open": base, "high": base + 1.0,
                          "low": base - 1.0, "close": base, "tick_volume": 50}
                         for t in times])


class TestConfig(unittest.TestCase):
    def test_mode_helpers(self):
        self.assertEqual(config.expiry_for_mode("INTRADAY"), 90)
        self.assertEqual(config.expiry_for_mode("SNIPER"), 300)
        self.assertEqual(config.drift_for_mode("INTRADAY"), 0.80)
        self.assertEqual(config.drift_for_mode("SNIPER"), 1.50)
        self.assertEqual(config.trailing_for_mode("INTRADAY"), 1.50)
        self.assertEqual(config.trailing_for_mode("SNIPER"), 2.00)

    def test_intraday_session(self):
        self.assertTrue(config.is_in_intraday_session(
            datetime(2026, 1, 5, 15, 0, tzinfo=TZ)))
        self.assertFalse(config.is_in_intraday_session(
            datetime(2026, 1, 5, 18, 0, tzinfo=TZ)))
        self.assertTrue(config.is_intraday_entry_closed(
            datetime(2026, 1, 5, 22, 30, tzinfo=TZ)))
        self.assertFalse(config.is_intraday_entry_closed(
            datetime(2026, 1, 5, 22, 29, tzinfo=TZ)))


class TestStateManager(unittest.TestCase):
    def test_quota_kill_time(self):
        b = sm.BotStateManager("INTRADAY")
        now = datetime(2026, 1, 5, 15, 0, tzinfo=TZ)
        self.assertFalse(b.validate_daily_intraday_rules(
            balance=10000, total_trades=3, net_pnl=0, now=now)[0])
        ok, _ = b.validate_daily_intraday_rules(
            balance=10000, total_trades=1, net_pnl=-250, now=now)
        self.assertFalse(ok)
        self.assertTrue(b.kill_switch_active)
        late = datetime(2026, 1, 5, 22, 30, tzinfo=TZ)
        self.assertFalse(b.validate_daily_intraday_rules(
            balance=10000, total_trades=0, net_pnl=0, now=late)[0])
        ok, _ = b.validate_daily_intraday_rules(
            balance=10000, total_trades=0, net_pnl=0, now=now)
        self.assertTrue(ok)
        self.assertFalse(b.kill_switch_active)  # pulih -> kunci dibuka

    def test_auto_flat_per_session(self):
        b = sm.BotStateManager("INTRADAY")
        t23 = datetime(2026, 1, 5, 23, 1, tzinfo=TZ)
        self.assertTrue(b.should_auto_flat(now=t23, key="A"))
        b._last_flat_by_key["A"] = t23.date()
        self.assertFalse(b.should_auto_flat(now=t23, key="A"))
        self.assertTrue(b.should_auto_flat(now=t23, key="B"))
        s = sm.BotStateManager("SNIPER")
        self.assertFalse(s.should_auto_flat(now=t23, key="A"))

    def test_mode_command(self):
        b = sm.BotStateManager("SNIPER")
        changed, _ = b.parse_mode_command("/mode intraday")
        self.assertTrue(changed)
        self.assertEqual(b.current_mode, "INTRADAY")


class TestIntradayStrategy(unittest.TestCase):
    def _buy_df(self):
        from strategies.intraday_vwap_m5 import get_vwap_atr
        m5 = _mkdf(60, 5, datetime(2026, 1, 5, 15, 55, tzinfo=TZ))
        for i in range(len(m5)):
            m5.loc[i, "high"] = 2600.0 + (i % 5) * 0.3 + 0.8
            m5.loc[i, "low"] = 2600.0 - (i % 4) * 0.3 - 0.8
        vw, _ = get_vwap_atr(m5)
        m5.loc[len(m5) - 2, "open"] = vw - 0.2
        m5.loc[len(m5) - 2, "close"] = vw + 0.6
        m5.loc[len(m5) - 2, "low"] = vw - 0.1
        m5.loc[len(m5) - 2, "high"] = vw + 1.2
        m15 = _mkdf(30, 15, datetime(2026, 1, 5, 15, 45, tzinfo=TZ))
        return m5, m15, vw

    def test_buy_and_blocks(self):
        from strategies.intraday_vwap_m5 import evaluate
        m5, m15, vw = self._buy_df()
        sig, entry, sl, tp, _ = evaluate(None, m15, m5)
        self.assertEqual(sig, "BUY")
        self.assertGreater(entry, vw)
        self.assertAlmostEqual((tp - entry), 2 * (entry - sl), places=1)
        sig2, *_ = evaluate(None, m15, m5, h1_trend="DOWN")
        self.assertIsNone(sig2)

    def test_vwap_reset(self):
        from strategies.intraday_vwap_m5 import session_vwap_series
        times = [datetime(2026, 1, 5, 5, 55, tzinfo=TZ),
                 datetime(2026, 1, 5, 6, 0, tzinfo=TZ),
                 datetime(2026, 1, 5, 6, 5, tzinfo=TZ)]
        pxs = [2500.0, 2700.0, 2700.0]
        rows = [{"time": int(t.astimezone(timezone.utc).timestamp()), "open": p,
                 "high": p + 1, "low": p - 1, "close": p, "tick_volume": 10}
                for t, p in zip(times, pxs)]
        v = session_vwap_series(pd.DataFrame(rows))
        self.assertAlmostEqual(v.iloc[1], 2700.0, places=1)


class TestRouterTelegramDrift(unittest.TestCase):
    def test_dispatch(self):
        end = datetime(2026, 1, 5, 15, 55, tzinfo=TZ)
        now = datetime(2026, 1, 5, 15, 55, tzinfo=TZ)
        h1, m15, m5 = (_mkdf(80, 60, end), _mkdf(100, 15, end), _mkdf(60, 5, end))
        r = route(h1, m15, events=[], now=now, mode="SNIPER")
        self.assertEqual(len(r), 8)
        r2 = route(h1, m15, events=[], now=now, mode="INTRADAY", m5_df=m5)
        self.assertEqual(r2[5], "INTRADAY")

    def test_expiry_drift_trailing(self):
        self.assertEqual(trade_expiry_seconds({"expiry_seconds": 90}), 90)
        self.assertEqual(trade_expiry_seconds({}), 300)
        ok, msg = check_price_drift(
            {"entry": 2600.0, "action": "BUY", "symbol": "XAUUSD", "mode": "INTRADAY"},
            tick_price=2601.0)
        self.assertFalse(ok)
        self.assertIn("$0.80", msg)
        ok, _ = check_price_drift(
            {"entry": 2600.0, "action": "BUY", "symbol": "XAUUSD", "mode": "SNIPER"},
            tick_price=2601.0)
        self.assertTrue(ok)
        sm.bot_state.set_mode("INTRADAY")
        try:
            self.assertEqual(trailing_now(), 1.50)
        finally:
            sm.bot_state.set_mode("SNIPER")


class TestDbLogger(unittest.TestCase):
    def test_stats_by_mode(self):
        import db_logger as L
        tmp = os.path.join(tempfile.gettempdir(), "test_dualmode_journal.db")
        if os.path.exists(tmp):
            os.remove(tmp)
        old = L.DB_NAME
        L.DB_NAME = tmp
        try:
            L.init_db()
            today = datetime.now(TZ).strftime("%Y-%m-%d")

            def _row(tid, pnl, mode):
                return {"deal_ticket": tid, "position_ticket": tid, "symbol": "XAUUSD",
                        "trade_type": "BUY", "volume": 0.01,
                        "close_time": today + " 15:00:00", "close_price": 2600,
                        "profit": pnl, "commission": 0, "swap": 0, "net_profit": pnl,
                        "user_id": "u1", "mode_used": mode}
            self.assertEqual(L.insert_closed_rows(
                [_row(1, 10, "SNIPER"), _row(2, -5, "INTRADAY")], user_id="u1"), 2)
            st = L.daily_stats_by_mode(user_id="u1", day=today, db_path=tmp)
            self.assertEqual(st["SNIPER"]["n"], 1)
            self.assertEqual(st["INTRADAY"]["n"], 1)
        finally:
            L.DB_NAME = old
            if os.path.exists(tmp):
                os.remove(tmp)


if __name__ == "__main__":
    unittest.main()

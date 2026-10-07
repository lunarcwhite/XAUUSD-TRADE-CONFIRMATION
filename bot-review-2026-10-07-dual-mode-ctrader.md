# Code Review: Gold Bot Updates (Oct 7, 2026)

Reviewed commits vs last review (5b8c603):
- **e359de8** — cTrader adapter (OAuth2 + ProtoBuf), service identity, checklist spot-check. Status: BETA, not live-verified.
- **83a2a1e** — Dual-Mode V3.0: Sniper M15 + Intraday M5 (VWAP/ATR), drift guard, expiry 90/300, quota/kill-switch, auto-flat, per-mode reports + tests.

Read-only review. Nothing was changed; the running paper bot was not touched.

## Verdict

Ambitious and mostly well-built — the dual-mode dispatch is clean and the fail-closed guards from review #1 survived. But **4 critical issues must be fixed before the new parts are used**, and cTrader must stay paper/BETA.

## Critical issues (fix before using)

**C1 — The daily trade limit and kill-switch can never fire.**
`state_manager.py` `get_today_stats()` counts today's trades and PnL from the `trade_history` table — but the only thing that writes to that table is the 23:55 daily reporter. Nothing logs a close during the day. So all day long, "today's" stats read as 0: the 3-trade quota never blocks and the 2% kill-switch never trips on today's losses. The docstring even blesses this: "DB hilang/rusak -> fail-open (0 trade, 0 pnl)" — a deliberate fail-open, against the fail-closed principle from review #1. The unit test injects stats directly, so it never catches this.
*Fix: log closes to `trade_history` at close time (in the lifecycle manager), or merge live `broker.fetch_closed()` into the stats.*

**C2 — cTrader access tokens stored plaintext in SQLite (repeat of review #1).**
`user_store.py` adds a `ctrader_access_token` column; onboarding takes the raw token from Telegram chat → `save_user` → plaintext in `bot_users.db`. Same flaw as the first review, now for the new broker. With `OPEN_REGISTRATION=true`, anyone's token lands unencrypted on disk.

**C3 — Auto-flat cannot actually close positions.**
`state_manager.close_all_positions()` calls `broker.partial_close(pos, 1.0)` because no adapter implements `close_position`. But both the paper adapter and the cTrader adapter refuse `ratio=1.0` (remaining volume would drop below minimum → returns failure), and the fallback `_mt5_close_raw` fails on Linux. Result: at 23:00 in INTRADAY mode, auto-flat reports failure and positions stay open overnight. Worse, the "flat day" mark is set even when every close fails, so there is no retry.

**C4 — cTrader `list_positions` leaks other symbols when `symbol=None`.**
In `broker_ctrader.py::list_positions`, the symbol filter only applies when a symbol is passed. With `symbol=None` the filter never applies: every position on the account is returned, mislabeled as XAUUSD. `close_all_positions` with `symbol=None` would then try to close other symbols' positions. (The paper adapter filters correctly.)

## Warnings

- **W1 — Drift guard fails open** (`order_manager.py::check_price_drift`): if the tick fetch fails it returns "ok, skip drift check" → the order executes blind. The exact failure mode it guards against (unknown/stale price) is when tick fetch fails.
- **W2 — cTrader `VOL_PER_LOT = 100_000` is unverified for gold.** Honestly flagged as BETA with a startup warning, but if wrong, position sizing could be off ~10x. Verify on demo before any live order.
- **W3 — No OAuth token refresh** in the cTrader adapter; an expired token means a dead bot until manual re-onboarding. `list_accounts` only queries the demo host, and it leaks one TCP client per call.
- **W4 — cTrader `get_tick` invents a $0.30 spread** (`bid=px-0.15, ask=px+0.15`) when the spot subscription drops — drift guard and sizing then run on fake prices.
- **W5 — cTrader `_positions()` returns `[]` on API error** (fail-open): lifecycle/auto-flat would believe "no positions" during an outage. Same issue class as review #1.
- **W6 — M5 strategy looseness**: `touch_tol = max(0.50, 0.25*ATR)` is uncapped (very permissive in volatility); entry uses a 2-bar-old close which goes stale fast on M5 (partly mitigated by the 90s expiry + $0.80 drift guard); VWAP only sees 80 M5 bars (~6.7h), so it's a partial-session VWAP early in the day.
- **W7 — Mode is process-global** (`bot_state` singleton): one user's `/mode` switches everyone, and `trailing_now()` changes trailing on already-running positions immediately.
- **W8 — `mode_used` attribution happens at the 23:55 sync**, not at trade time — quota accounting can misattribute if modes are switched mid-day.

## What's good

- **Dual-mode dispatch is clean**: the router runs exactly one branch (SNIPER 4-strategy or INTRADAY M5) — no double-positioning. `MAX_OPEN_POSITIONS=1` and the fail-closed `has_open_position` from review #1 are preserved.
- **Per-trade mode + expiry**: pending trades store `mode`/`expiry_seconds`; the Telegram listener and expiry cleaner both honor per-trade 90/300s expiry. The drift guard ($0.80/$1.50) at approval is a good stale-fill defense.
- **cTrader adapter is honestly scoped**: BETA/unverified labels, fail-closed `initialize()` on missing credentials, placeholder-aware config validation, safe `demo` default, secrets never printed. The `_TF_PERIOD` enum mapping was verified against the real `ctrader-open-api` 0.9.2 package — all six values correct (M1=1, M5=5, M15=7, H1=9, H4=10, D1=12).
- **Schema migrations are defensive** (ALTER TABLE only-if-missing + 3-level INSERT fallback) — deploying over the existing DB won't crash; old rows attribute to SNIPER sensibly.
- **Defense in depth kept**: news blackout still applies to the INTRADAY branch; session/cutoff checks in both strategy and state manager; per-symbol feed grouping preserved (no cross-symbol leak in the paper path).
- **Tests exist** and cover the pure logic (quota/kill math, sessions, VWAP reset, router, drift, expiry) — but not the stale-DB path (C1) or the full-close path (C3).

## What this means for your running paper bot

- **New env keys are all optional with safe defaults**: `BOT_MODE` defaults to `SNIPER` = today's behavior. Nothing changes unless you run `/mode intraday`. `CTRADER_*` only needed for cTrader.
- `requirements.txt` adds `ctrader-open-api` + `service_identity` — not needed for paper; don't install unless onboarding cTrader.
- DB auto-migrates on first run (adds `mode_used` columns). No manual migration needed.
- Small behavior changes even in SNIPER/paper: trailing distance read live per mode (still $2.00 in SNIPER), expiry-cleaner wording, one auto-flat thread per session (no-op unless INTRADAY).
- **Do not use INTRADAY mode until C1 + C3 are fixed** (the brakes don't work and flat doesn't work). **Do not go live on cTrader** (BETA, W2/W3).
- Regressions from review #1 re-checked: fail-closed news guard intact, per-symbol feeds intact, governor's `has_open_position` unchanged and still fail-closed.

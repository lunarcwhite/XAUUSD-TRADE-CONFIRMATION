"""Eksekusi MT5 + lifecycle Partial/BE/trailing + generik dual-broker (PRD V3.0 S5.4-5.5)."""
import time

from config import (
    BE_BUFFER,
    DEVIATION,
    MAGIC_NUMBER,
    RISK_PERCENT,
    TRAILING_DISTANCE,
    TRAILING_STEP,
    drift_for_mode,
)


def _mt5():
    import MetaTrader5 as mt5

    return mt5

done_11 = set()  # tiket yang sudah lewat Fase 1 (1:1)


def trailing_now(mode=None) -> float:
    """Jarak trailing aktif: $1.50 Intraday vs $2.00 Sniper (PRD 5.5).

    mode None -> baca state_manager.bot_state live agar /mode switch
    langsung berlaku untuk posisi berjalan.
    """
    try:
        if mode is None:
            from state_manager import bot_state
            mode = bot_state.current_mode
        from config import trailing_for_mode
        return float(trailing_for_mode(mode))
    except Exception:
        return float(TRAILING_DISTANCE)


def check_price_drift(trade, tick_price=None, broker=None, mode=None):
    """Price Drift Guard (PRD 5.4): tolak eksekusi bila tick bergeser > batas mode.

    tick_price None + broker ada -> ambil tick live (ask BUY / bid SELL).
    Fail-CLOSED (review W1): tick tak tersedia/tak valid -> TOLAK, karena
    eksekusi buta harga persis mode kegagalan yang dijaga guard ini.
    Return (ok: bool, detail: str).
    """
    try:
        entry = float(trade.get("entry", 0) or 0)
    except Exception:
        return False, "❌ Order Dibatalkan: harga entry sinyal invalid!"
    if not entry:
        return False, "❌ Order Dibatalkan: harga entry sinyal invalid!"
    if mode is None:
        mode = trade.get("mode") or trade.get("mode_used")
    if mode is None:
        try:
            from state_manager import bot_state
            mode = bot_state.current_mode
        except Exception:
            mode = "SNIPER"
    try:
        limit = float(drift_for_mode(mode))
    except Exception:
        limit = 1.50
    px = tick_price
    if px is None and broker is not None:
        try:
            tick = broker.get_tick(trade.get("symbol"))
            if tick is None:
                return False, ("❌ Order Dibatalkan: harga live tak tersedia "
                               "(drift guard fail-closed)!")
            is_buy = str(trade.get("action", "")).upper() == "BUY"
            px = float(tick.ask if is_buy else tick.bid)
        except Exception as e:
            return False, f"❌ Order Dibatalkan: tick error ({e})!"
    if px is None:
        return False, ("❌ Order Dibatalkan: tanpa harga pembanding "
                       "(drift guard fail-closed)!")
    try:
        drift = abs(float(px) - entry)
    except Exception:
        return False, "❌ Order Dibatalkan: harga pembanding invalid!"
    if drift > limit:
        return (False,
                f"❌ Order Dibatalkan: Harga telah bergeser > ${limit:.2f} dari kalkulasi awal!")
    return True, f"drift ok (${drift:.2f} <= ${limit:.2f})"


def calculate_lot(entry, sl):
    """Lot = (Balance*1%) / (|Entry-SL|*100). 2 desimal, min 0.01."""
    mt5 = _mt5()
    acct = mt5.account_info()
    if acct is None:
        return 0.01
    dist = abs(entry - sl)
    if dist <= 0:
        return 0.01
    return max(0.01, round(acct.balance * RISK_PERCENT / (dist * 100), 2))


def get_filling_mode(symbol):
    mt5 = _mt5()
    sym = mt5.symbol_info(symbol)
    if not sym:
        return mt5.ORDER_FILLING_RETURN
    if sym.filling_mode & mt5.ORDER_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    if sym.filling_mode & mt5.ORDER_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def execute_market_order(trade):
    """Eksekusi market BUY/SELL. trade: symbol/action/lot/sl/tp."""
    mt5 = _mt5()
    tick = mt5.symbol_info_tick(trade["symbol"])
    if not tick:
        return False, "Tick broker tak tersedia"
    is_buy = trade["action"] == "BUY"
    result = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": trade["symbol"],
        "volume": float(trade["lot"]),
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if is_buy else tick.bid,
        "sl": float(trade["sl"]),
        "tp": float(trade["tp"]),
        "deviation": DEVIATION,
        "magic": MAGIC_NUMBER,
        "comment": "Telegram Semi-Auto",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": get_filling_mode(trade["symbol"]),
    })
    if result is None:
        return False, f"order_send gagal: {mt5.last_error()}"
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return False, f"Broker menolak ({result.retcode}: {result.comment})"
    return True, f"Ticket #{result.order} @ {result.price:.2f}"


def modify_position_sl(ticket, symbol, new_sl, tp):
    mt5 = _mt5()
    r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP,
                        "position": ticket, "symbol": symbol,
                        "sl": float(round(new_sl, 2)),
                        "tp": float(round(tp, 2))})
    return r is not None and r.retcode == mt5.TRADE_RETCODE_DONE


def execute_partial_close(pos, ratio=0.5):
    """Tutup `ratio` volume. Return (ok, closed_vol)."""
    mt5 = _mt5()
    sym = mt5.symbol_info(pos.symbol)
    close_vol = round(round(pos.volume * ratio / sym.volume_step)
                      * sym.volume_step, 2)
    if close_vol < sym.volume_min or (pos.volume - close_vol) < sym.volume_min:
        print(f"[SKIP PARTIAL] #{pos.ticket} vol {pos.volume} terlalu kecil.")
        return False, 0.0
    is_buy = pos.type == mt5.POSITION_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    r = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL, "position": pos.ticket,
        "symbol": pos.symbol, "volume": close_vol,
        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
        "price": tick.bid if is_buy else tick.ask,
        "deviation": DEVIATION, "magic": MAGIC_NUMBER,
        "comment": "Partial TP 50%",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": get_filling_mode(pos.symbol),
    })
    return (True, close_vol) if r and r.retcode == mt5.TRADE_RETCODE_DONE else (False, 0.0)


def position_lifecycle_manager(send_text):
    """Partial 50% + BE di 1:1, lalu trailing $2.00/$1.50 per mode. Poll 2 dtk (MT5)."""
    mt5 = _mt5()
    print("🚀 Lifecycle manager aktif (Partial + BE + Trailing)...")
    while True:
        try:
            positions = mt5.positions_get() or []
            live = {p.ticket for p in positions}
            done_11.intersection_update(live)  # buang tiket yang sudah tutup
            for pos in positions:
                if pos.magic != MAGIC_NUMBER or pos.sl == 0.0:
                    continue
                tick = mt5.symbol_info_tick(pos.symbol)
                if not tick:
                    continue
                is_buy = pos.type == mt5.POSITION_TYPE_BUY
                px = tick.bid if is_buy else tick.ask  # PRD: Bid BUY / Ask SELL
                risk = (pos.price_open - pos.sl) if is_buy else (pos.sl - pos.price_open)
                if risk <= 0:
                    continue
                target = pos.price_open + risk if is_buy else pos.price_open - risk

                if pos.ticket not in done_11 and (
                        (is_buy and px >= target) or (not is_buy and px <= target)):
                    ok, closed_lot = execute_partial_close(pos)
                    new_sl = pos.price_open + BE_BUFFER if is_buy else pos.price_open - BE_BUFFER
                    modify_position_sl(pos.ticket, pos.symbol, new_sl, pos.tp)
                    done_11.add(pos.ticket)
                    send_text(
                        f"🎯 *R:R 1:1 ({pos.type and 'BUY' if is_buy else 'SELL'} #{pos.ticket})*\n"
                        f"💰 *Partial:* `{closed_lot} Lot`"
                        f"{'' if ok else ' (skip, vol kecil)'}\n"
                        f"🛡️ *BE:* SL → `{new_sl:.2f}`")
                    print(f"[PARTIAL+BE] #{pos.ticket}")
                elif ((is_buy and pos.sl >= pos.price_open)
                        or (not is_buy and pos.sl <= pos.price_open)):
                    _trail = trailing_now()
                    pot = px - _trail if is_buy else px + _trail
                    if ((is_buy and pot > pos.sl + TRAILING_STEP)
                            or (not is_buy and pot < pos.sl - TRAILING_STEP)):
                        if modify_position_sl(pos.ticket, pos.symbol, pot, pos.tp):
                            print(f"[TRAIL] #{pos.ticket} SL → {pot:.2f}")
        except Exception as e:
            print(f"[ERROR] lifecycle: {e}")
        time.sleep(2)


# ================= GENERIC (dual-broker MT5/OANDA) =================
_done_generic = set()


def make_execute_via_broker(broker):
    """Adaptor telegram_button_listener -> broker.market_order. trade: symbol/action/lot/sl/tp."""
    def _exec(trade):
        try:
            return broker.market_order(
                trade.get("symbol"), trade.get("action"),
                float(trade.get("lot", 0.01)),
                float(trade.get("sl")), float(trade.get("tp")))
        except Exception as e:
            return False, f"Eksekusi gagal: {e}"
    return _exec


def generic_position_lifecycle_manager(broker, send_text, symbol=None, stop_event=None):
    """Lifecycle broker-agnostic: Partial 50% + BE di 1:1, trailing per-mode."""
    print(f"🚀 Lifecycle manager aktif [{getattr(broker, 'name', '?')}] ...")
    while stop_event is None or not stop_event.is_set():
        try:
            positions = broker.list_positions(symbol) if symbol else broker.list_positions()
            if positions is None:
                # Status tak diketahui (broker error) -> lewati iterasi TANPA
                # mengubah state _done (jangan picu partial ganda pasca-outage).
                time.sleep(2)
                continue
            live = {str(p.ticket) for p in positions}
            _done_generic.intersection_update(live)
            for pos in positions:
                if not pos.sl:
                    continue
                tick = broker.get_tick(pos.symbol)
                if not tick:
                    continue
                is_buy = pos.side == "BUY"
                px = tick.bid if is_buy else tick.ask
                risk = (pos.price_open - pos.sl) if is_buy else (pos.sl - pos.price_open)
                if risk <= 0:
                    continue
                target = pos.price_open + risk if is_buy else pos.price_open - risk
                key = str(pos.ticket)
                if key not in _done_generic and (
                        (is_buy and px >= target) or (not is_buy and px <= target)):
                    ok, closed_lot = broker.partial_close(pos, 0.5)
                    new_sl = pos.price_open + BE_BUFFER if is_buy else pos.price_open - BE_BUFFER
                    broker.modify_sltp(pos.ticket, pos.symbol, new_sl, pos.tp)
                    _done_generic.add(key)
                    send_text(
                        f"🎯 *R:R 1:1 ({pos.side} #{pos.ticket})*\n"
                        f"💰 *Partial:* `{closed_lot} Lot`"
                        f"{'' if ok else ' (skip, vol kecil)'}\n"
                        f"🛡️ *BE:* SL → `{new_sl:.2f}`")
                    print(f"[PARTIAL+BE] #{pos.ticket}")
                elif ((is_buy and pos.sl >= pos.price_open)
                        or (not is_buy and pos.sl <= pos.price_open)):
                    _trail = trailing_now()
                    pot = px - _trail if is_buy else px + _trail
                    if ((is_buy and pot > pos.sl + TRAILING_STEP)
                            or (not is_buy and pot < pos.sl - TRAILING_STEP)):
                        if broker.modify_sltp(pos.ticket, pos.symbol, pot, pos.tp):
                            print(f"[TRAIL] #{pos.ticket} SL → {pot:.2f}")
        except Exception as e:
            print(f"[ERROR] lifecycle: {e}")
        time.sleep(2)

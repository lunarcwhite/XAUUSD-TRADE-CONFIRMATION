"""Render chart snapshot sinyal Dual-Mode (PRD V3.0 S5.6 + chart M15/M5).

- Sniper M15: candlestick + EMA21/50 + panel RSI + garis Entry/SL/TP.
- Intraday M5: sama + overlay Session VWAP (garis cyan) bila `vwap` diberikan.
`vwap` bisa Series (garisUTS) atau skalar (hline). Kolom indikator yang hilang
dihitung otomatis agar df M5 mentah tak crash (bug lama: KeyError EMA50/RSI).
"""
import mplfinance as mpf
import pandas as pd

RSI_PERIOD = 14


def _ensure_indicators(plot_df: pd.DataFrame) -> pd.DataFrame:
    if "EMA21" not in plot_df.columns:
        plot_df["EMA21"] = plot_df["close"].ewm(span=21, adjust=False).mean()
    if "EMA50" not in plot_df.columns:
        plot_df["EMA50"] = plot_df["close"].ewm(span=50, adjust=False).mean()
    if "RSI" not in plot_df.columns:
        import numpy as _np
        delta = plot_df["close"].diff()
        gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
        ag = gain.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
        al = loss.ewm(alpha=1 / RSI_PERIOD, min_periods=RSI_PERIOD, adjust=False).mean()
        rs = ag / al.replace(0, float("nan"))
        rs = rs.replace([_np.inf, -_np.inf], 100.0)
        plot_df["RSI"] = 100 - (100 / (1 + rs))
    return plot_df


def generate_signal_chart(df, entry, sl, tp, symbol, filename="signal_chart.png",
                          title_suffix="", vwap=None, timeframe="M15"):
    """Candlestick (45 bar) + EMA21/50 + VWAP opsional + panel RSI + Entry/SL/TP.

    timeframe: label TF ("M15"/"M5"), dipakai bila title_suffix kosong.
    vwap: Series (digambar sebagai garis) atau skalar (digambar sebagai hline).
    """
    plot_df = df.tail(45).copy()
    plot_df = _ensure_indicators(plot_df)
    # VWAP Series disejajarkan dengan window plot bila panjangnya cocok df penuh.
    vwap_line = None
    try:
        if vwap is not None and not isinstance(vwap, (int, float)):
            vs = pd.Series(vwap)
            vwap_line = vs.tail(45).reset_index(drop=True) if len(vs) >= len(df) else vs
            vwap_line.index = plot_df.index
    except Exception:
        vwap_line = None
    plot_df["time"] = pd.to_datetime(plot_df["time"], unit="s")
    plot_df.set_index("time", inplace=True)
    plot_df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "tick_volume": "Volume",
        },
        inplace=True,
    )
    addplots = [
        mpf.make_addplot(plot_df["EMA21"], panel=0, color="#FFEB3B", width=1.2),
        mpf.make_addplot(plot_df["EMA50"], panel=0, color="#FF9800", width=1.5),
        mpf.make_addplot(
            plot_df["RSI"], panel=1, color="#AB47BC", width=1.3,
            ylabel="RSI (14)", ylim=(10, 90),
        ),
        mpf.make_addplot([70] * len(plot_df), panel=1, color="#78909C",
                          linestyle="--", width=0.8),
        mpf.make_addplot([30] * len(plot_df), panel=1, color="#78909C",
                          linestyle="--", width=0.8),
    ]
    if vwap_line is not None:
        try:
            addplots.insert(2, mpf.make_addplot(
                vwap_line, panel=0, color="#00E5FF", width=1.4,
                linestyle="--", label="VWAP"))
        except Exception:
            pass
    hlines, hcolors = [entry, sl, tp], ["#1E88E5", "#E53935", "#43A047"]
    try:
        if isinstance(vwap, (int, float)):
            import math
            if not math.isnan(float(vwap)):
                hlines.append(float(vwap))
                hcolors.append("#00E5FF")
    except Exception:
        pass
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        rc={"font.size": 8},
        marketcolors=mpf.make_marketcolors(
            up="#26a69a", down="#ef5350", edge="inherit", wick="inherit"),
    )
    default_title = f"\n{symbol} {timeframe} - Confirmation Setup (EMA 50 & RSI 14)"
    mpf.plot(
        plot_df, type="candle", style=style, addplot=addplots,
        title=(f"\n{symbol} {timeframe} - {title_suffix}" if title_suffix
               else default_title),
        ylabel="Price (USD)",
        hlines=dict(hlines=hlines, colors=hcolors,
                    linestyle="--", linewidths=1.2),
        panel_ratios=(3, 1),
        savefig=dict(fname=filename, dpi=120, bbox_inches="tight"),
        figsize=(10, 6.5),
    )
    return filename


if __name__ == "__main__":
    print("chart_engine: import generate_signal_chart dari main.py.")

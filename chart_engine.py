"""Render chart snapshot sinyal (PRD S4.3)."""
import mplfinance as mpf
import pandas as pd


def generate_signal_chart(df, entry, sl, tp, symbol, filename="signal_chart.png",
                          title_suffix=""):
    """Candlestick M15 (45 bar) + EMA21/50 overlay + panel RSI + garis Entry/SL/TP."""
    plot_df = df.tail(45).copy()
    if "EMA21" not in plot_df.columns:
        plot_df["EMA21"] = plot_df["close"].ewm(span=21, adjust=False).mean()
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
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        rc={"font.size": 8},
        marketcolors=mpf.make_marketcolors(
            up="#26a69a", down="#ef5350", edge="inherit", wick="inherit"),
    )
    mpf.plot(
        plot_df, type="candle", style=style, addplot=addplots,
        title=f"\n{symbol} M15{(' - ' + title_suffix) if title_suffix else ' - Confirmation Setup (EMA 50 & RSI 14)'}",
        ylabel="Price (USD)",
        hlines=dict(hlines=[entry, sl, tp],
                    colors=["#1E88E5", "#E53935", "#43A047"],
                    linestyle="--", linewidths=1.2),
        panel_ratios=(3, 1),
        savefig=dict(fname=filename, dpi=120, bbox_inches="tight"),
        figsize=(10, 6.5),
    )
    return filename


if __name__ == "__main__":
    print("chart_engine: import generate_signal_chart dari main.py.")

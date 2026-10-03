# PRODUCT REQUIREMENTS DOCUMENT (PRD) - V2.0

## Project: XAU/USD Adaptive Dual-Strategy Trading Assistant System

---

### 1. RINGKASAN PROYEK & TUJUAN

Membangun bot asisten trading semi-otomatis adaptif untuk instrumen XAU/USD berbasis MetaTrader 5 Python API dan Telegram Bot. Sistem ini memiliki kemampuan **Market Regime Detection** yang secara otomatis memilih strategi yang tepat:

1. Mendeteksi rezim pasar pada timeframe H1 (Trending vs. Ranging).
2. Mengeksekusi strategi **Session Liquidity Sweep** saat pasar konsolidasi/ranging.
3. Mengeksekusi strategi **HTF Trend Pullback** saat pasar bergerak dalam tren kuat.
4. Memvalidasi setup dengan filter _News Guard_ (Forex Factory) dan momentum RSI 14.
5. Mengirimkan visualisasi grafik (_chart snapshot_) dengan penanda rezim dan tombol persetujuan interaktif ke Telegram.
6. Mengeksekusi order instan jika dikonfirmasi pengguna dalam 5 menit.
7. Mengelola posisi aktif (Partial Take Profit 50% di 1:1, Auto Break-Even, dan Dynamic Trailing Stop).
8. Mencatat transaksi ke SQLite dan mengirim kurva ekuitas harian pada 23:55 WIB.

---

### 2. TECH STACK & DEPENDENCIES

- **Runtime & OS:** Python 3.10+ di Windows (Native MT5 Library support).
- **Broker API:** `MetaTrader5` library resmi.
- **Data & Analisis:** `pandas`, `numpy`.
- **Visualisasi:** `mplfinance`, `matplotlib`.
- **Jaringan & HTTP:** `requests`.
- **Database:** `sqlite3` (Bawaan Python).
- **Timezone:** `zoneinfo` (`Asia/Jakarta` / WIB).

---

### 3. STRUKTUR PROYEK MODULAR

AI Agent wajib membagi arsitektur strategi menjadi router terpisah:

gold-trade-bot/
├── PRD.md # Dokumen spesifikasi utama (Ground Truth)
├── config.py # Konfigurasi kredensial, parameter trading, dan path
├── news_guard.py # Kalender ekonomi Forex Factory & verifikasi blackout
├── chart_engine.py # Render visualisasi candlestick + indikator via mplfinance
├── telegram_bot.py # Foto dispatch, inline keyboard, listener, & expiry cleaner
├── order_manager.py # Eksekusi order MT5 & Lifecycle Manager (BE/Partial/Trailing)
├── db_logger.py # SQLite persistence & Equity Curve generator
├── strategies/
│ ├── **init**.py
│ ├── regime_classifier.py # Deteksi tren H1 (ADX & EMA 50/200)
│ ├── session_sweep.py # Logika strategi Session Liquidity Sweep
│ └── trend_pullback.py # Logika strategi HTF Trend Pullback
├── strategy_router.py # Dispatcher: Memilih strategi berdasarkan regime
└── main.py # Loop utama (M15 New Bar Detector)

---

### 4. SPESIFIKASI BISNIS & LOGIKA SISTEM

#### 4.1. Modul News Guard (`news_guard.py`)

- Sumber: Forex Factory JSON feed mingguan.
- Filter: Currency `USD` dengan `impact == "High"`.
- Jendela Blackout: ±30 menit dari jam rilis berita.
- Jika waktu saat ini berada dalam jendela blackout, sistem langsung membatalkan evaluasi strategi (_short-circuit_).

#### 4.2. Market Regime Classifier (`strategies/regime_classifier.py`)

Klasifikasi pasar dihitung pada timeframe **H1** setiap candle M15 selesai:

- **Kalkulasi Indikator H1 (Pure Pandas):**
  - EMA 50 & EMA 200.
  - ADX 14 (Average Directional Index): Menggunakan smoothed TR, +DM, dan -DM.
- **Logika Klasifikasi:**
  - **Rezim TRENDING:** Terpenuhi jika `ADX >= 25` DAN susunan EMA rapi:
    - _Uptrend:_ `Close H1 > EMA 50 > EMA 200`.
    - _Downtrend:_ `Close H1 < EMA 50 < EMA 200`.
  - **Rezim RANGING:** Terpenuhi jika `ADX < 25` ATAU susunan EMA saling bersilangan (_entangled/flat_).

#### 4.3. Detail Dua Strategi

##### Strategi A: Session Liquidity Sweep (`strategies/session_sweep.py`)

- **Aktif Saat:** Rezim terdeteksi `RANGING`.
- **Waktu Eksekusi:** 14:00 – 23:00 WIB (Sesi London & New York Open).
- **Setup BUY:**
  1. Jarum candle M15 (`Low[-2]` atau `Low[-3]`) sempat menembus ke bawah `Asian Low` (06:00–14:00 WIB).
  2. Lilin M15 ditutup kembali di atas `Asian Low` (`Close[-2] > Asian Low`).
  3. Lilin konfirmasi bullish (`Close[-2] > Open[-2]`).
  4. RSI 14 memantul dari area oversold (`RSI <= 35` dan `RSI[-2] > RSI[-3]`).
  5. SL: `min(Low[-2], Low[-3]) - 1.00`. TP: Minimal 1:2 R:R.
- **Setup SELL:**
  1. Jarum candle M15 (`High[-2]` atau `High[-3]`) sempat menembus ke atas `Asian High`.
  2. Lilin M15 ditutup kembali di bawah `Asian High` (`Close[-2] < Asian High`).
  3. Lilin konfirmasi bearish (`Close[-2] < Open[-2]`).
  4. RSI 14 memantul dari area overbought (`RSI >= 65` dan `RSI[-2] < RSI[-3]`).
  5. SL: `max(High[-2], High[-3]) + 1.00`. TP: Minimal 1:2 R:R.

##### Strategi B: HTF Trend Pullback (`strategies/trend_pullback.py`)

- **Aktif Saat:** Rezim terdeteksi `TRENDING`.
- **Waktu Eksekusi:** Fleksibel sepanjang sesi London & NY (14:00 – 02:00 WIB).
- **Setup BUY (Uptrend H1):**
  1. Harga di M15 terkoreksi turun menyentuh zona dinamis antara EMA 21 dan EMA 50 M15.
  2. RSI 14 di M15 sempat menyentuh level 40–48 (area pullback sehat pada uptrend).
  3. Muncul lilin konfirmasi rejection bullish (`Close[-2] > EMA 21[-2]`).
  4. SL: 1.00 di bawah Swing Low koreksi terdekat. TP: Minimal 1:2 R:R.
- **Setup SELL (Downtrend H1):**
  1. Harga di M15 terkoreksi naik menyentuh zona dinamis antara EMA 21 dan EMA 50 M15.
  2. RSI 14 di M15 sempat menyentuh level 52–60 (area pullback sehat pada downtrend).
  3. Muncul lilin konfirmasi rejection bearish (`Close[-2] < EMA 21[-2]`).
  4. SL: 1.00 di atas Swing High koreksi terdekat. TP: Minimal 1:2 R:R.

#### 4.4. Modul Visualisasi Grafik (`chart_engine.py`)

- Menggunakan `mplfinance` tema gelap (`nightclouds`).
- **Panel Atas (Price):** 45 candle M15, EMA 21 (Kuning), EMA 50 (Oranye), garis Entry (Biru), SL (Merah), TP (Hijau).
- **Panel Bawah (Oscillator):** Sub-panel RSI 14 (Ungu) dengan level 70 dan 30.
- **Judul Gambar:** Wajib menyertakan rezim pasar dan strategi yang aktif (Contoh: `XAUUSD M15 - [TRENDING: PULLBACK BUY]`).

#### 4.5. Modul Telegram & Eksekusi Semi-Otomatis (`telegram_bot.py` & `order_manager.py`)

- Kirim snapshot foto beserta detail: Strategi yang terpicu, Rezim Pasar, Entry, SL, TP, dan Rekomendasi Lot (Risiko 1% modal).
- Sediakan Inline Keyboard: `[🟢 Buka Posisi]` dan `[🔴 Abaikan]`.
- **Auto-Expiry 5 Menit:** Background thread menghapus tombol setelah 300 detik dan membatalkan toleransi eksekusi jika diklik terlambat.
- **Lifecycle Manager (Background Thread):**
  - R:R 1:1 tercapai $\rightarrow$ Partial close 50% lot + geser SL ke Break-Even (+ $0.20 buffer).
  - Melewati BE $\rightarrow$ Dynamic Trailing Stop aktif dengan jarak $2.00 dan step $0.50.

#### 4.6. Database & Rekapitulasi Harian (`db_logger.py`)

- SQLite database `trading_journal.db` mencatat deals tertutup (`DEAL_ENTRY_OUT`).
- Kolom tambahan: Catat nama strategi yang digunakan (`session_sweep` atau `trend_pullback`).
- Pukul 23:55 WIB: Render grafik kurva ekuitas dua panel (Equity & Drawdown) dan kirim rekap harian ke Telegram.

---

### 5. ATURAN IMPLEMENTASI UNTUK AI AGENT

1. **Pemisahan Logika:** Jangan campur kode deteksi tren H1 dengan logika eksekusi M15 di file yang sama. Gunakan struktur folder `strategies/`.
2. **Kemandirian Komputasi:** Seluruh penghitungan ADX, EMA, dan RSI wajib menggunakan _pure pandas_. Dilarang mengimpor pustaka TA-Lib eksternal.
3. **Traceability Log:** Terminal wajib mencetak rezim yang terdeteksi pada setiap pergantian candle M15 (contoh: `[REGIME] H1 ADX: 28.4 -> TRENDING. Memeriksa Trend Pullback...`).

# PRODUCT REQUIREMENTS DOCUMENT (PRD)

## Project: XAU/USD Semi-Automated Trading Assistant System

---

### 1. RINGKASAN PROYEK & TUJUAN

Membangun bot asisten trading semi-otomatis untuk instrumen XAU/USD (Gold vs USD) berbasis MetaTrader 5 Python API dan Telegram Bot. Sistem bertindak sebagai mesin validasi kuantitatif yang:

1. Memindai penutupan candle M15 untuk mendeteksi setup _Session Liquidity Sweep_.
2. Memvalidasi setup dengan filter _News Guard_ (Forex Factory) serta indikator _EMA 50 & RSI 14_.
3. Mengirimkan visualisasi grafik (_chart snapshot_) dengan tombol persetujuan interaktif ke Telegram.
4. Mengeksekusi order secara instan ke MT5 jika disetujui pengguna dalam kurun waktu 5 menit.
5. Mengelola siklus posisi aktif (Partial Take Profit 50% di 1:1 R:R, Auto Break-Even, dan Dynamic Trailing Stop).
6. Mencatat riwayat transaksi ke basis data SQLite dan mengirimkan laporan performa harian beserta kurva ekuitas (_Equity Curve_).

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

AI Agent wajib membagi kode ke dalam arsitektur modular berikut:

gold-trade-bot/
├── PRD.md # Dokumen spesifikasi utama (Ground Truth)
├── config.py # Konfigurasi kredensial, parameter trading, dan path
├── news_guard.py # Kalender ekonomi Forex Factory & verifikasi blackout
├── strategy.py # Kalkulasi EMA/RSI & evaluasi Session Liquidity Sweep
├── chart_engine.py # Render visualisasi grafik candlestick via mplfinance
├── telegram_bot.py # Pengirim foto, inline keyboard, listener, & expiry cleaner
├── order_manager.py # Eksekusi order MT5 & Lifecycle Manager (BE/Partial/Trailing)
├── db_logger.py # SQLite persistence & Equity Curve generator
└── main.py # Entry point & loop utama (M15 New Bar Detector)

---

### 4. SPESIFIKASI BISNIS & LOGIKA SISTEM

#### 4.1. Modul News Guard (`news_guard.py`)

- **Sumber Data:** Endpoint JSON Forex Factory: `https://nfs.faireconomy.media/ff_calendar_thisweek.json`.
- **Kriteria Filter:** Mata uang `USD` dengan `impact == "High"`.
- **Jendela Blackout:** ±30 menit dari waktu rilis berita (`NEWS_BUFFER_MINUTES = 30`).
- **Aturan Operasional:** Jika waktu saat ini berada di dalam jendela blackout, seluruh evaluasi sinyal dibatalkan seketika (_short-circuit evaluation_). Unduh ulang kalender sekali sehari pada 06:00 WIB.

#### 4.2. Modul Strategi & Indikator (`strategy.py`)

- **Timeframe Operasional:** M15 (Evaluasi hanya saat lilin index `[-2]` resmi ditutup).
- **Indikator (Pure Pandas):**
  - **EMA 50:** `df['close'].ewm(span=50, adjust=False).mean()`
  - **RSI 14:** Wilder's Smoothing RSI berbasis `alpha = 1/14`.
- **Sesi Waktu (WIB):**
  - **Asian Range:** 06:00 – 14:00 WIB. Tentukan `Asian High` dan `Asian Low`.
  - **Execution Window:** 14:00 – 23:00 WIB (Sesi London & New York Open).
- **Kondisi BUY Valid:**
  1. `Low[-2]` atau `Low[-3]` < `Asian Low` (Sweep likuiditas sisi bawah).
  2. `Close[-2] > Asian Low` (Rejection range).
  3. `Close[-2] > Open[-2]` (Lilin konfirmasi bullish).
  4. RSI sempat $\le 35$ dan `RSI[-2] > RSI[-3]` (Momentum memantul dari oversold).
  5. `Close[-2] > EMA50[-2]` (Harga berada di atas rata-rata tren).
  6. Stop Loss: `min(Low[-2], Low[-3]) - 1.00`. Take Profit: Minimal 1:2 R:R.
- **Kondisi SELL Valid:**
  1. `High[-2]` atau `High[-3]` > `Asian High` (Sweep likuiditas sisi atas).
  2. `Close[-2] < Asian High` (Rejection range).
  3. `Close[-2] < Open[-2]` (Lilin konfirmasi bearish).
  4. RSI sempat $\ge 65$ dan `RSI[-2] < RSI[-3]` (Momentum memantul dari overbought).
  5. `Close[-2] < EMA50[-2]` (Harga berada di bawah rata-rata tren).
  6. Stop Loss: `max(High[-2], High[-3]) + 1.00`. Take Profit: Minimal 1:2 R:R.

#### 4.3. Modul Visualisasi Grafik (`chart_engine.py`)

- **Library:** `mplfinance` (Gaya: `nightclouds`, gelap).
- **Layout Grafik:**
  - Panel 0 (Atas, 75% tinggi): Candlestick M15 (45 bar terakhir), garis overlay EMA 50 (Oranye), garis horizontal Entry (Biru), Stop Loss (Merah), dan Take Profit (Hijau).
  - Panel 1 (Bawah, 25% tinggi): Sub-panel RSI 14 (Ungu) dengan garis referensi level 70 dan 30 putus-putus.
- **Output:** Simpan ke file sementara `signal_chart.png` (DPI: 120), kirim ke Telegram, lalu hapus dari disk.

#### 4.4. Modul Telegram & Interaktivitas (`telegram_bot.py`)

- **Pesan Notifikasi:** Kirim foto via endpoint `sendPhoto` lengkap dengan detail: Direction, Entry, SL, TP, Rekomendasi Lot (1% risiko), status RSI, dan status EMA.
- **Inline Keyboard:** Dua tombol: `[🟢 Buka Posisi]` dan `[🔴 Abaikan]`.
- **Callback Data Convention:** Wajib format ringkas `<action>:<trade_id>` (contoh: `exec:tr_1728001`) untuk mencegah error batasan 64 byte Telegram.
- **Timeout / Expiry (300 Detik / 5 Menit):**
  - Background cleaner thread menghapus tombol inline dan mengubah teks menjadi `STATUS: KEDALUWARSA` jika tidak ada tindakan dalam 5 menit.
  - On-click guard membatalkan eksekusi jika pengguna menekan tombol saat usia sinyal sudah lewat dari 300 detik.

#### 4.5. Modul Eksekusi & Manajemen Posisi (`order_manager.py`)

- **Kalkulasi Lot Dinamis:** `Lot = (Balance * 0.01) / (|Entry - SL| * 100)`. Bulatkan ke 2 desimal, minimal `0.01 lot`.
- **Filling Mode Guard:** Periksa flag `symbol_info.filling_mode` secara dinamis (`IOC`, `FOK`, atau `RETURN`).
- **Slippage Guard:** Tetapkan deviasi maksimal 30 poin ($0.30) dan Magic Number `1002026`.
- **Lifecycle Manager (Background Thread setiap 2 detik):**
  - **Fase 1 (Target 1:1 R:R):**
    - BUY: Jika `Bid >= Open + Initial_Risk`.
    - SELL: Jika `Ask <= Open - Initial_Risk`.
    - Tindakan: Eksekusi parsial tutup 50% volume + geser SL ke `Open + $0.20` (BUY) atau `Open - $0.20` (SELL).
  - **Fase 2 (Dynamic Trailing Stop):**
    - Aktif hanya setelah posisi berada di Break-Even.
    - Kunci trailing dengan jarak $2.00 di belakang harga pasar terkini.
    - Hanya perbarui SL jika harga bergerak minimal sebesar step $0.50 (mencegah server spamming).

#### 4.6. Modul Database & Rekap Harian (`db_logger.py`)

- **Skema SQLite (`trading_journal.db`):**
  ```sql
  CREATE TABLE IF NOT EXISTS trade_history (
      deal_ticket INTEGER PRIMARY KEY,
      position_ticket INTEGER,
      symbol TEXT,
      trade_type TEXT,
      volume REAL,
      close_time TEXT,
      close_price REAL,
      profit REAL,
      commission REAL,
      swap REAL,
      net_profit REAL
  );
  ```

# PRODUCT REQUIREMENTS DOCUMENT (PRD) - V3.0
## Project: XAU/USD Adaptive Dual-Mode Trading Assistant System

---

### 1. RINGKASAN PROYEK & TUJUAN
Membangun bot asisten trading semi-otomatis untuk instrumen XAU/USD berbasis MetaTrader 5 Python API dan Telegram Bot dengan arsitektur **Dual-Mode Operasional**:
1. **Mode 1: Sniper / Precision Mode (Timeframe M15)**  
   Setup selektif berbasis *Session Liquidity Sweep* dan *HTF Trend Pullback*. Menahan posisi secara objektif mengikuti struktur swing tanpa batasan kuota kaku (timeout konfirmasi 300 detik).
2. **Mode 2: Intraday Disciplined Mode (Timeframe M5)**  
   Eksekusi cepat berbasis *Session VWAP* dan *Dynamic ATR Buffer*. Dirancang dengan rem psikologis harian: kuota maksimal 3 trade/hari, *Daily Kill Switch* 2%, proteksi latensi *Price Drift Guard* ($0.80), batas kedaluwarsa tombol ketat (90 detik), serta penutupan posisi otomatis sebelum tengah malam (*Overnight Auto-Flat* 23:00 WIB).

---

### 2. TECH STACK & DEPENDENCIES
- **Runtime & OS:** Python 3.10+ di Windows (Native MetaTrader 5 support).
- **Broker API:** Library resmi `MetaTrader5`.
- **Data & Analisis:** `pandas`, `numpy`.
- **Visualisasi:** `mplfinance`, `matplotlib`.
- **Jaringan & HTTP:** `requests`.
- **Database:** `sqlite3` (Bawaan Python).
- **Timezone:** `zoneinfo` (`Asia/Jakarta` / WIB).

---

### 3. STRUKTUR PROYEK MODULAR
AI Agent wajib memisahkan kode ke dalam modul terisolasi berikut:

gold-trade-bot/
├── PRD.md                       # Dokumen spesifikasi utama (Ground Truth)
├── config.py                    # Konstanta, parameter kedua mode, dan kredensial
├── state_manager.py             # Pengelola mode (SNIPER/INTRADAY) & rem darurat harian
├── news_guard.py                # Scraper Forex Factory & verifikasi blackout USD
├── chart_engine.py              # Visualisasi candlestick, VWAP, EMA, RSI via mplfinance
├── telegram_bot.py              # Dispatcher foto, inline keyboard, listener, & expiry cleaner
├── order_manager.py             # Eksekusi MT5, Drift Guard, Lifecycle (BE/Partial/Trailing)
├── db_logger.py                 # SQLite persistence & Equity Curve generator
├── strategies/
│   ├── __init__.py
│   ├── regime_classifier.py     # Deteksi tren H1 (ADX 14 & EMA 50/200)
│   ├── session_sweep.py         # Strategi M15 Session Sweep (Mode Sniper)
│   ├── trend_pullback.py        # Strategi M15 Trend Pullback (Mode Sniper)
│   └── intraday_vwap_m5.py      # Strategi M5 VWAP + ATR Rejection (Mode Intraday)
├── strategy_router.py           # Dispatcher strategi berdasarkan mode aktif & rezim
└── main.py                      # Multi-timeframe listener loop (M15 & M5 Bar Close)

---

### 4. SPESIFIKASI DUAL-MODE & PARAMETER OPERASIONAL

| Parameter | Mode 1: SNIPER (M15) | Mode 2: INTRADAY (M5) |
| :--- | :--- | :--- |
| **Timeframe Eksekusi** | M15 (Closed Bar `[-2]`) | M5 (Closed Bar `[-2]`) |
| **Konteks Timeframe Tinggi** | H1 (Regime ADX & EMA) | H1 (Tren Utama) + M15 (Area Nilai) |
| **Indikator Kunci** | EMA 50, RSI 14 | Session VWAP (Reset 06:00 WIB), ATR 14 M5 |
| **Metode Penentuan SL** | Swing Low/High M15 ± $1.00 Buffer | Swing Low/High M5 ± (1.2 × ATR M5) |
| **Batas Waktu Tombol (Timeout)**| 300 Detik (5 Menit) | **90 Detik (1.5 Menit)** |
| **Max Price Drift Guard** | $1.50 | **$0.80 (Toleransi pergeseran harga)** |
| **Batas Kuota Transaksi** | Tanpa batasan kuota harian | **Maksimal 3 Trade / Hari** |
| **Daily Kill Switch** | Nonaktif | **Aktif (Kunci sistem jika rugi harian $\ge$ 2%)** |
| **Batas Waktu Buka Posisi** | 14:00 – 23:00 WIB | 14:30 – 17:00 & 19:30 – 22:30 WIB |
| **Aturan Menginap (Overnight)** | Diizinkan berjalan normal | **Auto-Flat pukul 23:00 WIB (Wajib tutup)** |

---

### 5. DETAIL SPESIFIKASI MODUL

#### 5.1. Modul State Manager (`state_manager.py`)
- Menyimpan status mode aktif: `SNIPER` (default) atau `INTRADAY`.
- Menyediakan endpoint penggantian mode via Telegram command `/mode <sniper|intraday>`.
- **Validasi Khusus Mode INTRADAY:**
  1. Hitung total trade hari ini dari SQLite: Tolak sinyal jika `total_trades >= 3`.
  2. Hitung net profit/loss hari ini: Kunci sistem (*Kill Switch*) jika `net_pnl <= -(balance * 0.02)`.
  3. Tolak entri jika jam lokal $\ge$ 22:30 WIB.
- **Thread Auto-Flat (23:00 WIB):**
  - Jika mode adalah `INTRADAY`, tutup seluruh tiket posisi aktif ber-magic number bot di harga pasar dan kirim notifikasi penutupan ke Telegram.

#### 5.2. Modul News Guard (`news_guard.py`)
- Sumber data: Forex Factory JSON feed mingguan.
- Filter: Currency `USD` dengan `impact == "High"`.
- Jendela Blackout: ±30 menit dari rilis berita.
- Berlaku mutlak untuk kedua mode: jika blackout aktif, seluruh pembuatan sinyal dibatalkan (*short-circuit*).

#### 5.3. Logika Strategi Mode Intraday M5 (`strategies/intraday_vwap_m5.py`)
- **Session VWAP (Pure Pandas):**
  - Reset setiap pergantian hari pukul 06:00 WIB.
  - Formula: $\text{VWAP} = \frac{\sum (\text{Typical Price} \times \text{Volume})}{\sum \text{Volume}}$, di mana $\text{Typical Price} = \frac{\text{High} + \text{Low} + \text{Close}}{3}$.
- **ATR 14 (Timeframe M5):**
  - Dihitung menggunakan Wilder's smoothing untuk menentukan dinamika buffer SL.
- **Setup BUY M5 Valid:**
  1. Konteks H1: Tren tidak sedang dalam *Strong Downtrend*.
  2. Filter Nilai: Lilin penutupan M5 berada di atas VWAP (`Close[-2] > VWAP[-2]`).
  3. Pemicu: Jarum lilin (`Low[-2]` atau `Low[-3]`) sempat menguji (*retest*) area VWAP / Support M15, lalu lilin `[-2]` ditutup bullish (`Close[-2] > Open[-2]`).
  4. Stop Loss: $\text{Swing Low M5} - (1{,}2 \times \text{ATR}_{M5})$.
  5. Take Profit: Minimal rasio 1:2 R:R.
- **Setup SELL M5 Valid:**
  1. Konteks H1: Tren tidak sedang dalam *Strong Uptrend*.
  2. Filter Nilai: Lilin penutupan M5 berada di bawah VWAP (`Close[-2] < VWAP[-2]`).
  3. Pemicu: Jarum lilin (`High[-2]` atau `High[-3]`) sempat menguji area VWAP / Resistance M15, lalu lilin `[-2]` ditutup bearish (`Close[-2] < Open[-2]`).
  4. Stop Loss: $\text{Swing High M5} + (1{,}2 \times \text{ATR}_{M5})$.
  5. Take Profit: Minimal rasio 1:2 R:R.

#### 5.4. Modul Telegram, Latensi & Drift Guard (`telegram_bot.py` & `order_manager.py`)
- **Pesan Notifikasi:**
  - Header wajib menampilkan mode: `[MODE: INTRADAY M5]` atau `[MODE: SNIPER M15]`.
  - Sertakan detail harga: Entry, SL, TP, Rekomendasi Lot (1% modal), Nilai VWAP, dan Nilai ATR.
- **Tombol Persetujuan:** `[🟢 Buka Posisi]` dan `[🔴 Abaikan]`.
- **Dynamic Expiry Cleaner:**
  - Jika sinyal berasal dari Mode INTRADAY $\rightarrow$ Tombol kedaluwarsa dalam **90 detik**.
  - Jika sinyal berasal dari Mode SNIPER $\rightarrow$ Tombol kedaluwarsa dalam **300 detik**.
- **Price Drift Guard (Proteksi Eksekusi Riil):**
  - Saat tombol `[🟢 Buka Posisi]` ditekan, bot membaca harga tick terkini (`tick.ask` untuk BUY, `tick.bid` untuk SELL).
  - Untuk Mode INTRADAY: Jika $\vert{}\text{Harga Tick} - \text{Harga Entry Sinyal}\vert{} > \$0{,}80$, **batalkan eksekusi order** dan kirim notifikasi:  
    `"❌ Order Dibatalkan: Harga telah bergeser > $0.80 dari kalkulasi awal!"`

#### 5.5. Manajemen Posisi Aktif (`order_manager.py`)
- Berjalan di background thread setiap 2 detik untuk seluruh posisi aktif:
  1. **Target 1:1 R:R Tercapai:**
     - Eksekusi penutupan parsial 50% volume (*Partial TP*).
     - Geser Stop Loss ke $\text{Entry} + \$0{,}20$ (BUY) atau $\text{Entry} - \$0{,}20$ (SELL) untuk mengunci *Break-Even*.
  2. **Dynamic Trailing Stop:**
     - Aktif setelah posisi berada di Break-Even.
     - Jarak trailing: $2.00 di belakang harga pasar (Mode Sniper) atau $1.50 (Mode Intraday).
     - Step modifikasi minimal: $0.50 (anti-spam server broker).

#### 5.6. Database & Rekapitulasi Harian (`db_logger.py`)
- Skema SQLite `trading_journal.db`:
  - Catat setiap deal keluar (`DEAL_ENTRY_OUT`) dengan kolom: `deal_ticket`, `position_ticket`, `symbol`, `mode_used`, `trade_type`, `volume`, `close_time`, `net_profit`.
- **Laporan Harian (23:55 WIB):**
  - Ringkasan statistik performa dipisahkan berdasarkan mode (`SNIPER` vs `INTRADAY`).
  - Render grafik kurva ekuitas (*Equity Curve & Drawdown*) dua panel berlatar gelap via Matplotlib.
  - Kirim foto kurva ekuitas beserta rincian teks ke Telegram.

---

### 6. ATURAN PENERAPAN UNTUK AI CODING AGENT
1. **Multi-Timeframe Polling:** Loop `main.py` harus memantau penutupan lilin M15 (untuk Mode Sniper) dan penutupan lilin M5 (untuk Mode Intraday) tanpa saling memblokir.
2. **Kemandirian Komputasi:** Kalkulasi VWAP, ATR, ADX, EMA, dan RSI wajib menggunakan *pure pandas/numpy*. Dilarang menggunakan pustaka eksternal berbasis C seperti TA-Lib.
3. **Pemberian Identitas Unik Callback:** Callback tombol Telegram wajib berformat `<action>:<trade_id>` untuk mematuhi batas 64 byte payload Telegram.
4. **Isolasi Kegagalan:** Kesalahan jaringan pada scraper berita atau Telegram API tidak boleh menghentikan loop manajemen posisi aktif di MT5.
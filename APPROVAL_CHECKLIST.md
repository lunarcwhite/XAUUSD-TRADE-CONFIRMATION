# APPROVAL CHECKLIST — Sinyal Bot XAU/USD

> Dibaca sebelum menekan tombol Telegram. Satu sinyal = satu baris alasan.

## 1. Arti APPROVED

Sinyal dieksekusi hanya bila: **cocok protokol**, ATAU di luar protokol tapi
alasan objektifnya tertulis satu baris. Tidak ada approve "karena kelihatannya bagus".

## 2. Fakta bot per sinyal (dari caption Telegram)

- [ ] Nama strategi: `session_sweep / trend_pullback / breakout / post_news`
- [ ] Rezim: `RANGING / TRENDING / POST_NEWS` — masuk akal dengan kondisiku sendiri?
- [ ] Entry/SL/TP + rasio: hitung ulang TP:SL ≈ 1:2
      (post-news: SL lebih lebar, lot 0.5x — cek risiko% caption sudah setengah)
- [ ] Lot + risiko%: masih ≤ toleransiku hari ini?
- [ ] Chart: harga vs garis Entry/SL/TP + EMA — tidak ada anomali
      (gap aneh, spread melebar, bar tidak wajar)
- [ ] Spot-check bulanan: hitung manual sesekali — lot ≈ (balance × risiko%)
      ÷ (|Entry − SL| × 100). Aritmetika bot dipercaya harian, diverifikasi bulanan.

## 3. Cek khusus bot

- [ ] Bukan dalam blackout news ±30 mnt
      (bot memblokir otomatis, tapi verifikasi manual untuk news dadakan)
- [ ] Governor hijau: tanpa posisi terbuka, tidak dalam cooldown 60 mnt,
      daily-loss 3% belum kena
- [ ] Umur sinyal < 5 menit — lewat dari itu: WAIT (biarkan kedaluwarsa),
      jangan approve

## 4. Lifecycle vs protokol exit

**Aturan yang menang: bot** (partial 50% di 1:1 → BE +$0.20 → trailing
$2.00/step $0.50). Alasan: otomatis, konsisten, terukur.
Protokol manual (50–70% di 1:2) hanya jadi override eksepsi — wajib tulis
alasan satu baris bila intervensi manual.

## 5. Red flags bias baru

- [ ] Approve tanpa baca caption (buta-percaya-bot)
- [ ] Approve karena bosan menunggu / ingin aksi
- [ ] Approve balas dendam setelah loss (cek daily-loss dulu)
- [ ] FOMO pada sinyal `post_news` — spike = volatilitas, bukan kepastian

## 6. Keputusan final

Format: `TRADE` (tap approve) / `WAIT` (biarkan expire) / `SKIP` (tap ignore)
— karena: ... (satu baris alasan, format sama setiap kali)

"""Enkripsi at-rest untuk secret broker di SQLite (review C2).

Skema: Fernet(AES-128-CBC+HMAC) dengan kunci stabil yang diturunkan dari
TELEGRAM_BOT_TOKEN via SHA-256 — tanpa config baru, stabil lintas restart,
dan DB curian tanpa .env tak bisa dipakai langsung. Format: "enc:v1:<ct>".
Baris legacy plaintext tetap terbaca (fallback), dienkripsi ulang saat save.

Bukan HSM: melindungi file-di-curi, bukan memori-di-baca. Bila token bot
belum diset -> fallback plaintext + peringatan sekali (fail-open eksplisit).
"""
from __future__ import annotations

import base64
import hashlib

PREFIX = "enc:v1:"
_warned_once = False


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except Exception:
        return None
    try:
        from config import TELEGRAM_BOT_TOKEN
    except Exception:
        return None
    tok = (TELEGRAM_BOT_TOKEN or "").strip()
    if not tok or tok.startswith("isi_"):
        return None
    key = base64.urlsafe_b64encode(
        hashlib.sha256(b"cred-v1:" + tok.encode("utf-8")).digest())
    try:
        return Fernet(key)
    except Exception:
        return None


def _warn_plain():
    global _warned_once
    if not _warned_once:
        _warned_once = True
        print("[WARN] cred at-rest PLAINTEXT (TELEGRAM_BOT_TOKEN kosong/invalid).")


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt_value(value):
    """Enkripsi string secret. Non-string/kosong/sudah-enc -> apa adanya."""
    if not isinstance(value, str) or not value or is_encrypted(value):
        return value
    f = _fernet()
    if f is None:
        _warn_plain()
        return value
    try:
        return PREFIX + f.encrypt(value.encode("utf-8")).decode("ascii")
    except Exception:
        return value


def decrypt_value(value):
    """Kembalikan plaintext. Legacy plaintext -> apa adanya."""
    if not isinstance(value, str) or not value or not is_encrypted(value):
        return value or ""
    f = _fernet()
    if f is None:
        _warn_plain()
        return ""
    try:
        return f.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        print("[WARN] cred gagal di-decrypt (token bot berubah?).")
        return ""

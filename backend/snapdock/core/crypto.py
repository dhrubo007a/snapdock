"""AES-256-GCM file encryption / decryption.

Each encrypted file has the layout:
  [8-byte file nonce][N × (4-byte chunk length)(12-byte chunk nonce)(ciphertext+tag)]

The per-chunk nonce is: file_nonce (8 bytes) || chunk_index (4 bytes, big-endian).
Chunk index is also used as Additional Authenticated Data (AAD) to prevent
chunk reordering attacks.

The key is the daemon's 32-byte AES-256 key from config.  Because the file
nonce is random per call, the same key may be used for every file safely.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CHUNK_SIZE = 64 * 1024  # 64 KiB — works for both small config files and large volume archives
_ENC_SUFFIX = ".enc"


# --------------------------------------------------------------------------- #
# Public API                                                                    #
# --------------------------------------------------------------------------- #

def encrypt_file(src: Path, key: bytes) -> Path:
    """Encrypt *src* in-place (src is deleted, src.enc is written).

    Returns the path of the encrypted file.
    Raises ``ValueError`` if the key is not 32 bytes.
    """
    _check_key(key)
    dst = src.with_suffix(src.suffix + _ENC_SUFFIX)
    file_nonce = os.urandom(8)
    aesgcm = AESGCM(key)

    with src.open("rb") as fin, dst.open("wb") as fout:
        fout.write(file_nonce)
        chunk_index = 0
        while True:
            chunk = fin.read(CHUNK_SIZE)
            if not chunk:
                break
            nonce = file_nonce + chunk_index.to_bytes(4, "big")
            aad = chunk_index.to_bytes(8, "big")
            ciphertext = aesgcm.encrypt(nonce, chunk, aad)
            fout.write(len(ciphertext).to_bytes(4, "big"))
            fout.write(ciphertext)
            chunk_index += 1

    src.unlink()
    return dst


def decrypt_file(src: Path, key: bytes) -> Path:
    """Decrypt *src* (a .enc file) in-place (src is deleted, plaintext written).

    Returns the path of the decrypted file.
    """
    _check_key(key)
    if not src.name.endswith(_ENC_SUFFIX):
        raise ValueError(f"Expected .enc file, got {src}")
    dst = src.with_suffix("")  # strip .enc
    aesgcm = AESGCM(key)

    with src.open("rb") as fin, dst.open("wb") as fout:
        file_nonce = fin.read(8)
        if len(file_nonce) != 8:
            raise ValueError("Truncated encrypted file: missing file nonce")
        chunk_index = 0
        while True:
            length_bytes = fin.read(4)
            if not length_bytes:
                break
            if len(length_bytes) < 4:
                raise ValueError("Truncated encrypted file: incomplete chunk length")
            chunk_len = int.from_bytes(length_bytes, "big")
            ciphertext = fin.read(chunk_len)
            if len(ciphertext) != chunk_len:
                raise ValueError("Truncated encrypted file: incomplete ciphertext")
            nonce = file_nonce + chunk_index.to_bytes(4, "big")
            aad = chunk_index.to_bytes(8, "big")
            plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
            fout.write(plaintext)
            chunk_index += 1

    src.unlink()
    return dst


def checksum_file(path: Path) -> str:
    """Return a SHA-256 hex digest of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def encrypt_directory(directory: Path, key: bytes) -> None:
    """Recursively encrypt all non-.enc files in *directory*."""
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.name.endswith(_ENC_SUFFIX):
            encrypt_file(path, key)


def decrypt_directory(directory: Path, key: bytes) -> None:
    """Recursively decrypt all .enc files in *directory*."""
    for path in sorted(directory.rglob("*.enc")):
        if path.is_file():
            decrypt_file(path, key)


# --------------------------------------------------------------------------- #
# Internals                                                                     #
# --------------------------------------------------------------------------- #

def _check_key(key: bytes) -> None:
    if len(key) != 32:
        raise ValueError(f"AES-256 key must be 32 bytes, got {len(key)}")

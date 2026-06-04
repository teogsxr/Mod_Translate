"""ChaCha20 encryption/decryption with deterministic key derivation.

Keys are derived from filenames alone using Bob Jenkins' lookup3 hash.
No key database needed - the filename IS the key source.

The encryption is symmetric: encrypt(data) == decrypt(data) with the same key.
"""

import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

HASH_INITVAL = 0x000C5EDE
IV_XOR = 0x60616263
XOR_DELTAS = [
    0x00000000, 0x0A0A0A0A, 0x0C0C0C0C, 0x06060606,
    0x0E0E0E0E, 0x0A0A0A0A, 0x06060606, 0x02020202,
]
MASK32 = 0xFFFFFFFF


def _rot(v: int, k: int) -> int:
    return ((v << k) | (v >> (32 - k))) & MASK32


def _add(a: int, b: int) -> int:
    return (a + b) & MASK32


def _sub(a: int, b: int) -> int:
    return (a - b) & MASK32


def hashlittle(data: bytes, initval: int = 0) -> int:
    """Bob Jenkins' lookup3 hashlittle - returns the primary hash (c).

    This is the standard lookup3 hash used for key derivation,
    distinct from PaChecksum which is a modified variant.
    """
    length = len(data)
    a = b = c = _add(0xDEADBEEF + length, initval)
    off = 0

    while length > 12:
        a = _add(a, struct.unpack_from("<I", data, off)[0])
        b = _add(b, struct.unpack_from("<I", data, off + 4)[0])
        c = _add(c, struct.unpack_from("<I", data, off + 8)[0])
        a = _sub(a, c); a ^= _rot(c, 4);  c = _add(c, b)
        b = _sub(b, a); b ^= _rot(a, 6);  a = _add(a, c)
        c = _sub(c, b); c ^= _rot(b, 8);  b = _add(b, a)
        a = _sub(a, c); a ^= _rot(c, 16); c = _add(c, b)
        b = _sub(b, a); b ^= _rot(a, 19); a = _add(a, c)
        c = _sub(c, b); c ^= _rot(b, 4);  b = _add(b, a)
        off += 12
        length -= 12

    tail = data[off:] + b"\x00" * 12
    if length >= 12:
        c = _add(c, struct.unpack_from("<I", tail, 8)[0])
    elif length >= 9:
        v = struct.unpack_from("<I", tail, 8)[0]
        c = _add(c, v & (MASK32 >> (8 * (12 - length))))
    if length >= 8:
        b = _add(b, struct.unpack_from("<I", tail, 4)[0])
    elif length >= 5:
        v = struct.unpack_from("<I", tail, 4)[0]
        b = _add(b, v & (MASK32 >> (8 * (8 - length))))
    if length >= 4:
        a = _add(a, struct.unpack_from("<I", tail, 0)[0])
    elif length >= 1:
        v = struct.unpack_from("<I", tail, 0)[0]
        a = _add(a, v & (MASK32 >> (8 * (4 - length))))
    elif length == 0:
        return c

    c ^= b; c = _sub(c, _rot(b, 14))
    a ^= c; a = _sub(a, _rot(c, 11))
    b ^= a; b = _sub(b, _rot(a, 25))
    c ^= b; c = _sub(c, _rot(b, 16))
    a ^= c; a = _sub(a, _rot(c, 4))
    b ^= a; b = _sub(b, _rot(a, 14))
    c ^= b; c = _sub(c, _rot(b, 24))
    return c


def derive_key_iv(filename: str) -> tuple[bytes, bytes]:
    """Derive 32-byte ChaCha20 key and 16-byte IV from a filename.

    Uses the lowercase basename only (directory prefix is stripped).

    Args:
        filename: Original filename (with or without path).

    Returns:
        (key, iv) as bytes - 32 bytes key, 16 bytes IV.
    """
    basename = os.path.basename(filename).lower()
    seed = hashlittle(basename.encode("utf-8"), HASH_INITVAL)

    iv = struct.pack("<I", seed) * 4
    key_base = seed ^ IV_XOR
    key = b"".join(struct.pack("<I", key_base ^ d) for d in XOR_DELTAS)
    return key, iv


def chacha20_crypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """ChaCha20 encrypt or decrypt (symmetric - same operation both ways).

    Args:
        data: Input data (plaintext or ciphertext).
        key: 32-byte ChaCha20 key.
        iv: 16-byte IV (nonce).

    Returns:
        Processed data.
    """
    cipher = Cipher(algorithms.ChaCha20(key, iv), mode=None)
    return cipher.encryptor().update(data)


def decrypt(data: bytes, filename: str) -> bytes:
    """Decrypt data using a key derived from the filename.

    Args:
        data: Encrypted data.
        filename: Original filename for key derivation.

    Returns:
        Decrypted data.
    """
    key, iv = derive_key_iv(filename)
    return chacha20_crypt(data, key, iv)


def encrypt(data: bytes, filename: str) -> bytes:
    """Encrypt data using a key derived from the filename.

    ChaCha20 is symmetric, so encrypt == decrypt.

    Args:
        data: Plaintext data.
        filename: Original filename for key derivation.

    Returns:
        Encrypted data.
    """
    return decrypt(data, filename)

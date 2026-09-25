"""
Binary container for a fixed-size batch of JPEG frames.

Layout (little-endian):

    4 bytes   magic  b"ISLF"
    2 bytes   frame count N
    4*N bytes length of each JPEG
    ...       the N JPEG blobs, back to back

No base64, no JSON, no zip. The JPEGs are already compressed, so the
container adds only 6 + 4*N bytes.

Keep this file identical in the backend and the model API.
"""

import struct

MAGIC = b"ISLF"
_HEADER = struct.Struct("<4sH")  # magic, frame count


def pack_frames(jpegs: list) -> bytes:
    if not jpegs:
        raise ValueError("No frames to pack")

    lengths = struct.pack(f"<{len(jpegs)}I", *map(len, jpegs))

    return b"".join([_HEADER.pack(MAGIC, len(jpegs)), lengths, *jpegs])


def unpack_frames(
    body: bytes,
    expected: int = 16,
    max_frame_bytes: int = 500_000,
) -> list:
    if len(body) < _HEADER.size:
        raise ValueError("Payload too short")

    magic, count = _HEADER.unpack_from(body, 0)

    if magic != MAGIC:
        raise ValueError("Bad payload header")

    if count != expected:
        raise ValueError(f"Expected {expected} frames, got {count}")

    table_end = _HEADER.size + 4 * count

    if len(body) < table_end:
        raise ValueError("Truncated length table")

    lengths = struct.unpack_from(f"<{count}I", body, _HEADER.size)

    if any(n == 0 or n > max_frame_bytes for n in lengths):
        raise ValueError("Invalid frame length")

    if sum(lengths) != len(body) - table_end:
        raise ValueError("Payload length mismatch")

    frames, offset = [], table_end

    for n in lengths:
        frames.append(bytes(body[offset:offset + n]))
        offset += n

    return frames
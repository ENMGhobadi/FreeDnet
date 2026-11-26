#!/usr/bin/env python3
"""Generates a modern Windows 11 multi-resolution icon (freednet.ico) using only Python stdlib."""
import os
import struct
import zlib


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _render_png(size: int) -> bytes:
    rows = []
    cx = (size - 1) / 2.0
    cy = (size - 1) / 2.0
    r_outer = size * 0.46
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dx = abs(x - cx)
            dy = abs(y - cy)
            # Rounded square mask (squircle-like)
            corner = size * 0.24
            rx = max(0.0, dx - (r_outer - corner))
            ry = max(0.0, dy - (r_outer - corner))
            dist = (rx * rx + ry * ry) ** 0.5
            if dist <= corner:
                # Gradient from deep indigo (#1d4ed8) to cyan (#06b6d4)
                t = (x + y) / (2.0 * size)
                r = int(29 * (1 - t) + 6 * t)
                g = int(78 * (1 - t) + 182 * t)
                b = int(216 * (1 - t) + 212 * t)
                a = 255

                # White stylized "F" / lightning bolt emblem in center
                nx = (x - cx) / size
                ny = (y - cy) / size
                in_stem = -0.18 <= nx <= -0.05 and -0.25 <= ny <= 0.25
                in_top = -0.18 <= nx <= 0.20 and -0.25 <= ny <= -0.13
                in_mid = -0.18 <= nx <= 0.13 and -0.04 <= ny <= 0.07
                if in_stem or in_top or in_mid:
                    r, g, b = 255, 255, 255

                row.extend((r, g, b, a))
            else:
                row.extend((0, 0, 0, 0))
        rows.append(bytes(row))

    raw = b"".join(rows)
    ihdr = struct.pack("!IIBBBBB", size, size, 8, 6, 0, 0, 0)
    idat = zlib.compress(raw, 9)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def build_ico(out_path: str):
    sizes = [32, 64, 128, 256]
    pngs = [_render_png(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = []
    for s, data in zip(sizes, pngs):
        w = 0 if s >= 256 else s
        entries.append(struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    with open(out_path, "wb") as f:
        f.write(header + b"".join(entries) + b"".join(pngs))


if __name__ == "__main__":
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "freednet.ico")
    build_ico(target)
    print(f"Generated {target}")

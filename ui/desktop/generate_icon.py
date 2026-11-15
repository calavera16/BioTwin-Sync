"""Reproducible stdlib rendering of biotwin.svg into Windows PNG-frame ICO.

Build-only code-native icon; no fonts, image models, or extra dependencies.
"""
from pathlib import Path
import struct
import zlib

POINTS = ((20, 68), (43, 68), (55, 40), (74, 86), (86, 58), (96, 68), (108, 68))


def distance2(x, y, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    t = max(0, min(1, ((x - a[0]) * vx + (y - a[1]) * vy) / (vx * vx + vy * vy)))
    return (x - a[0] - t * vx) ** 2 + (y - a[1] - t * vy) ** 2


def frame(size):
    rows = bytearray()
    bg, fg = (11, 18, 33), (105, 220, 179)
    for y in range(size):
        rows.append(0)
        for x in range(size):
            hit = 0
            for sy in range(4):
                for sx in range(4):
                    px, py = (x + (sx + .5) / 4) * 128 / size, (y + (sy + .5) / 4) * 128 / size
                    hit += any(distance2(px, py, a, b) <= 16 for a, b in zip(POINTS, POINTS[1:]))
            rows.extend(round(a + (b - a) * hit / 16) for a, b in zip(bg, fg))
            rows.append(255)
    def chunk(kind, raw):
        return struct.pack('>I', len(raw)) + kind + raw + struct.pack('>I', zlib.crc32(kind + raw))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))


def icon_bytes():
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [frame(size) for size in sizes]
    offset = 6 + 16 * len(sizes)
    records = bytearray(struct.pack('<HHH', 0, 1, len(sizes)))
    for size, data in zip(sizes, images):
        records.extend(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    return bytes(records) + b''.join(images)


if __name__ == '__main__':
    target = Path(__file__).with_name('biotwin.ico')
    raw = icon_bytes()
    if not target.exists() or target.read_bytes() != raw:
        target.write_bytes(raw)
    print(f'{target.name}: {len(raw)} bytes')

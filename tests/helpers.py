"""测试辅助：按 README 第 3 节布局现场构造归档。"""

from __future__ import annotations

import struct
import zlib

SEGMENT = 65536


def build_archive(entries: list[tuple[str, bytes]], *,
                  declared_length: int | None = None,
                  table_offset: int = 32,
                  truncate: int | None = None,
                  magic: bytes = b"ARCK",
                  version: int = 1) -> bytes:
    """从 (名字, 原始内容) 列表构造归档字节，可注入坏包。"""
    table = bytearray()
    for name, data in entries:
        name_bytes = name.encode("utf-8")
        table += struct.pack("<H", len(name_bytes))
        table += name_bytes
        table += struct.pack("<QI", len(data), zlib.crc32(data))
    body = bytearray()
    for _name, data in entries:
        for pos in range(0, len(data), SEGMENT):
            comp = zlib.compress(data[pos:pos + SEGMENT])
            body += struct.pack("<I", len(comp))
            body += comp
    blob = bytearray(b"\x00" * table_offset)
    blob += table
    blob += body
    length = len(blob) if declared_length is None else declared_length
    blob[0:32] = struct.pack("<4sHHIQIQ", magic, version, 0, len(entries),
                             table_offset, len(table), length)
    if truncate is not None:
        blob = blob[:truncate]
    return bytes(blob)

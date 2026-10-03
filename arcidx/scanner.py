"""归档结构扫描：头部、条目表、数据区段头。

解析顺序（README 第 3 节）：读头部 → 校验魔数/版本/L → 校验条目表区间 →
逐条解析记录 → 从数据区起点顺序扫段头。建索引与 check 共用这一套逻辑，
扫描过程只读头部、条目表与每段 4 字节段头，不读段体。
"""

import struct

from . import format as fmt
from .errors import FormatError

HEADER_STRUCT = struct.Struct("<4sHHIQIQ")
NAME_LEN_STRUCT = struct.Struct("<H")
RECORD_TAIL_STRUCT = struct.Struct("<QI")
SEGMENT_HEADER_STRUCT = struct.Struct("<I")


class Entry:
    """条目表记录 + 扫描得到的数据位置。"""

    __slots__ = ("name", "data_offset", "span", "raw_size", "crc32", "segments")

    def __init__(self, name, data_offset, span, raw_size, crc32, segments):
        self.name = name
        self.data_offset = data_offset
        self.span = span
        self.raw_size = raw_size
        self.crc32 = crc32
        self.segments = segments

    def __eq__(self, other):
        return isinstance(other, Entry) and all(
            getattr(self, field) == getattr(other, field) for field in self.__slots__
        )

    def __repr__(self):
        return (
            f"Entry(name={self.name!r}, data_offset={self.data_offset}, "
            f"span={self.span}, raw_size={self.raw_size}, "
            f"crc32={self.crc32:08x}, segments={self.segments})"
        )


class ScanResult:
    __slots__ = ("entries", "segment_total", "table_offset", "table_size",
                 "total_length", "bytes_read")

    def __init__(self, entries, segment_total, table_offset, table_size,
                 total_length, bytes_read):
        self.entries = entries
        self.segment_total = segment_total
        self.table_offset = table_offset
        self.table_size = table_size
        self.total_length = total_length
        self.bytes_read = bytes_read


def _table_range_error(table_offset, table_size, total_length):
    return FormatError(
        "TABLE_RANGE", None, table_offset, f"size={table_size} limit={total_length}"
    )


def parse_header(head):
    """解析 32 字节头部；坏头抛 FormatError(LENGTH_MISMATCH)。"""
    if len(head) < fmt.HEADER_SIZE:
        raise FormatError("LENGTH_MISMATCH", None, None, f"actual={len(head)}")
    magic, version, _reserved, _n, _t, _s, total_length = HEADER_STRUCT.unpack(head)
    if magic != fmt.MAGIC:
        raise FormatError("LENGTH_MISMATCH", None, None, f"magic={magic.hex() or '??'}")
    if version != fmt.VERSION:
        raise FormatError("LENGTH_MISMATCH", None, None, f"version={version}")
    return HEADER_STRUCT.unpack(head)


def parse_table(table, entry_count, table_offset, table_size, total_length):
    """把条目表字节解析成 (名字, 原始大小, crc32) 列表。"""
    records = []
    pos = 0
    for _ in range(entry_count):
        if pos + 2 > len(table):
            raise _table_range_error(table_offset, table_size, total_length)
        (name_len,) = NAME_LEN_STRUCT.unpack_from(table, pos)
        end = pos + 2 + name_len + RECORD_TAIL_STRUCT.size
        if name_len < 1 or name_len > fmt.MAX_NAME_BYTES or end > len(table):
            raise _table_range_error(table_offset, table_size, total_length)
        name_bytes = table[pos + 2 : pos + 2 + name_len]
        try:
            name = name_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise _table_range_error(table_offset, table_size, total_length)
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name):
            raise _table_range_error(table_offset, table_size, total_length)
        raw_size, crc32 = RECORD_TAIL_STRUCT.unpack_from(table, pos + 2 + name_len)
        records.append((name, raw_size, crc32))
        pos = end
    return records


def scan(reader, actual_length):
    """按解析顺序扫一遍归档，返回 ScanResult；坏包抛 FormatError。

    reader 是 CountingReader；读入字节数随之累计（头部 + 条目表 + 段头）。
    """
    reader.seek(0)
    head = reader.read_exact(fmt.HEADER_SIZE)
    _magic, _version, _reserved, entry_count, table_offset, table_size, total_length = (
        parse_header(head)
    )
    if total_length != actual_length:
        raise FormatError(
            "LENGTH_MISMATCH", None, total_length, f"actual={actual_length}"
        )
    if table_offset + table_size > total_length:
        raise _table_range_error(table_offset, table_size, total_length)
    reader.seek(table_offset)
    table = reader.read_exact(table_size)
    if len(table) < table_size:
        raise _table_range_error(table_offset, table_size, total_length)
    records = parse_table(table, entry_count, table_offset, table_size, total_length)

    entries = []
    segment_total = 0
    pos = table_offset + table_size
    for name, raw_size, crc32 in records:
        count = fmt.segment_count(raw_size)
        data_offset = pos
        for seg in range(count):
            have = total_length - pos
            if have < fmt.SEGMENT_HEADER_SIZE:
                raise FormatError(
                    "STREAM_TRUNCATED", name, pos,
                    f"segment={seg} need=4 have={max(have, 0)}",
                )
            reader.seek(pos)
            seg_head = reader.read_exact(fmt.SEGMENT_HEADER_SIZE)
            if len(seg_head) < fmt.SEGMENT_HEADER_SIZE:
                raise FormatError(
                    "STREAM_TRUNCATED", name, pos,
                    f"segment={seg} need=4 have={len(seg_head)}",
                )
            (compressed_len,) = SEGMENT_HEADER_STRUCT.unpack(seg_head)
            have_body = have - fmt.SEGMENT_HEADER_SIZE
            if compressed_len > have_body:
                raise FormatError(
                    "STREAM_TRUNCATED", name, pos,
                    f"segment={seg} need={compressed_len} have={have_body}",
                )
            pos += fmt.SEGMENT_HEADER_SIZE + compressed_len
        entries.append(
            Entry(name, data_offset, pos - data_offset, raw_size, crc32, count)
        )
        segment_total += count
    return ScanResult(
        entries, segment_total, table_offset, table_size, total_length,
        reader.bytes_read,
    )

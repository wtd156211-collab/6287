"""arcidx: 只读归档的索引与随机读取库。

公共接口::

    index = arcidx.build_index(archive_path)
    arcidx.write_index(index, index_path)
    index = arcidx.load_index(index_path)
    arc = arcidx.open_archive(archive_path, index)
    arc.entries()
    arc.read(name)
    arc.last_read

坏包用 :class:`ArchiveError` 表达（含 code/entry/offset/detail）。
"""

from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass

MAGIC = b"ARCK"
INDEX_MAGIC = b"ARCI"
VERSION = 1
HEADER_SIZE = 32
SEGMENT_SIZE = 65536


class ArchiveError(Exception):
    """坏包错误。``row`` 是 check 输出的四列 TSV。"""

    def __init__(self, code: str, entry: str = "-", offset: str = "-",
                 detail: str = ""):
        self.code = code
        self.entry = entry
        self.offset = offset
        self.detail = detail
        self.row = (code, entry, str(offset), detail)
        super().__init__("\t".join(self.row))


class UsageError(Exception):
    """用法错误：参数缺失、索引缺失或与归档不配套。"""


@dataclass(frozen=True)
class Entry:
    """条目表中的一条记录（字段同 ``list`` 输出）。"""

    index: int
    name: str
    offset: int
    span: int
    size: int
    crc32: int
    segments: int

    @property
    def crc32_hex(self) -> str:
        return "%08x" % self.crc32

    def as_row(self) -> tuple[str, str, str, str, str, str, str]:
        return (
            str(self.index),
            self.name,
            str(self.offset),
            str(self.span),
            str(self.size),
            self.crc32_hex,
            str(self.segments),
        )


@dataclass(frozen=True)
class ReadStat:
    """最近一次随机读的统计，数值全部来自引擎。"""

    name: str
    offset: int
    span: int
    size: int
    bytes_read: int
    elapsed_ms: float


@dataclass(frozen=True)
class Index:
    """可复用的归档索引。"""

    length: int
    table_offset: int
    table_size: int
    entry_count: int
    segment_count: int
    entries: tuple[Entry, ...]
    bytes_read: int
    elapsed_ms: float

    @property
    def by_name(self) -> dict[str, Entry]:
        return {entry.name: entry for entry in self.entries}


class _CountingReader:
    """只统计从归档文件对象实际取回的字节（seek 不计）。"""

    def __init__(self, fp):
        self._fp = fp
        self.bytes_read = 0

    def read(self, count: int) -> bytes:
        data = self._fp.read(count)
        self.bytes_read += len(data)
        return data

    def seek(self, pos: int) -> None:
        self._fp.seek(pos)

    def skip(self, count: int) -> None:
        self._fp.seek(count, os.SEEK_CUR)


def _u16(data: bytes, pos: int) -> int:
    return struct.unpack_from("<H", data, pos)[0]


def _u32(data: bytes, pos: int) -> int:
    return struct.unpack_from("<I", data, pos)[0]


def _parse_table(table: bytes, count: int, data_start: int,
                 reader: _CountingReader) -> tuple[list[Entry], int]:
    """解析条目表并顺序扫段头，返回 (条目列表, 段总数)。"""
    pos = 0
    records: list[tuple[str, int, int]] = []
    for _ in range(count):
        if pos + 2 > len(table):
            raise ArchiveError("TABLE_RANGE", "-", "-",
                               "record header exceeds table")
        name_len = _u16(table, pos)
        pos += 2
        if pos + name_len + 12 > len(table):
            raise ArchiveError("TABLE_RANGE", "-", "-",
                               "record body exceeds table")
        try:
            name = table[pos:pos + name_len].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArchiveError("TABLE_RANGE", "-", "-",
                               "bad utf-8 name: %s" % exc)
        pos += name_len
        size, crc = struct.unpack_from("<QI", table, pos)
        pos += 12
        records.append((name, size, crc))
    if pos != len(table):
        raise ArchiveError("TABLE_RANGE", "-", "-",
                           "table has %d trailing bytes" % (len(table) - pos))

    entries: list[Entry] = []
    segment_total = 0
    cur = data_start
    for seq, (name, size, crc) in enumerate(records):
        offset = cur
        segment_count = 0 if size == 0 else (size + SEGMENT_SIZE - 1) // SEGMENT_SIZE
        span = 0
        for seg in range(segment_count):
            header = reader.read(4)
            if len(header) < 4:
                raise ArchiveError(
                    "STREAM_TRUNCATED", name, cur,
                    "segment=%d need=4 have=%d"
                    % (seg, len(header)))
            comp_len = struct.unpack("<I", header)[0]
            body_end = cur + 4 + comp_len
            if comp_len > 0:
                reader.skip(comp_len)
            after = cur + 4
            remaining = _file_size(reader) - after
            if comp_len > remaining:
                raise ArchiveError(
                    "STREAM_TRUNCATED", name, cur,
                    "segment=%d need=%d have=%d"
                    % (seg, comp_len, remaining))
            span += 4 + comp_len
            cur = body_end
            segment_total += 1
        entries.append(Entry(seq, name, offset, span, size, crc,
                             segment_count))
    return entries, segment_total


def _file_size(reader: _CountingReader) -> int:
    fp = reader._fp
    cur = fp.tell()
    fp.seek(0, os.SEEK_END)
    size = fp.tell()
    fp.seek(cur)
    return size


def build_index(path: str) -> Index:
    """扫一遍归档建立索引，不读任何段体。"""
    import time

    start = time.perf_counter()
    actual = os.path.getsize(path)
    with open(path, "rb") as fp:
        reader = _CountingReader(fp)
        header = reader.read(HEADER_SIZE)
        if len(header) < HEADER_SIZE:
            raise ArchiveError("LENGTH_MISMATCH", "-", "-",
                               "actual=%d" % actual)
        magic, version, _reserved, count, table_off, table_size, length = \
            struct.unpack("<4sHHIQIQ", header)
        if magic != MAGIC or version != VERSION:
            raise ArchiveError("LENGTH_MISMATCH", "-", "-",
                               "actual=%d" % actual)
        if length != actual:
            raise ArchiveError("LENGTH_MISMATCH", "-", str(length),
                               "actual=%d" % actual)
        if table_off < HEADER_SIZE or table_off + table_size > length:
            raise ArchiveError("TABLE_RANGE", "-", str(table_off),
                               "size=%d limit=%d" % (table_size, length))
        reader.seek(table_off)
        table = reader.read(table_size)
        if len(table) != table_size:
            raise ArchiveError("TABLE_RANGE", "-", str(table_off),
                               "size=%d limit=%d" % (table_size, length))
        data_start = table_off + table_size
        reader.seek(data_start)
        entries, segment_total = _parse_table(table, count, data_start, reader)
    elapsed = (time.perf_counter() - start) * 1000.0
    return Index(length, table_off, table_size, count, segment_total,
                 tuple(entries), reader.bytes_read, elapsed)


_INDEX_HEADER = struct.Struct("<4sHHQIIQ")


def encode_index(index: Index) -> bytes:
    """把索引序列化成确定性的二进制格式。"""
    parts = [_INDEX_HEADER.pack(
        INDEX_MAGIC, VERSION, 0, index.length, index.entry_count,
        index.segment_count, index.table_offset)]
    for entry in index.entries:
        name_bytes = entry.name.encode("utf-8")
        parts.append(struct.pack("<H", len(name_bytes)))
        parts.append(name_bytes)
        parts.append(struct.pack("<QQQII", entry.offset, entry.span,
                                 entry.size, entry.crc32, entry.segments))
    return b"".join(parts)


def write_index(index: Index, path: str) -> None:
    with open(path, "wb") as fp:
        fp.write(encode_index(index))


def load_index(path: str) -> Index:
    if not os.path.isfile(path):
        raise UsageError("索引不存在: %s" % path)
    with open(path, "rb") as fp:
        blob = fp.read()
    if len(blob) < _INDEX_HEADER.size:
        raise UsageError("索引已损坏: %s" % path)
    magic, version, _reserved, length, count, segment_total, table_off = \
        _INDEX_HEADER.unpack_from(blob, 0)
    if magic != INDEX_MAGIC or version != VERSION:
        raise UsageError("索引格式不符: %s" % path)
    pos = _INDEX_HEADER.size
    entries: list[Entry] = []
    for seq in range(count):
        if pos + 2 > len(blob):
            raise UsageError("索引已损坏: %s" % path)
        name_len = _u16(blob, pos)
        pos += 2
        if pos + name_len + 32 > len(blob):
            raise UsageError("索引已损坏: %s" % path)
        name = blob[pos:pos + name_len].decode("utf-8")
        pos += name_len
        offset, span, size, crc, seg_count = struct.unpack_from(
            "<QQQII", blob, pos)
        pos += 32
        entries.append(Entry(seq, name, offset, span, size, crc, seg_count))
    if pos != len(blob):
        raise UsageError("索引已损坏: %s" % path)
    return Index(length, table_off, 0, count, segment_total,
                 tuple(entries), 0, 0.0)


def open_archive(archive_path: str, index: Index | str) -> "Archive":
    """按索引打开归档；索引缺失或与归档不配套抛 :class:`UsageError`。"""
    if isinstance(index, str):
        index = load_index(index)
    if not os.path.isfile(archive_path):
        raise UsageError("归档不存在: %s" % archive_path)
    if os.path.getsize(archive_path) != index.length:
        raise UsageError("索引与归档不配套（长度不符）: %s" % archive_path)
    return Archive(archive_path, index)


class Archive:
    """随机读取归档条目的句柄。"""

    def __init__(self, path: str, index: Index):
        self._path = path
        self._index = index
        self._by_name = index.by_name
        self.last_read: ReadStat | None = None

    def entries(self) -> tuple[Entry, ...]:
        return self._index.entries

    def read(self, name: str) -> bytes:
        """随机读一个条目：只读它自己的跨度，逐段解压并校验。"""
        import time

        entry = self._by_name.get(name)
        if entry is None:
            raise UsageError("条目不存在: %s" % name)
        start = time.perf_counter()
        with open(self._path, "rb") as fp:
            fp.seek(entry.offset)
            blob = fp.read(entry.span) if entry.span else b""
            bytes_read = len(blob)
            if len(blob) != entry.span:
                raise ArchiveError(
                    "STREAM_TRUNCATED", name, entry.offset,
                    "segment=0 need=%d have=%d"
                    % (entry.span, len(blob)))
            chunks: list[bytes] = []
            pos = 0
            for seg in range(entry.segments):
                if pos + 4 > len(blob):
                    raise ArchiveError(
                        "STREAM_TRUNCATED", name, entry.offset + pos,
                        "segment=%d need=4 have=%d"
                        % (seg, len(blob) - pos))
                comp_len = _u32(blob, pos)
                pos += 4
                if pos + comp_len > len(blob):
                    raise ArchiveError(
                        "STREAM_TRUNCATED", name, entry.offset + pos - 4,
                        "segment=%d need=%d have=%d"
                        % (seg, comp_len, len(blob) - pos))
                try:
                    chunks.append(zlib.decompress(blob[pos:pos + comp_len]))
                except zlib.error as exc:
                    raise ArchiveError(
                        "STREAM_ERROR", name, entry.offset + pos - 4,
                        "segment=%d zlib error: %s" % (seg, exc))
                pos += comp_len
        data = b"".join(chunks)
        if len(data) != entry.size or zlib.crc32(data) != entry.crc32:
            raise ArchiveError(
                "CRC_MISMATCH", name, str(entry.offset),
                "size=%d crc=%08x expected_crc=%08x"
                % (len(data), zlib.crc32(data), entry.crc32))
        elapsed = (time.perf_counter() - start) * 1000.0
        self.last_read = ReadStat(name, entry.offset, entry.span,
                                  entry.size, bytes_read, elapsed)
        return data

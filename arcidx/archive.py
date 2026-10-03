"""随机读取引擎：按索引只取目标条目的跨度，逐段解压并校验。"""

import time
import zlib

from . import format as fmt
from .counting import CountingReader, file_size
from .errors import (
    CorruptStreamError,
    CrcMismatchError,
    FormatError,
    UnknownEntryError,
)
from .index import load_index


class ReadStats:
    """最近一次 read 的统计（README 第 4 节 last_read）。"""

    __slots__ = ("name", "data_offset", "span", "raw_size", "bytes_read",
                 "elapsed_ms")

    def __init__(self, name, data_offset, span, raw_size, bytes_read, elapsed_ms):
        self.name = name
        self.data_offset = data_offset
        self.span = span
        self.raw_size = raw_size
        self.bytes_read = bytes_read
        self.elapsed_ms = elapsed_ms


class Archive:
    """open_archive 的返回对象：条目表 + 按名随机读。"""

    def __init__(self, archive_path, index_path):
        self._archive_path = archive_path
        loaded = load_index(index_path, archive_path)
        self._entries = loaded.entries
        self._by_name = {entry.name: entry for entry in self._entries}
        self._reader = CountingReader.open(archive_path)
        self.last_read = None

    def close(self):
        self._reader.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def entries(self):
        """条目表，顺序同归档条目表。"""
        return list(self._entries)

    def find(self, name):
        return self._by_name.get(name)

    def read(self, name):
        """随机读一条：只从归档取回该条目跨度内的字节，返回原始内容。"""
        entry = self._by_name.get(name)
        if entry is None:
            raise UnknownEntryError(f"entry not found: {name}")
        started = time.perf_counter()
        before = self._reader.bytes_read
        self._reader.seek(entry.data_offset)
        blob = self._reader.read_exact(entry.span)
        fetched = self._reader.bytes_read - before
        if len(blob) < entry.span:
            raise FormatError(
                "STREAM_TRUNCATED", name, entry.data_offset + len(blob),
                f"segment=0 need={entry.span - len(blob)} have=0",
            )
        content = self._inflate(entry, blob)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.last_read = ReadStats(
            name, entry.data_offset, entry.span, entry.raw_size,
            fetched, elapsed_ms,
        )
        return content

    @staticmethod
    def _inflate(entry, blob):
        out = bytearray()
        pos = 0
        for seg in range(entry.segments):
            if pos + fmt.SEGMENT_HEADER_SIZE > len(blob):
                raise FormatError(
                    "STREAM_TRUNCATED", entry.name, entry.data_offset + pos,
                    f"segment={seg} need=4 have={len(blob) - pos}",
                )
            compressed_len = int.from_bytes(blob[pos : pos + 4], "little")
            pos += fmt.SEGMENT_HEADER_SIZE
            if pos + compressed_len > len(blob):
                raise FormatError(
                    "STREAM_TRUNCATED", entry.name,
                    entry.data_offset + pos - fmt.SEGMENT_HEADER_SIZE,
                    f"segment={seg} need={compressed_len} "
                    f"have={len(blob) - pos}",
                )
            try:
                out += zlib.decompress(blob[pos : pos + compressed_len])
            except zlib.error as err:
                raise CorruptStreamError(
                    entry.name,
                    entry.data_offset + pos - fmt.SEGMENT_HEADER_SIZE,
                    seg, err,
                ) from err
            pos += compressed_len
        if len(out) != entry.raw_size:
            raise FormatError(
                "LENGTH_MISMATCH", entry.name, entry.data_offset,
                f"actual={len(out)}",
            )
        actual_crc = zlib.crc32(out)
        if actual_crc != entry.crc32:
            raise CrcMismatchError(entry.name, entry.crc32, actual_crc)
        return bytes(out)


def open_archive(archive_path, index_path):
    """库入口：打开归档 + 索引，返回 Archive。"""
    return Archive(archive_path, index_path)

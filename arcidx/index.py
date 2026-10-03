"""索引的构建、序列化与载入。

索引布局（二进制、小端、确定性）：
  固定头 48 字节：magic "ARCI" + u32 版本 + u64 归档总长 L + u64 条目数
                  + u64 段总数 + u64 条目表偏移 T + u64 条目表字节数 S
  之后每条目：u16 名字字节数 + 名字 + u64 数据偏移 + u64 跨度
              + u64 原始大小 + u32 crc32 + u32 段数

体积 = 48 + 34 * 条目数 + 名字总字节数，
稳在 README 上界 1024 + 48n + 16k + 名字字节数 之内。
索引不存条目内容，也不存逐段偏移——单条随机读只需要数据偏移与跨度，
段边界信息对读取没有收益，省掉换来更小的索引。
"""

import os
import struct

from . import format as fmt
from .counting import CountingReader, file_size
from .errors import UsageError
from .scanner import Entry, scan

INDEX_HEADER_STRUCT = struct.Struct("<4sI5Q")
INDEX_NAME_LEN_STRUCT = struct.Struct("<H")
INDEX_RECORD_STRUCT = struct.Struct("<QQQII")


def build_index(archive_path):
    """扫一遍归档，返回 (ScanResult, 索引字节)。"""
    with CountingReader.open(archive_path) as reader:
        result = scan(reader, file_size(archive_path))
    return result, dump_index(result)


def dump_index(result):
    """把扫描结果序列化成索引字节（确定性：同输入同字节）。"""
    out = bytearray()
    out += INDEX_HEADER_STRUCT.pack(
        fmt.INDEX_MAGIC,
        fmt.INDEX_VERSION,
        result.total_length,
        len(result.entries),
        result.segment_total,
        result.table_offset,
        result.table_size,
    )
    for entry in result.entries:
        name_bytes = entry.name.encode("utf-8")
        out += INDEX_NAME_LEN_STRUCT.pack(len(name_bytes))
        out += name_bytes
        out += INDEX_RECORD_STRUCT.pack(
            entry.data_offset,
            entry.span,
            entry.raw_size,
            entry.crc32,
            entry.segments,
        )
    return bytes(out)


def write_index(index_bytes, index_path):
    with open(index_path, "wb") as fh:
        fh.write(index_bytes)
    return len(index_bytes)


class LoadedIndex:
    """load_index 的结果：条目列表 + 配套校验所需的归档长度。"""

    __slots__ = ("entries", "total_length", "segment_total")

    def __init__(self, entries, total_length, segment_total):
        self.entries = entries
        self.total_length = total_length
        self.segment_total = segment_total


def parse_index(data):
    """解析索引字节；任何不合法都抛 UsageError。"""
    bad = UsageError("index file is malformed or truncated")
    if len(data) < fmt.INDEX_HEADER_SIZE:
        raise bad
    magic, version, total_length, entry_count, segment_total, _t, _s = (
        INDEX_HEADER_STRUCT.unpack_from(data, 0)
    )
    if magic != fmt.INDEX_MAGIC or version != fmt.INDEX_VERSION:
        raise UsageError("index file magic/version mismatch")
    entries = []
    pos = fmt.INDEX_HEADER_SIZE
    for _ in range(entry_count):
        if pos + 2 > len(data):
            raise bad
        (name_len,) = INDEX_NAME_LEN_STRUCT.unpack_from(data, pos)
        pos += 2
        if pos + name_len + INDEX_RECORD_STRUCT.size > len(data):
            raise bad
        try:
            name = data[pos : pos + name_len].decode("utf-8")
        except UnicodeDecodeError:
            raise bad
        pos += name_len
        data_offset, span, raw_size, crc32, segments = (
            INDEX_RECORD_STRUCT.unpack_from(data, pos)
        )
        pos += INDEX_RECORD_STRUCT.size
        entries.append(Entry(name, data_offset, span, raw_size, crc32, segments))
    if pos != len(data):
        raise bad
    return LoadedIndex(entries, total_length, segment_total)


def load_index(index_path, archive_path):
    """载入索引并校验与归档配套（记录归档总长 L）；不配套按用法错误。"""
    if not os.path.exists(index_path):
        raise UsageError(f"index file not found: {index_path}")
    with open(index_path, "rb") as fh:
        data = fh.read()
    loaded = parse_index(data)
    actual = file_size(archive_path)
    if loaded.total_length != actual:
        raise UsageError(
            f"index does not match archive: index L={loaded.total_length} "
            f"archive L={actual}"
        )
    return loaded

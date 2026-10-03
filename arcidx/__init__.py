"""arcidx：只读归档索引与随机读取库。"""

from .archive import Archive, ReadStats, open_archive
from .checker import check_archive
from .errors import (
    ArcidxError,
    CorruptStreamError,
    CrcMismatchError,
    FormatError,
    UnknownEntryError,
    UsageError,
)
from .index import build_index, load_index
from .scanner import Entry

__all__ = [
    "Archive",
    "ReadStats",
    "Entry",
    "open_archive",
    "check_archive",
    "build_index",
    "load_index",
    "ArcidxError",
    "FormatError",
    "UsageError",
    "UnknownEntryError",
    "CorruptStreamError",
    "CrcMismatchError",
]

"""归档与索引格式的常量定义（口径见 README 第 2、3 节）。"""

MAGIC = b"ARCK"
VERSION = 1
HEADER_SIZE = 32

SEGMENT_SIZE = 65536
SEGMENT_HEADER_SIZE = 4

MAX_NAME_BYTES = 1024

INDEX_MAGIC = b"ARCI"
INDEX_VERSION = 1
# 索引固定头：magic(4) + version(4) + L(8) + n(8) + segments(8) + T(8) + S(8)
INDEX_HEADER_SIZE = 48
# 每条索引记录的定长部分：u16 名字长 + u64 偏移 + u64 跨度 + u64 原始大小 + u32 crc32 + u32 段数
INDEX_RECORD_FIXED = 34

# 上界公式（README 第 2 节）
INDEX_SIZE_SLACK = 1024
INDEX_SIZE_PER_ENTRY = 48
INDEX_SIZE_PER_SEGMENT = 16
BUILD_READ_SLACK = 4096
BUILD_READ_PER_SEGMENT = 4
READ_SLACK = 1024


def segment_count(raw_size):
    """条目内容按 65536 字节切段的段数。"""
    if raw_size <= 0:
        return 0
    return (raw_size + SEGMENT_SIZE - 1) // SEGMENT_SIZE


def index_size_bound(entry_count, segment_total, name_bytes):
    return (
        INDEX_SIZE_SLACK
        + INDEX_SIZE_PER_ENTRY * entry_count
        + INDEX_SIZE_PER_SEGMENT * segment_total
        + name_bytes
    )


def build_read_bound(table_size, segment_total):
    return HEADER_SIZE + table_size + BUILD_READ_PER_SEGMENT * segment_total + BUILD_READ_SLACK

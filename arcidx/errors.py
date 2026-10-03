"""错误类型：坏包诊断、用法错误、校验失败。"""


class ArcidxError(Exception):
    """库内所有错误的基类。"""


class FormatError(ArcidxError):
    """归档结构损坏；携带 check 诊断所需的四列。"""

    def __init__(self, code, entry, offset, detail):
        self.code = code
        self.entry = entry
        self.offset = offset
        self.detail = detail
        super().__init__(self.line())

    def line(self):
        entry = self.entry if self.entry is not None else "-"
        offset = str(self.offset) if self.offset is not None else "-"
        return f"{self.code}\t{entry}\t{offset}\t{self.detail}"


class UsageError(ArcidxError):
    """用法错误（索引缺失、索引与归档不配套等），退出码 2。"""


class UnknownEntryError(UsageError):
    """条目名不在条目表里。"""


class CorruptStreamError(ArcidxError):
    """段体不是合法的 zlib 流（归档数据区损坏）。"""

    def __init__(self, name, offset, segment, reason):
        self.name = name
        self.offset = offset
        self.segment = segment
        super().__init__(
            f"corrupt zlib stream in {name!r} at offset {offset} "
            f"(segment {segment}): {reason}"
        )


class CrcMismatchError(ArcidxError):
    """读出的内容 crc32 与条目表不符。"""

    def __init__(self, name, expected, actual):
        self.name = name
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"crc32 mismatch on {name!r}: table={expected:08x} actual={actual:08x}"
        )

"""坏包诊断：按 README 第 3 节解析顺序走，报出第一个问题。"""

from .counting import CountingReader, file_size
from .errors import FormatError
from .scanner import scan


def check_archive(archive_path):
    """返回 FormatError（发现问题）或 None（无问题）。"""
    try:
        with CountingReader.open(archive_path) as reader:
            scan(reader, file_size(archive_path))
    except FormatError as err:
        return err
    return None

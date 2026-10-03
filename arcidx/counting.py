"""带字节计数的文件读取包装。

「读入字节数」只统计引擎从归档文件对象取回的字节数；seek 不计。
底层用无缓冲的 FileIO，保证 read 调用与引擎取回的字节一一对应。
"""

import os


class CountingReader:
    def __init__(self, raw):
        self._raw = raw
        self.bytes_read = 0

    @classmethod
    def open(cls, path):
        return cls(open(path, "rb", buffering=0))

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def seek(self, offset):
        self._raw.seek(offset)

    def tell(self):
        return self._raw.tell()

    def read(self, n=-1):
        data = self._raw.read(n)
        self.bytes_read += len(data)
        return data

    def read_exact(self, n):
        """读满 n 字节；文件不够长时返回实际读到的（可能更少）。"""
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self._raw.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            self.bytes_read += len(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


def file_size(path):
    return os.path.getsize(path)

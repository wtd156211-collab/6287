"""库级单元测试：合成归档上的索引、随机读、错误诊断。"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
import zlib

from arcidx import (ArchiveError, UsageError, build_index, encode_index,
                    load_index, open_archive, write_index)

from .helpers import SEGMENT, build_archive


class EngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _paths(self, blob):
        arc = os.path.join(self.tmp, "a.arc")
        idx = os.path.join(self.tmp, "a.idx")
        with open(arc, "wb") as fp:
            fp.write(blob)
        return arc, idx

    def test_roundtrip_single_and_multi_segment(self):
        payloads = {
            "small.txt": b"hello world",
            "empty.dat": b"",
            "big.bin": bytes((i * 7 + 3) % 256 for i in range(SEGMENT + 123)),
            "目录/中文.txt": ("文本" * 30000).encode("utf-8"),
        }
        blob = build_archive(list(payloads.items()))
        arc, idx = self._paths(blob)
        index = build_index(arc)
        self.assertEqual(index.entry_count, 4)
        self.assertEqual(index.segment_count, 6)  # 1+0+2+3
        write_index(index, idx)
        archive = open_archive(arc, load_index(idx))
        for name, payload in payloads.items():
            data = archive.read(name)
            self.assertEqual(data, payload)
            stat = archive.last_read
            self.assertEqual(stat.bytes_read, stat.span)
            self.assertLessEqual(stat.bytes_read, stat.span + 1024)
            self.assertLess(stat.elapsed_ms, 20.0)

    def test_index_does_not_read_segment_bodies(self):
        blob = build_archive([("a", b"x" * 10), ("b", b"y" * 10)])
        arc, _ = self._paths(blob)
        index = build_index(arc)
        table_size = 2 * (2 + 1 + 12)  # 两条记录
        # 只读了头部、表、4 字节/段头
        self.assertEqual(index.bytes_read, 32 + table_size + 4 * 2)

    def test_index_deterministic_and_within_bound(self):
        payloads = [("f%02d" % i, b"abc" * (i + 1)) for i in range(30)]
        arc, idx = self._paths(build_archive(payloads))
        i1 = encode_index(build_index(arc))
        i2 = encode_index(build_index(arc))
        self.assertEqual(i1, i2)
        with open(idx, "wb") as fp:
            fp.write(i1)
        index = load_index(idx)
        name_bytes = sum(len(n.encode()) for n, _ in payloads)
        bound = 1024 + 48 * len(payloads) + 16 * index.segment_count + name_bytes
        self.assertLessEqual(len(i1), bound)

    def test_index_round_trips_fields(self):
        arc, idx = self._paths(build_archive([("n", b"zzzz")]))
        write_index(build_index(arc), idx)
        entry = load_index(idx).entries[0]
        self.assertEqual((entry.index, entry.name, entry.size,
                          entry.segments, entry.crc32_hex),
                         (0, "n", 4, 1, "%08x" % zlib.crc32(b"zzzz")))

    def test_crc_mismatch_on_corrupted_content(self):
        blob = bytearray(build_archive([("a", b"hello"), ("b", b"world")]))
        blob[-1] ^= 0xFF
        arc, idx = self._paths(bytes(blob))
        write_index(build_index(arc), idx)
        with self.assertRaises(ArchiveError) as ctx:
            open_archive(arc, load_index(idx)).read("b")
        self.assertEqual(ctx.exception.code, "STREAM_ERROR")
        self.assertEqual(ctx.exception.entry, "b")

    def test_unknown_entry_and_missing_index(self):
        arc, idx = self._paths(build_archive([("a", b"x")]))
        write_index(build_index(arc), idx)
        with self.assertRaises(UsageError):
            open_archive(arc, os.path.join(self.tmp, "nope.idx"))
        with self.assertRaises(UsageError):
            open_archive(arc, load_index(idx)).read("missing")

    def test_index_archive_mismatch(self):
        arc, idx = self._paths(build_archive([("a", b"x")]))
        write_index(build_index(arc), idx)
        with open(arc, "ab") as fp:
            fp.write(b"extra")
        with self.assertRaises(UsageError):
            open_archive(arc, load_index(idx))

    def test_check_header_too_short(self):
        arc, _ = self._paths(b"ARCK\x01")
        with self.assertRaises(ArchiveError) as ctx:
            build_index(arc)
        self.assertEqual(ctx.exception.row[:3],
                         ("LENGTH_MISMATCH", "-", "-"))

    def test_check_bad_magic_and_version(self):
        for kwargs in ({"magic": b"XXXX"}, {"version": 9}):
            arc, _ = self._paths(build_archive([("a", b"x")], **kwargs))
            with self.assertRaises(ArchiveError) as ctx:
                build_index(arc)
            self.assertEqual(ctx.exception.code, "LENGTH_MISMATCH")
            self.assertEqual(ctx.exception.offset, "-")

    def test_check_length_mismatch(self):
        blob = build_archive([("a", b"x" * 20)], declared_length=999)
        arc, _ = self._paths(blob)
        with self.assertRaises(ArchiveError) as ctx:
            build_index(arc)
        err = ctx.exception
        self.assertEqual((err.code, err.offset), ("LENGTH_MISMATCH", "999"))
        self.assertEqual(err.detail, "actual=%d" % len(blob))

    def test_check_table_range(self):
        limit = 50
        blob = build_archive([("a", b"x" * 20)], table_offset=10_000,
                             declared_length=limit, truncate=limit)
        arc, _ = self._paths(blob)
        with self.assertRaises(ArchiveError) as ctx:
            build_index(arc)
        err = ctx.exception
        self.assertEqual(err.code, "TABLE_RANGE")
        self.assertEqual(err.offset, "10000")
        self.assertTrue(err.detail.startswith("size="))
        self.assertTrue(err.detail.endswith("limit=%d" % limit))

    def test_check_stream_truncated_body(self):
        full = build_archive([("ok", b"a" * 10), ("tail", b"z" * 40000)])
        cut = len(full) - 5
        arc, _ = self._paths(build_archive(
            [("ok", b"a" * 10), ("tail", b"z" * 40000)],
            declared_length=cut, truncate=cut))
        with self.assertRaises(ArchiveError) as ctx:
            build_index(arc)
        err = ctx.exception
        self.assertEqual(err.code, "STREAM_TRUNCATED")
        self.assertEqual(err.entry, "tail")
        self.assertIn("segment=0", err.detail)
        self.assertIn("have=", err.detail)

    def test_check_stream_truncated_header_need_4(self):
        # 数据区 b 的段头位置只剩 2 字节，段头读不满
        entries = [("a", b"x" * 10), ("b", b"y" * 10)]
        full = build_archive(entries)
        table_size = 2 * (2 + 1 + 12)
        seg_span = (len(full) - 32 - table_size) // 2
        cut = 32 + table_size + seg_span + 2
        arc, _ = self._paths(build_archive(
            entries, declared_length=cut, truncate=cut))
        with self.assertRaises(ArchiveError) as ctx:
            build_index(arc)
        err = ctx.exception
        self.assertEqual((err.code, err.entry),
                         ("STREAM_TRUNCATED", "b"))
        self.assertIn("need=4", err.detail)

    def test_segment_header_offset_is_diagnostic(self):
        cut = len(build_archive([("a", b"x" * 10)])) - 3
        arc, _ = self._paths(build_archive(
            [("a", b"x" * 10)], declared_length=cut, truncate=cut))
        with self.assertRaises(ArchiveError) as ctx:
            build_index(arc)
        # 偏移必须指向 a 的段头
        table_size = 2 + 1 + 12
        self.assertEqual(str(ctx.exception.offset), str(32 + table_size))


if __name__ == "__main__":
    unittest.main()

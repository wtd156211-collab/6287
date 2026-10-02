"""样例验收：entries.tsv / reads.tsv / check.tsv / limits.tsv 全对上。"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import zlib

from arcidx import build_index, encode_index, load_index, open_archive, write_index

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVES = os.path.join(ROOT, "samples", "archives")
BROKEN = os.path.join(ROOT, "samples", "broken")
EXPECTED = os.path.join(ROOT, "samples", "expected")

GOOD = ("many-small", "few-large", "skewed", "long-names")
BAD = ("length-mismatch", "table-range", "stream-truncated")


def _expected(name):
    with open(os.path.join(EXPECTED, name), "rb") as fp:
        return fp.read()


@unittest.skipUnless(os.path.isdir(ARCHIVES), "samples 目录缺失")
class SampleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _index(self, name):
        arc = os.path.join(ARCHIVES, name + ".arc")
        idx = os.path.join(self.tmp, name + ".idx")
        index = build_index(arc)
        write_index(index, idx)
        return arc, idx, index

    def test_entries_match_expected(self):
        for name in GOOD:
            with self.subTest(archive=name):
                arc, idx, _ = self._index(name)
                archive = open_archive(arc, load_index(idx))
                lines = "".join(
                    "\t".join(e.as_row()) + "\n" for e in archive.entries())
                self.assertEqual(lines.encode("utf-8"),
                                 _expected(name + ".entries.tsv"))

    def test_reads_match_expected(self):
        for name in GOOD:
            with self.subTest(archive=name):
                arc, idx, _ = self._index(name)
                archive = open_archive(arc, load_index(idx))
                for line in _expected(name + ".reads.tsv").decode(
                        "utf-8").splitlines():
                    entry_name, off, span, size, crc, sha = line.split("\t")
                    data = archive.read(entry_name)
                    stat = archive.last_read
                    self.assertEqual(stat.offset, int(off))
                    self.assertEqual(stat.span, int(span))
                    self.assertEqual(stat.size, int(size))
                    self.assertEqual("%08x" % zlib.crc32(data), crc)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), sha)
                    self.assertLessEqual(stat.bytes_read,
                                         stat.span + 1024)

    def test_check_outputs_match_expected(self):
        from arcidx.__main__ import main
        import contextlib
        import io
        for name in BAD:
            with self.subTest(broken=name):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = main(["check", os.path.join(BROKEN, name + ".arc")])
                self.assertEqual(rc, 1)
                self.assertEqual(buf.getvalue().encode("utf-8"),
                                 _expected(name + ".check.tsv"))

    def test_limits(self):
        limits = {}
        for line in _expected("limits.tsv").decode("utf-8").splitlines()[1:]:
            name, entries, segments, name_bytes, index_bound, _slack = \
                line.split("\t")
            limits[name] = (int(entries), int(segments), int(name_bytes),
                            int(index_bound))
        for name, (count, segments, name_bytes, bound) in limits.items():
            with self.subTest(archive=name):
                arc, idx, index = self._index(name)
                self.assertEqual(index.entry_count, count)
                self.assertEqual(index.segment_count, segments)
                self.assertEqual(
                    sum(len(e.name.encode("utf-8")) for e in index.entries),
                    name_bytes)
                self.assertLessEqual(os.path.getsize(idx), bound)
                # 建索引读入上界
                self.assertLessEqual(
                    index.bytes_read,
                    32 + index.table_size + 4 * segments + 4096)

    def test_index_deterministic(self):
        for name in GOOD:
            with self.subTest(archive=name):
                arc = os.path.join(ARCHIVES, name + ".arc")
                self.assertEqual(encode_index(build_index(arc)),
                                 encode_index(build_index(arc)))

    def test_index_build_budget(self):
        arc = os.path.join(ARCHIVES, "many-small.arc")
        index = build_index(arc)
        self.assertLess(index.elapsed_ms, 2000.0)

    def test_single_read_budget(self):
        arc, idx, _ = self._index("many-small")
        archive = open_archive(arc, load_index(idx))
        for entry in archive.entries():
            archive.read(entry.name)
            self.assertLess(archive.last_read.elapsed_ms, 20.0)


if __name__ == "__main__":
    unittest.main()

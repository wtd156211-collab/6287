"""验收测试：样例比对、上界、坏包诊断、CLI、页面与确定性。"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVES = os.path.join(ROOT, "samples", "archives")
BROKEN = os.path.join(ROOT, "samples", "broken")
EXPECTED = os.path.join(ROOT, "samples", "expected")
ARC_NAMES = ["many-small", "few-large", "skewed", "long-names"]

sys.path.insert(0, ROOT)
from arcidx import build_index, check_archive, open_archive  # noqa: E402
from arcidx.errors import (  # noqa: E402
    CrcMismatchError,
    FormatError,
    UsageError,
)
from arcidx.format import build_read_bound, index_size_bound  # noqa: E402
from arcidx.index import parse_index  # noqa: E402


def read_tsv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return [line.rstrip("\n").split("\t") for line in fh if line.strip()]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "arcidx", *args],
        cwd=ROOT, capture_output=True, text=True,
    )


class SamplesMixin:
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="arcidx-test-")
        cls.index_bytes = {}
        for name in ARC_NAMES:
            result, blob = build_index(os.path.join(ARCHIVES, name + ".arc"))
            cls.index_bytes[name] = (result, blob)
            with open(os.path.join(cls.tmp, name + ".idx"), "wb") as fh:
                fh.write(blob)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def idx(self, name):
        return os.path.join(self.tmp, name + ".idx")

    def arc(self, name):
        return os.path.join(ARCHIVES, name + ".arc")


class TestEntriesAndReads(SamplesMixin, unittest.TestCase):
    """验收 ①②：entries.tsv 逐字节相同；reads 的 crc/sha 对上、读入不超预算。"""

    def test_entries_tsv_matches_expected(self):
        for name in ARC_NAMES:
            with self.subTest(archive=name):
                got = run_cli("list", self.arc(name), self.idx(name))
                self.assertEqual(got.returncode, 0, got.stderr)
                with open(os.path.join(EXPECTED, name + ".entries.tsv"),
                          "rb") as fh:
                    self.assertEqual(got.stdout.encode("utf-8"), fh.read())

    def test_reads_match_crc32_and_sha256(self):
        for name in ARC_NAMES:
            rows = read_tsv(os.path.join(EXPECTED, name + ".reads.tsv"))
            with open_archive(self.arc(name), self.idx(name)) as archive:
                for row in rows:
                    entry_name, off, span, raw, crc, sha = row
                    off, span, raw = int(off), int(span), int(raw)
                    with self.subTest(archive=name, entry=entry_name):
                        data = archive.read(entry_name)
                        stats = archive.last_read
                        self.assertEqual(len(data), raw)
                        self.assertEqual(f"{zlib.crc32(data):08x}", crc)
                        self.assertEqual(hashlib.sha256(data).hexdigest(), sha)
                        self.assertEqual(stats.data_offset, off)
                        self.assertEqual(stats.span, span)
                        self.assertEqual(stats.bytes_read, span)
                        self.assertLessEqual(stats.bytes_read, span + 1024)
                        self.assertGreater(stats.elapsed_ms, 0.0)

    def test_cli_read_output_and_file(self):
        proc = run_cli("read", self.arc("skewed"), self.idx("skewed"),
                       "p00-random.bin", os.path.join(self.tmp, "out.bin"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = proc.stdout.rstrip("\n").split("\t")
        self.assertEqual(fields[0], "read")
        self.assertEqual(fields[1], "p00-random.bin")
        self.assertEqual(int(fields[5]), 65566)
        self.assertRegex(fields[6], r"^\d+\.\d{3}$")
        data = open(os.path.join(self.tmp, "out.bin"), "rb").read()
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         read_tsv(os.path.join(EXPECTED, "skewed.reads.tsv"))
                         [0][5])


class TestIndexBudgetAndDeterminism(SamplesMixin, unittest.TestCase):
    """验收 ④：索引不超上界、建索引读入不超上界、两次索引逐字节相同。"""

    def test_index_bounds_and_determinism(self):
        limits = {r[0]: r for r in read_tsv(os.path.join(EXPECTED, "limits.tsv"))
                  [1:]}
        for name in ARC_NAMES:
            with self.subTest(archive=name):
                result, first = self.index_bytes[name]
                _, second = build_index(self.arc(name))
                self.assertEqual(first, second)
                parsed = parse_index(first)
                name_bytes = sum(len(e.name.encode("utf-8"))
                                 for e in parsed.entries)
                bound = int(limits[name][4])
                self.assertEqual(
                    bound,
                    index_size_bound(len(parsed.entries), parsed.segment_total,
                                     name_bytes),
                )
                self.assertLessEqual(len(first), bound)
                self.assertLessEqual(
                    result.bytes_read,
                    build_read_bound(result.table_size, result.segment_total),
                )

    def test_index_is_lean(self):
        # 实际索引明显低于上界（取舍点：不存逐段偏移）。
        result, blob = self.index_bytes["many-small"]
        self.assertLessEqual(len(blob), int(48 * 500 + 16 * 510 + 13500 + 1024))
        self.assertLess(len(blob), 40000)


class TestBrokenArchives(unittest.TestCase):
    """验收 ③：3 个坏包 check 输出逐字节相同、退出码 1。"""

    BROKEN_NAMES = ["length-mismatch", "table-range", "stream-truncated"]

    def test_check_output_matches_expected(self):
        for name in self.BROKEN_NAMES:
            with self.subTest(broken=name):
                arc = os.path.join(BROKEN, name + ".arc")
                problem = check_archive(arc)
                self.assertIsInstance(problem, FormatError)
                with open(os.path.join(EXPECTED, name + ".check.tsv"),
                          encoding="utf-8") as fh:
                    self.assertEqual(problem.line() + "\n", fh.read())
                proc = run_cli("check", arc)
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(proc.stdout, problem.line() + "\n")
                self.assertEqual(proc.stderr, "")

    def test_check_good_archives_are_silent(self):
        for name in ARC_NAMES:
            with self.subTest(archive=name):
                arc = os.path.join(ARCHIVES, name + ".arc")
                self.assertIsNone(check_archive(arc))
                proc = run_cli("check", arc)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, "")

    def test_short_header_and_bad_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            short = os.path.join(tmp, "short.arc")
            with open(short, "wb") as fh:
                fh.write(b"ARCK\x01")
            err = check_archive(short)
            self.assertEqual(err.code, "LENGTH_MISMATCH")
            self.assertIsNone(err.offset)
            self.assertEqual(err.detail, "actual=5")

            bad = os.path.join(tmp, "bad.arc")
            with open(bad, "wb") as fh:
                fh.write(b"XXXX" + b"\x00" * 28)
            err = check_archive(bad)
            self.assertEqual(err.code, "LENGTH_MISMATCH")
            self.assertIsNone(err.offset)

    def test_diagnostic_locates_entry_and_offset(self):
        err = check_archive(
            os.path.join(BROKEN, "stream-truncated.arc"))
        self.assertEqual(err.code, "STREAM_TRUNCATED")
        self.assertEqual(err.entry, "tail.txt")
        self.assertEqual(err.offset, 1528)
        self.assertEqual(err.detail, "segment=0 need=1319 have=659")


class TestCliErrorsAndCrc(SamplesMixin, unittest.TestCase):
    """用法错误退出码 2；内容损坏时 crc 校验失败退出码 1。"""

    def test_missing_index_exit_code_2(self):
        proc = run_cli("list", self.arc("skewed"),
                       os.path.join(self.tmp, "missing.idx"))
        self.assertEqual(proc.returncode, 2)

    def test_mismatched_index_exit_code_2(self):
        proc = run_cli("list", self.arc("few-large"), self.idx("skewed"))
        self.assertEqual(proc.returncode, 2)

    def test_unknown_entry_exit_code_2(self):
        proc = run_cli("read", self.arc("skewed"), self.idx("skewed"),
                       "nope", os.path.join(self.tmp, "x"))
        self.assertEqual(proc.returncode, 2)

    def test_crc_mismatch_exit_code_1(self):
        src = self.arc("skewed")
        dst = os.path.join(self.tmp, "tampered.arc")
        data = bytearray(open(src, "rb").read())
        data[245 + 10] ^= 0xFF  # 翻第一条段体里的一个字节，段头不变
        with open(dst, "wb") as fh:
            fh.write(data)
        proc_index = run_cli("index", dst, os.path.join(self.tmp, "t.idx"))
        self.assertEqual(proc_index.returncode, 0, proc_index.stderr)
        proc = run_cli("read", dst, os.path.join(self.tmp, "t.idx"),
                       "p00-random.bin", os.path.join(self.tmp, "bad.bin"))
        self.assertEqual(proc.returncode, 1)
        from arcidx.errors import ArcidxError
        with self.assertRaises(ArcidxError):
            with open_archive(dst, os.path.join(self.tmp, "t.idx")) as ar:
                ar.read("p00-random.bin")

    def test_library_usage_errors(self):
        with self.assertRaises(UsageError):
            parse_index(b"too short")


class TestHtml(SamplesMixin, unittest.TestCase):
    """验收 ⑥：单文件页面，数值来自引擎，除耗时外确定。"""

    def test_html(self):
        out = os.path.join(self.tmp, "archive.html")
        proc = run_cli("html", self.arc("skewed"), self.idx("skewed"), out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        html = open(out, encoding="utf-8").read()
        self.assertNotRegex(html, r"https?://")
        self.assertNotIn("<script src", html)
        self.assertIn("本次读入字节数", html)
        match = re.search(r"const DATA = (\{.*?\});", html, re.S)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1).replace("<\\/", "</"))
        self.assertEqual(len(payload["entries"]), 8)
        entry = payload["entries"][0]
        self.assertEqual(entry["name"], "p00-random.bin")
        self.assertEqual(entry["offset"], 245)
        self.assertEqual(entry["span"], 65566)
        self.assertEqual(entry["rawSize"], 65536)
        self.assertEqual(entry["bytesRead"], 65566)
        self.assertGreater(entry["elapsedMs"], 0.0)

    def test_html_determinism_except_timings(self):
        paths = []
        for i in range(2):
            out = os.path.join(self.tmp, f"h{i}.html")
            run_cli("html", self.arc("long-names"), self.idx("long-names"), out)
            paths.append(out)
        strip = re.compile(r'"elapsedMs":[0-9.]+')
        a = strip.sub('"elapsedMs":0', open(paths[0], encoding="utf-8").read())
        b = strip.sub('"elapsedMs":0', open(paths[1], encoding="utf-8").read())
        self.assertEqual(a, b)


class TestPerformanceBudget(SamplesMixin, unittest.TestCase):
    """验收 ⑤：单条随机读在 20ms 内（不含解释器启动）。"""

    def test_random_read_under_20ms(self):
        with open_archive(self.arc("many-small"), self.idx("many-small")) as ar:
            ar.read("dir-00/sub-000/file-000.dat")  # 热身
            worst = 0.0
            for entry in ar.entries():
                ar.read(entry.name)
                worst = max(worst, ar.last_read.elapsed_ms)
            self.assertLess(worst, 20.0)

    def test_large_entry_memory(self):
        import tracemalloc
        with open_archive(self.arc("few-large"), self.idx("few-large")) as ar:
            tracemalloc.start()
            try:
                ar.read("logs/app.log")
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        self.assertLess(peak, 32 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main(verbosity=2)

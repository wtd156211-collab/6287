"""CLI 与页面测试：子进程跑 python -m arcidx，核对输出与退出码。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVES = os.path.join(ROOT, "samples", "archives")
BROKEN = os.path.join(ROOT, "samples", "broken")
EXPECTED = os.path.join(ROOT, "samples", "expected")


def run_cli(*args):
    env = dict(os.environ, PYTHONPATH=ROOT)
    return subprocess.run(
        [sys.executable, "-m", "arcidx", *args],
        capture_output=True, cwd=ROOT, env=env)


@unittest.skipUnless(os.path.isdir(ARCHIVES), "samples 目录缺失")
class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.arc = os.path.join(ARCHIVES, "many-small.arc")
        self.idx = os.path.join(self.tmp, "many.idx")
        proc = run_cli("index", self.arc, self.idx)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.index_line = proc.stdout.decode("utf-8")

    def test_index_line_shape(self):
        fields = self.index_line.rstrip("\n").split("\t")
        self.assertEqual(fields[0], "index")
        self.assertEqual(len(fields), 6)
        self.assertEqual(fields[1], "500")
        self.assertEqual(fields[2], "510")
        self.assertEqual(int(fields[3]), os.path.getsize(self.idx))
        self.assertRegex(fields[5], r"^\d+\.\d{3}$")

    def test_list_matches_expected(self):
        proc = run_cli("list", self.arc, self.idx)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(os.path.join(EXPECTED, "many-small.entries.tsv"),
                  "rb") as fp:
            self.assertEqual(proc.stdout, fp.read())
        self.assertNotIn(b"\r\n", proc.stdout)

    def test_read_line_and_output(self):
        out_file = os.path.join(self.tmp, "out.bin")
        proc = run_cli("read", self.arc, self.idx,
                       "dir-00/sub-000/file-000.dat", out_file)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fields = proc.stdout.decode("utf-8").rstrip("\n").split("\t")
        self.assertEqual(fields[0], "read")
        self.assertEqual(fields[1], "dir-00/sub-000/file-000.dat")
        self.assertEqual(len(fields), 7)
        self.assertRegex(fields[6], r"^\d+\.\d{3}$")
        with open(out_file, "rb") as fp:
            data = fp.read()
        self.assertEqual(len(data), int(fields[4]))
        # 读入字节数 ≤ 跨度 + 1024
        self.assertLessEqual(int(fields[5]), int(fields[3]) + 1024)

    def test_read_missing_entry_is_usage_error(self):
        proc = run_cli("read", self.arc, self.idx, "nope",
                       os.path.join(self.tmp, "x"))
        self.assertEqual(proc.returncode, 2)

    def test_missing_index_is_usage_error(self):
        proc = run_cli("list", self.arc, os.path.join(self.tmp, "no.idx"))
        self.assertEqual(proc.returncode, 2)

    def test_check_exit_codes(self):
        for name in ("length-mismatch", "table-range", "stream-truncated"):
            proc = run_cli("check", os.path.join(BROKEN, name + ".arc"))
            self.assertEqual(proc.returncode, 1)
            with open(os.path.join(EXPECTED, name + ".check.tsv"),
                      "rb") as fp:
                self.assertEqual(proc.stdout, fp.read())
        proc = run_cli("check", self.arc)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"")

    def test_html_page_values_come_from_engine(self):
        page_path = os.path.join(self.tmp, "archive.html")
        proc = run_cli("html", self.arc, self.idx, page_path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(page_path, encoding="utf-8") as fp:
            page = fp.read()
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        match = re.search(
            r'<script id="archive-data" type="application/json">(.*?)</script>',
            page, re.S)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        self.assertEqual(payload["entry_count"], 500)
        self.assertEqual(len(payload["entries"]), 500)
        first = payload["entries"][0]
        self.assertEqual(first["name"], "dir-00/sub-000/file-000.dat")
        self.assertEqual(first["offset"], 20532)
        self.assertEqual(first["span"], 330)
        self.assertEqual(first["size"], 5166)
        self.assertEqual(first["bytes_read"], 330)
        self.assertIsInstance(first["elapsed_ms"], float)

    def test_html_deterministic_except_timing(self):
        def generate():
            path = os.path.join(self.tmp, "p.html")
            run_cli("html", self.arc, self.idx, path)
            with open(path, encoding="utf-8") as fp:
                return fp.read()

        def strip_timing(page):
            return re.sub(r'"elapsed_ms":[0-9.]+', '"elapsed_ms":0', page)

        self.assertEqual(strip_timing(generate()), strip_timing(generate()))


if __name__ == "__main__":
    unittest.main()

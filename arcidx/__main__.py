"""arcidx 命令行：index / list / read / html / check。"""

from __future__ import annotations

import sys

from . import (ArchiveError, UsageError, build_index, load_index,
               open_archive, write_index)
from .html import render_html


def _out(line: str) -> None:
    sys.stdout.write(line + "\n")


def _cmd_index(argv: list[str]) -> int:
    if len(argv) != 2:
        raise UsageError("用法: python -m arcidx index <归档> <索引>")
    archive_path, index_path = argv
    index = build_index(archive_path)
    write_index(index, index_path)
    import os
    index_bytes = os.path.getsize(index_path)
    _out("index\t%d\t%d\t%d\t%d\t%.3f" % (
        index.entry_count, index.segment_count, index_bytes,
        index.bytes_read, index.elapsed_ms))
    return 0


def _cmd_list(argv: list[str]) -> int:
    if len(argv) != 2:
        raise UsageError("用法: python -m arcidx list <归档> <索引>")
    archive = open_archive(argv[0], argv[1])
    out = sys.stdout
    for entry in archive.entries():
        out.write("\t".join(entry.as_row()) + "\n")
    return 0


def _cmd_read(argv: list[str]) -> int:
    if len(argv) != 4:
        raise UsageError("用法: python -m arcidx read <归档> <索引> <条目名> <输出文件>")
    archive_path, index_path, name, out_path = argv
    archive = open_archive(archive_path, index_path)
    data = archive.read(name)
    with open(out_path, "wb") as fp:
        fp.write(data)
    stat = archive.last_read
    _out("read\t%s\t%d\t%d\t%d\t%d\t%.3f" % (
        stat.name, stat.offset, stat.span, stat.size,
        stat.bytes_read, stat.elapsed_ms))
    return 0


def _cmd_html(argv: list[str]) -> int:
    if len(argv) != 3:
        raise UsageError("用法: python -m arcidx html <归档> <索引> <输出.html>")
    archive_path, index_path, out_path = argv
    archive = open_archive(archive_path, index_path)
    import os
    page = render_html(archive, os.path.basename(archive_path))
    with open(out_path, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(page)
    return 0


def _cmd_check(argv: list[str]) -> int:
    if len(argv) != 1:
        raise UsageError("用法: python -m arcidx check <归档>")
    try:
        build_index(argv[0])
    except ArchiveError as exc:
        _out("\t".join(exc.row))
        return 1
    return 0


_COMMANDS = {
    "index": _cmd_index,
    "list": _cmd_list,
    "read": _cmd_read,
    "html": _cmd_html,
    "check": _cmd_check,
}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(newline="\n")
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in _COMMANDS:
        sys.stderr.write(
            "用法: python -m arcidx <index|list|read|html|check> ...\n")
        return 2
    command = _COMMANDS[args[0]]
    try:
        return command(args[1:])
    except UsageError as exc:
        sys.stderr.write("用法错误: %s\n" % exc)
        return 2
    except ArchiveError as exc:
        sys.stderr.write("坏包: %s\n" % "\t".join(exc.row))
        return 1


if __name__ == "__main__":
    sys.exit(main())

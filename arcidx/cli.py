"""命令行：python -m arcidx {index,list,read,html,check} ..."""

import sys
import time

from .archive import open_archive
from .checker import check_archive
from .errors import ArcidxError, CrcMismatchError, FormatError, UsageError
from .index import build_index, write_index
from .html import generate_html


def _emit(line):
    """机器可读输出一律显式 LF（Windows 上也不转成 CRLF）。"""
    sys.stdout.buffer.write(line.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _cmd_index(args):
    started = time.perf_counter()
    result, index_bytes = build_index(args.archive)
    index_size = write_index(index_bytes, args.index)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    _emit(
        f"index\t{len(result.entries)}\t{result.segment_total}\t"
        f"{index_size}\t{result.bytes_read}\t{elapsed_ms:.3f}"
    )
    return 0


def _cmd_list(args):
    with open_archive(args.archive, args.index) as archive:
        out = []
        for ordinal, entry in enumerate(archive.entries()):
            out.append(
                f"{ordinal}\t{entry.name}\t{entry.data_offset}\t{entry.span}\t"
                f"{entry.raw_size}\t{entry.crc32:08x}\t{entry.segments}"
            )
    for line in out:
        _emit(line)
    return 0


def _cmd_read(args):
    with open_archive(args.archive, args.index) as archive:
        content = archive.read(args.name)
        stats = archive.last_read
    with open(args.output, "wb") as fh:
        fh.write(content)
    _emit(
        f"read\t{stats.name}\t{stats.data_offset}\t{stats.span}\t"
        f"{stats.raw_size}\t{stats.bytes_read}\t{stats.elapsed_ms:.3f}"
    )
    return 0


def _cmd_html(args):
    generate_html(args.archive, args.index, args.output)
    return 0


def _cmd_check(args):
    problem = check_archive(args.archive)
    if problem is None:
        return 0
    _emit(problem.line())
    return 1


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(prog="arcidx", description="归档索引与随机读取")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="扫归档写索引")
    p.add_argument("archive")
    p.add_argument("index")
    p.set_defaults(func=_cmd_index)

    p = sub.add_parser("list", help="打印条目表")
    p.add_argument("archive")
    p.add_argument("index")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("read", help="随机读一个条目")
    p.add_argument("archive")
    p.add_argument("index")
    p.add_argument("name")
    p.add_argument("output")
    p.set_defaults(func=_cmd_read)

    p = sub.add_parser("html", help="生成单文件页面")
    p.add_argument("archive")
    p.add_argument("index")
    p.add_argument("output")
    p.set_defaults(func=_cmd_html)

    p = sub.add_parser("check", help="诊断坏包")
    p.add_argument("archive")
    p.set_defaults(func=_cmd_check)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UsageError as err:
        print(f"arcidx: usage error: {err}", file=sys.stderr)
        return 2
    except CrcMismatchError as err:
        print(f"arcidx: {err}", file=sys.stderr)
        return 1
    except FormatError as err:
        print(err.line(), file=sys.stderr)
        return 1
    except ArcidxError as err:
        print(f"arcidx: {err}", file=sys.stderr)
        return 1

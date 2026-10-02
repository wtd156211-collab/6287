"""生成单文件 archive.html：条目表内联，点击条目看引擎读出的数值。"""

from __future__ import annotations

import json

from . import Archive

_PAGE_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>归档索引 · archivepeek</title>
<style>
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; font: 14px/1.5 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  background: #f5f6f8; color: #1f2329; height: 100vh; display: flex;
  flex-direction: column;
}
header { padding: 12px 20px; background: #22303c; color: #fff; }
header h1 { margin: 0; font-size: 16px; }
header .meta { margin-top: 4px; font-size: 12px; opacity: .75; }
main { flex: 1; display: flex; min-height: 0; }
aside {
  width: 380px; min-width: 260px; overflow: auto; border-right: 1px solid #d8dce3;
  background: #fff;
}
ul#entries { list-style: none; margin: 0; padding: 0; }
ul#entries li {
  padding: 7px 16px; cursor: pointer; border-bottom: 1px solid #eef0f3;
  font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
ul#entries li:hover { background: #eef4ff; }
ul#entries li.active { background: #dcebff; }
ul#entries li .seq { color: #8a9099; margin-right: 8px; }
section.detail { flex: 1; overflow: auto; padding: 24px 28px; }
.placeholder { color: #8a9099; margin-top: 40px; }
h2.name {
  font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 15px;
  word-break: break-all; margin: 0 0 20px;
}
table.stats { border-collapse: collapse; width: 100%; max-width: 720px; }
table.stats th, table.stats td {
  text-align: left; padding: 8px 12px; border-bottom: 1px solid #e4e7ec;
}
table.stats th { width: 180px; color: #5c6470; font-weight: 600; }
table.stats td { font-family: ui-monospace, Menlo, Consolas, monospace; }
.cost { font-size: 12px; color: #5c6470; margin-top: 20px; max-width: 720px; }
</style>
</head>
<body>
<header>
  <h1>归档索引与随机读取</h1>
  <div class="meta" id="meta"></div>
</header>
<main>
  <aside><ul id="entries"></ul></aside>
  <section class="detail" id="detail">
    <p class="placeholder">点击左侧条目，查看它的偏移、压缩前后大小、本次读入字节数与读取耗时。</p>
  </section>
</main>
<script id="archive-data" type="application/json">__DATA__</script>
<script>
"use strict";
var payload = JSON.parse(document.getElementById("archive-data").textContent);
document.getElementById("meta").textContent =
  payload.archive + " · " + payload.entry_count + " 个条目 · " +
  payload.segment_count + " 段 · 总长 " + payload.length + " 字节";

var listEl = document.getElementById("entries");
var detailEl = document.getElementById("detail");
payload.entries.forEach(function (e) {
  var li = document.createElement("li");
  var seq = document.createElement("span");
  seq.className = "seq";
  seq.textContent = e.index;
  li.appendChild(seq);
  li.appendChild(document.createTextNode(e.name));
  li.addEventListener("click", function () {
    var selected = listEl.querySelector(".active");
    if (selected) { selected.classList.remove("active"); }
    li.classList.add("active");
    renderDetail(e);
  });
  listEl.appendChild(li);
});

function row(label, value) {
  return "<tr><th>" + label + "</th><td>" + value + "</td></tr>";
}
function renderDetail(e) {
  detailEl.innerHTML =
    "<h2 class=\"name\">" + e.name + "</h2>" +
    "<table class=\"stats\">" +
    row("数据偏移", e.offset) +
    row("压缩前大小（字节）", e.size) +
    row("压缩后大小（字节）", e.span) +
    row("段数", e.segments) +
    row("crc32", e.crc32) +
    row("本次读入字节数", e.bytes_read) +
    row("读取耗时（毫秒）", e.elapsed_ms.toFixed(3)) +
    "</table>" +
    "<p class=\"cost\">本次只读了这一个条目的跨度（" + e.span +
    " 字节），没有触碰其他条目或整包。所有数值来自 arcidx 引擎的 last_read 统计。</p>";
}
</script>
</body>
</html>
"""


def render_html(archive: Archive, archive_name: str) -> str:
    """对每个条目做一次随机读，把引擎统计内联进单文件页面。"""
    records = []
    for entry in archive.entries():
        archive.read(entry.name)
        stat = archive.last_read
        records.append({
            "index": entry.index,
            "name": entry.name,
            "offset": entry.offset,
            "span": entry.span,
            "size": entry.size,
            "segments": entry.segments,
            "crc32": entry.crc32_hex,
            "bytes_read": stat.bytes_read,
            "elapsed_ms": round(stat.elapsed_ms, 3),
        })
    index = archive._index
    payload = {
        "archive": archive_name,
        "length": index.length,
        "entry_count": index.entry_count,
        "segment_count": index.segment_count,
        "entries": records,
    }
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # < 被转义后，名字里的 </script> 无法闭合脚本标签。
    data_json = data_json.replace("<", "\\u003c")
    return _PAGE_TEMPLATE.replace("__DATA__", data_json)

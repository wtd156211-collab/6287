"""生成单文件 archive.html：条目表 + 点击看详情。

生成时对每个条目各做一次随机读，把偏移、压缩前后大小、本次读入字节数
与耗时写进页面；数据全部内联，不发请求、不引 CDN。除耗时数值外输出确定。
"""

import json

from .archive import open_archive

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>归档条目 - arcidx</title>
<style>
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 0;
  background: #f4f6f8; color: #1c2733; }}
header {{ background: #1f3a5f; color: #fff; padding: 14px 22px; }}
header h1 {{ font-size: 18px; margin: 0; }}
header p {{ margin: 4px 0 0; font-size: 12px; opacity: .8; }}
main {{ display: flex; gap: 16px; padding: 16px 22px; align-items: flex-start; }}
#list {{ flex: 1 1 60%; background: #fff; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,.08); overflow: hidden; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 7px 10px; text-align: right; border-bottom: 1px solid #edf0f3;
  white-space: nowrap; }}
th:first-child, td:first-child {{ text-align: left; }}
thead th {{ background: #f0f3f7; cursor: pointer; user-select: none; }}
tbody tr {{ cursor: pointer; }}
tbody tr:hover {{ background: #f4f8fd; }}
tbody tr.active {{ background: #dce9fa; }}
td.name {{ font-family: Consolas, monospace; max-width: 46em;
  overflow: hidden; text-overflow: ellipsis; }}
#detail {{ flex: 0 0 320px; background: #fff; border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,.08); padding: 16px; position: sticky; top: 16px; }}
#detail h2 {{ font-size: 14px; margin: 0 0 10px; word-break: break-all; }}
#detail dl {{ display: grid; grid-template-columns: auto 1fr; gap: 6px 12px;
  font-size: 13px; margin: 0; }}
#detail dt {{ color: #66788a; }}
#detail dd {{ margin: 0; text-align: right; font-family: Consolas, monospace; }}
#summary {{ font-size: 12px; color: #66788a; padding: 8px 10px; }}
</style>
</head>
<body>
<header>
  <h1>归档条目索引</h1>
  <p id="meta"></p>
</header>
<main>
  <section id="list">
    <div id="summary"></div>
    <table>
      <thead><tr>
        <th>名字</th><th>数据偏移</th><th>压缩后</th><th>压缩前</th>
        <th>段数</th><th>读入字节</th><th>耗时 ms</th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </section>
  <aside id="detail"><h2>点击左侧条目查看详情</h2></aside>
</main>
<script>
const DATA = __DATA__;
const meta = document.getElementById("meta");
meta.textContent = "归档 " + DATA.archive + " · " + DATA.entries.length +
  " 个条目 · 归档 " + DATA.archiveBytes + " 字节 · 索引 " + DATA.indexBytes + " 字节";
document.getElementById("summary").textContent =
  "本次生成共随机读 " + DATA.entries.length + " 条，合计读入 " +
  DATA.entries.reduce((s, e) => s + e.bytesRead, 0) + " 字节";
const rows = document.getElementById("rows");
const detail = document.getElementById("detail");
DATA.entries.forEach((e, i) => {{
  const tr = document.createElement("tr");
  [e.name, e.offset, e.span, e.rawSize, e.segments, e.bytesRead,
   e.elapsedMs.toFixed(3)].forEach((v, j) => {{
    const td = document.createElement("td");
    td.textContent = v;
    if (j === 0) td.className = "name";
    tr.appendChild(td);
  }});
  tr.addEventListener("click", () => {{
    rows.querySelectorAll("tr.active").forEach(r => r.classList.remove("active"));
    tr.classList.add("active");
    detail.innerHTML = "";
    const h = document.createElement("h2");
    h.textContent = e.name;
    const dl = document.createElement("dl");
    [["数据偏移", e.offset], ["压缩前大小", e.rawSize + " 字节"],
     ["压缩后大小", e.span + " 字节"], ["段数", e.segments],
     ["crc32", e.crc], ["本次读入字节数", e.bytesRead + " 字节"],
     ["读取耗时", e.elapsedMs.toFixed(3) + " ms"]].forEach(([k, v]) => {{
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd"); dd.textContent = v;
      dl.append(dt, dd);
    }});
    detail.append(h, dl);
  }});
  rows.appendChild(tr);
}});
</script>
</body>
</html>
"""


def generate_html(archive_path, index_path, out_path):
    """对每个条目各做一次随机读，把引擎输出写进单文件页面。"""
    import os

    records = []
    with open_archive(archive_path, index_path) as archive:
        for entry in archive.entries():
            archive.read(entry.name)
            stats = archive.last_read
            records.append({
                "name": entry.name,
                "offset": stats.data_offset,
                "span": stats.span,
                "rawSize": stats.raw_size,
                "segments": entry.segments,
                "crc": f"{entry.crc32:08x}",
                "bytesRead": stats.bytes_read,
                "elapsedMs": round(stats.elapsed_ms, 3),
            })
    payload = {
        "archive": os.path.basename(archive_path),
        "archiveBytes": os.path.getsize(archive_path),
        "indexBytes": os.path.getsize(index_path),
        "entries": records,
    }
    data_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    data_json = data_json.replace("</", "<\\/")
    html = PAGE_TEMPLATE.replace("__DATA__", data_json)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    return records

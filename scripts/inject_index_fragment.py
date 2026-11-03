#!/usr/bin/env python3
"""Inject the auto-generated investigation list into the gh-pages landing page.

Replaces the content between the ``auto-investigations`` HTML markers in
``index.html`` with the fragment produced by
``publish_investigation_reports.py`` (``investigations_index.html``). Leaves the
file untouched if the markers are absent.

Usage: inject_index_fragment.py <fragment.html> <index.html>
"""
from __future__ import annotations

import sys
from pathlib import Path

BEGIN = "<!-- BEGIN auto-investigations"
END = "<!-- END auto-investigations -->"


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: inject_index_fragment.py <fragment.html> <index.html>",
              file=sys.stderr)
        return 2
    frag_path, index_path = Path(sys.argv[1]), Path(sys.argv[2])
    if not frag_path.is_file() or not index_path.is_file():
        print("fragment or index missing; nothing to inject")
        return 0
    fragment = frag_path.read_text(encoding="utf-8").strip()
    html = index_path.read_text(encoding="utf-8")
    begin, end = html.find(BEGIN), html.find(END)
    if begin == -1 or end == -1:
        print("auto-investigations markers not found; index.html left unchanged")
        return 0
    begin_close = html.find("-->", begin) + 3
    new_html = html[:begin_close] + "\n" + fragment + "\n" + html[end:]
    index_path.write_text(new_html, encoding="utf-8")
    print("index.html investigation list regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

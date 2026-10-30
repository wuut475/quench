#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把词库内嵌进 water.py，生成可以整段粘进云控制台的单文件版本。

    python3 build_fc.py      # → dist/water_fc.py

云函数（阿里云 FC / 腾讯云 SCF）的在线编辑器只接受单文件，
所以需要这一步；本地跑直接用 water.py + water_lib.json 就行。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
lib = json.loads((ROOT / "water_lib.json").read_text(encoding="utf-8"))
src = (ROOT / "water.py").read_text(encoding="utf-8")

literal = "EMBEDDED_LIB = json.loads(r'''" + json.dumps(lib, ensure_ascii=False) + "''')"
out, n = re.subn(r"^EMBEDDED_LIB = \{\}$", lambda _: literal, src, flags=re.M)
assert n == 1, "没找到 EMBEDDED_LIB 占位行"

dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
target = dist / "water_fc.py"
target.write_text(out, encoding="utf-8")
print(f"生成 {target}  ({len(out.encode())} 字节，{sum(len(v) for v in lib.values())} 条消息)")

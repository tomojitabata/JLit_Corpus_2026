#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_notebook_tables.py
========================
ノートブックの**一覧出力が表になっているか**を静的に検査する。

なぜ要るのか
------------
分析結果を ``print`` で流すと，あとから読めない。

1. 全角の幅のせいで桁が揃わず，数字を縦に比べられない
2. 列に名前が付かないので，見返したときに**何の数字か分からない**
3. 並べ替えも絞り込みもできない

そこで一覧は ``show(df, caption=…, fmt=…)`` で表にする約束にしてある。
セルを足すときに約束が破られるのを防ぐため，機械で見つける。

**何でも表にするわけではない。** 単発の数値・警告・KWIC の前後文脈・
読ませる本文は文のほうがよい。だから本スクリプトは「表にすべき形」に
絞って警告する。

検出するもの
------------
``[NG  ]`` **``display(`` を直接使っている**（``display(HTML(…))`` のように
          表でないものを出す場合は除く）
    ``show()`` を使うこと（見出し・索引の非表示・数値の右寄せが付く）。

``[warn]`` **桁詰めした print が2行以上あるセル**
    ``f'{x:<20}{y:>8}'`` のような幅指定は，手で表を組んでいる印である。
    全角が混ざると揃わない。``show()`` に渡すこと。

``[warn]`` **ループの中で print を2回以上呼んでいるセル**
    行を並べているなら表にできる。
    （警告・本文の表示など，表にすべきでないものも引っかかる。
    その場合は無視してよい。**この警告は禁止ではなく点検の合図である。**）

使い方
------
    python3 scripts/check_notebook_tables.py
    python3 scripts/check_notebook_tables.py --quiet   # NG だけ
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

OK, WARN, NG = '[ok  ]', '[warn]', '[NG  ]'

# f'{x:<20}' / f'{x:>8.1%}' のような幅指定つきの書式
PAD_RE = re.compile(r"\{[^{}]*:[<>^]\d")
# display( だが _display( ではないもの（PREAMBLE の内部実装は除く）
DISPLAY_RE = re.compile(r"(?<![_A-Za-z])display\s*\(")
# **表ではない display は除く。** リンクや画像を出す display(HTML(...)) /
# display(Markdown(...)) / display(Image(...)) は show() で置き換えられない
# （KWIC の画面へのリンクなど）。禁じたいのは**表を display で出すこと**である。
DISPLAY_OK_RE = re.compile(
    r"(?<![_A-Za-z])display\s*\(\s*(HTML|Markdown|Image|SVG|IFrame|Audio|Video)\s*\(")
PRINT_RE = re.compile(r"(?m)^(\s+)print\s*\(")


def check_cell(src: str) -> list[tuple[str, str]]:
    out = []
    n_disp = len(DISPLAY_RE.findall(src)) - len(DISPLAY_OK_RE.findall(src))
    if n_disp > 0:
        out.append((NG, f'display( を {n_disp} 回使っている → show() にする'))

    pads = [l for l in src.split('\n')
            if 'print(' in l and PAD_RE.search(l)]
    if len(pads) >= 2:
        out.append((WARN, f'桁詰めした print が {len(pads)} 行ある'
                          '（手で表を組んでいる）→ show() に渡す'))

    # インデントされた print（＝ループや分岐の中）の数
    indented = PRINT_RE.findall(src)
    if len(indented) >= 3 and 'show(' not in src:
        out.append((WARN, f'ループ内の print が {len(indented)} 箇所あり，'
                          'show() が無い → 一覧なら表にする'))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default='notebooks')
    ap.add_argument('--quiet', action='store_true', help='NG だけ表示する')
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, '*.ipynb')))
    if not files:
        print(f'{NG} ノートブックが無い: {args.dir}')
        return 1

    n_ng = n_warn = n_cell = 0
    for f in files:
        with open(f, encoding='utf-8') as fh:
            nb = json.load(fh)
        codes = [c for c in nb['cells'] if c['cell_type'] == 'code']
        for i, c in enumerate(codes, 1):
            src = ''.join(c['source'])
            if i == 1:
                continue          # 共通の準備セル（PREAMBLE）は対象外
            n_cell += 1
            for lv, msg in check_cell(src):
                if lv == NG:
                    n_ng += 1
                else:
                    n_warn += 1
                    if args.quiet:
                        continue
                print(f'{lv} {os.path.basename(f)} セル{i}\n        {msg}')

    print()
    print(f'{OK if not n_ng else NG} コードセル {n_cell} を検査：'
          f'NG {n_ng} 件／点検 {n_warn} 件')
    if n_ng:
        print('       display( は show( に直すこと（PREAMBLE の show を使う）。')
    if n_warn and not args.quiet:
        print('       [warn] は禁止ではない。**表にすべきものか**を目で決める。')
        print('       単発の数値・警告・KWIC・読ませる本文は print のままでよい。')
    return 1 if n_ng else 0


if __name__ == '__main__':
    raise SystemExit(main())

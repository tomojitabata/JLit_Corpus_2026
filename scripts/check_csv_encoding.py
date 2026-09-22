#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_csv_encoding.py
=====================
CSV / TSV が **macOS の Excel で文字化けしない**かを検査する。

なぜ要るのか
------------
macOS の Excel は，BOM の無い UTF-8 の CSV を開くと文字コードを推定し，
**日本語を Shift_JIS と読んで全部化けさせる。** 化けるのは表示だけなので，
受講生は「スクリプトが壊れた」と思い，教員は「こちらでは化けない」と答える
（Windows の Excel や Numbers では化けないことがある）。

対策は2つ。両方要る。

1. **書くときは BOM 付き**（``encoding='utf-8-sig'``）。Excel はこれを見て
   UTF-8 と判定する
2. **読むときも BOM を許す**（``encoding='utf-8-sig'``）。受講生が Excel で
   編集して保存すると BOM が付く。``utf-8`` で読むと最初の列名が
   ``\\ufeffauthor_ja`` になり，**1行も読めない**。しかも例外は出ず，
   「マニフェストが空です」という無関係なエラーになる

``utf-8-sig`` は BOM が無いファイルを読んでも無害なので，
**読み書きとも一律に ``utf-8-sig`` でよい。**

検査すること
------------
1. 同梱の .csv / .tsv に BOM があるか（設定ファイルは除外）
2. スクリプトが CSV/TSV を書くとき ``utf-8-sig`` を使っているか
3. スクリプトが CSV/TSV を読むとき ``utf-8-sig`` を使っているか

使い方
------
    python3 scripts/check_csv_encoding.py
    python3 scripts/check_csv_encoding.py --fix    # BOM を付け足す
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

OK, WARN, FATAL = '[ok  ]', '[warn]', '[FATAL]'
BOM = '﻿'

# 人が手で編集する設定ファイル。BOM は付けない（Git の差分が読みにくくなる）。
# ただし**読む側は BOM を許す**ので，Excel で保存されても壊れない。
CONFIG_FILES = {'config/corpus_manifest.tsv', 'config/merge_volumes.tsv'}


def check_files(root: str, fix: bool) -> list[tuple]:
    out = []
    for p in sorted(glob.glob(os.path.join(root, '**', '*.csv'), recursive=True)
                    + glob.glob(os.path.join(root, '**', '*.tsv'), recursive=True)):
        rel = os.path.relpath(p, root)
        if rel.replace(os.sep, '/') in CONFIG_FILES:
            out.append((OK, rel, '設定ファイル（BOM 不要・読む側が許容する）'))
            continue
        with open(p, 'rb') as fh:
            head = fh.read(3)
        if head == b'\xef\xbb\xbf':
            out.append((OK, rel, 'BOM あり'))
        elif fix:
            s = open(p, encoding='utf-8-sig').read()
            open(p, 'w', encoding='utf-8-sig').write(s)
            out.append((OK, rel, 'BOM を付けた'))
        else:
            out.append((FATAL, rel,
                        'BOM が無い。macOS の Excel で開くと化ける'
                        '（--fix で付けられる）'))
    return out


READ_RE = re.compile(r"open\([^)]*encoding=['\"]utf-8['\"]")


def check_scripts(root: str) -> list[tuple]:
    out = []
    for p in sorted(glob.glob(os.path.join(root, 'scripts', '*.py'))):
        rel = os.path.relpath(p, root)
        src = open(p, encoding='utf-8').read().split('\n')
        for n, line in enumerate(src, 1):
            if 'open(' not in line:
                continue
            blob = '\n'.join(src[n - 1:n + 3])
            if '.csv' not in blob and '.tsv' not in blob:
                continue
            if 'utf-8-sig' in blob:
                continue
            if "encoding='utf-8'" in blob or 'encoding="utf-8"' in blob:
                how = ('書いている（Excel で化ける）'
                       if ("'w'" in line or '"w"' in line)
                       else '読んでいる（Excel で保存された BOM 付きを読めない）')
                out.append((FATAL, f'{rel}:{n}',
                            f'CSV/TSV を utf-8（BOM 無し）で{how}: '
                            f'{line.strip()[:60]}'))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', default='.')
    ap.add_argument('--fix', action='store_true', help='BOM の無いファイルに付け足す')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    rows = check_files(args.root, args.fix) + check_scripts(args.root)
    n_fatal = sum(1 for lv, *_ in rows if lv == FATAL)
    for lv, where, msg in rows:
        if args.quiet and lv == OK:
            continue
        print(f'{lv} {where}\n        {msg}')
    print()
    if n_fatal:
        print(f'{FATAL} Excel で化ける箇所が {n_fatal} 件ある。')
        print('        ファイルなら --fix，スクリプトなら utf-8-sig に直すこと。')
    else:
        print(f'{OK} すべての CSV/TSV が BOM 付きで，'
              '読み書きとも utf-8-sig になっている。')
    return 1 if n_fatal else 0


if __name__ == '__main__':
    raise SystemExit(main())

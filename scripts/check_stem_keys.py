#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_stem_keys.py
==================
``work_stem`` のキーの作り方を静的に検査する。**0 埋めを忘れたキーを探す。**

なぜ必要か
------------
青空文庫の索引の作品 ID は 0 埋めされていない（``1743``）。本パイプラインの
ファイル名は6桁に 0 埋めしてある（``000119_001743``）。さらに
``corpus_metadata_v3.csv`` は，v1 由来の行では作品 ID が 0 埋めされておらず，
増補した行では 0 埋めされているという**混在状態**にあった。

そのため

    idx[f"{r['aozora_person_id']}_{r['aozora_work_id']}"] = r

と素朴に書くと，``000119_1743`` というキーができる。トークンファイルの語幹は
``000119_001743`` なので**1件も引けない**。

**そして，エラーは出ない。** ``dict.get`` はキーが見つからなくても例外を投げないので，
``period`` も ``year_first`` も空のまま図が描かれ，たとえば PCA の図では
多くの作品が「初出年不明」に入る。数字も図も出るので，**見た人が気づくまで
分からない**種類の誤りである。

検査すること
------------
``[NG  ]`` 人物 ID と作品 ID を連結したキーを作っているのに，
          **同じ式の中で ``zfill`` を呼んでいない**箇所。

``[warn]`` キーを作っているが，``zfill(6)`` 以外の桁数を使っている箇所
          （綴りが揃わない）。

使い方
------
    python3 scripts/check_stem_keys.py
    python3 scripts/check_stem_keys.py --dir scripts
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

OK, WARN, NG = '[ok  ]', '[warn]', '[NG  ]'

# 人物 ID と作品 ID を連結してキーを作っている式（f文字列でも連結でも拾う）
KEY_RE = re.compile(
    r"""f?['"][^'"]*\{\s*[A-Za-z_][\w.\[\]'"()]*\s*\}_\{\s*[A-Za-z_][\w.\[\]'"()]*\s*\}"""
)
# person/work の ID を扱っている行か（変数名でも列名でも）
IDISH_RE = re.compile(r'person_id|work_id|\bpid\b|\bwid\b')
ZFILL_RE = re.compile(r'zfill\(\s*(\d+)\s*\)')


# キーとして使われている印。辞書の添字・``get``・``keys`` への追加・
# ファイル名の組み立て。**print の中身は表示であってキーではない**ので外す。
USED_AS_KEY_RE = re.compile(
    r"""(\[\s*f?['"])|(\.get\(\s*f?['"])|(\bkeys\b)|(\bstem\b)|(\bname\s*=)"""
    r"""|(\bidx\b)|(\bindex\b)|(\bin\s+\w+\s*$)"""
)
DISPLAY_RE = re.compile(r'\bprint\s*\(|\bf-?string|\blabel\b|\bcaption\b')


def strip_noncode(text: str) -> list[str]:
    """注釈と三重引用符の文字列を空行にする。

    **注釈や docstring の中の「悪い例」を検出してはいけない。**
    この検査自身の説明文がそのまま引っかかると，誤検出が増えて警告が信用されなくなる。
    """
    out = []
    fence = ''
    for line in text.split('\n'):
        s = line
        if fence:
            end = s.find(fence)
            if end >= 0:
                s, fence = s[end + 3:], ''
            else:
                out.append('')
                continue
        # 行内の三重引用符の開始を探す
        while True:
            m = re.search(r"'''|\"\"\"", s)
            if not m:
                break
            q = m.group(0)
            rest = s[m.end():]
            close = rest.find(q)
            if close >= 0:                  # 同じ行で閉じている
                s = s[:m.start()] + ' ' + rest[close + 3:]
            else:                           # 次の行へ続く
                s, fence = s[:m.start()], q
                break
        # 行注釈を除く（文字列中の # は多くないので単純に扱う）
        h = s.find('#')
        if h >= 0 and s[:h].count("'") % 2 == 0 and s[:h].count('"') % 2 == 0:
            s = s[:h]
        out.append(s)
    return out


def scan_text(text: str, label: str) -> tuple[int, int]:
    """1つのソースを検査し ``(NG 件数, warn 件数)`` を返す。"""
    ng = warn = 0
    lines = strip_noncode(text)
    for i, line in enumerate(lines, 1):
        if not KEY_RE.search(line):
            continue
        if not USED_AS_KEY_RE.search(line) or DISPLAY_RE.search(line):
            continue                        # 表示のための文字列はキーではない
        # キーを作る式は複数行にまたがることがあるので前後も一緒に見る
        window = '\n'.join(lines[max(0, i - 4):i + 3])
        if not IDISH_RE.search(window):
            continue
        z = ZFILL_RE.findall(window)
        if not z:
            ng += 1
            print(f'{NG} {label}:{i}')
            print(f'        {line.strip()[:110]}')
            print('        ID を連結してキーを作っているが zfill が無い。'
                  '0 埋めの綴り違いで**1件も引けない**ことがある。')
        elif any(n != '6' for n in z):
            warn += 1
            print(f'{WARN} {label}:{i}  zfill({"，".join(z)}) は6桁ではない')
            print(f'        {line.strip()[:110]}')
    return ng, warn


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default='scripts')
    ap.add_argument('--notebooks', default='notebooks',
                    help='ノートブックのコードセルも検査する')
    args = ap.parse_args()

    ng = warn = n_files = 0
    for f in sorted(glob.glob(os.path.join(args.dir, '*.py'))):
        if os.path.basename(f) == os.path.basename(__file__):
            continue
        n_files += 1
        with open(f, encoding='utf-8') as fh:
            a, b = scan_text(fh.read(), os.path.basename(f))
        ng += a
        warn += b

    for f in sorted(glob.glob(os.path.join(args.notebooks, '*.ipynb'))):
        with open(f, encoding='utf-8') as fh:
            nb = json.load(fh)
        n_files += 1
        src = '\n'.join(''.join(c['source']) for c in nb['cells']
                        if c['cell_type'] == 'code')
        a, b = scan_text(src, os.path.basename(f))
        ng += a
        warn += b

    print()
    if ng:
        print(f'{NG} {n_files} ファイルを検査：0 埋めを忘れたキー {ng} 件')
        print('       キーは両方を 0 埋めして作ること。念のため索引そのままの'
              '綴りも登録しておくとよい:')
        print("         keys = [f'{pid.zfill(6)}_{wid.zfill(6)}', "
              "f'{pid}_{wid}', f'{pid.zfill(6)}_{wid}']")
        print('       突合できなかった件数を**必ず報告する**こと。'
              '黙って通すことがこの誤りの核心である。')
    else:
        print(f'{OK} {n_files} ファイルを検査：0 埋めを忘れたキーは無い'
              + (f'（点検 {warn} 件）' if warn else ''))
    return 1 if ng else 0


if __name__ == '__main__':
    raise SystemExit(main())

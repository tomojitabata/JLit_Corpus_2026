#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_unidic_dir.py
===================
展開した UniDic のディレクトリが**本当に使えるか**を確かめる。

なぜ必要か
------------
zip の展開は，途中で失敗しても**それらしいディレクトリを残す**ことがある。
macOS の「アーカイブユーティリティ」は途中までのファイルを置いて終わるので，
一見すると成功したように見える。ところが解析を始めた段階で

    RuntimeError: Failed initializing MeCab …
    param.cpp(69) [ifs] no such file or directory: ./dicrc

のようなエラーで止まる。しかもこのメッセージからは「展開が不完全だった」とは
読み取れないので，辞書の指定が間違っていると誤解して時間を無駄にする。

**展開したら，解析を始める前にここで確かめること。**

検査すること
------------
1. MeCab の辞書に必要なファイルが揃っているか（``dicrc`` ``sys.dic``
   ``unk.dic`` ``char.bin`` ``matrix.bin``）
2. 0 バイトのファイルが無いか（途中で切れた展開の典型）
3. ``fugashi`` が実際に読み込めるか
4. 素性がいくつあり，``lemma`` ``orthBase`` などが取れるか
   （古文・近代語の UniDic は現代語版と素性の並びが違う）

使い方
------
    python3 scripts/check_unidic_dir.py /Users/Shared/jlit/unidic-novel-v202512
    python3 scripts/check_unidic_dir.py /Users/Shared/jlit/*        # まとめて
"""
from __future__ import annotations

import argparse
import os
import sys

OK, WARN, FATAL = '[ok  ]', '[warn]', '[FATAL]'

REQUIRED = ('dicrc', 'sys.dic', 'unk.dic', 'char.bin', 'matrix.bin')
OPTIONAL = ('left-id.def', 'right-id.def', 'pos-id.def', 'rewrite.def',
            'feature.def')
PROBE = '國語の研究をしたり。彼は笑つて云つた。'
FEATURES = ('pos1', 'pos2', 'cType', 'cForm', 'lForm', 'lemma',
            'orth', 'orthBase', 'pron', 'goshu')


def find_dic_root(path: str) -> str:
    """``dicrc`` のある階層を探す。

    zip を展開すると ``unidic-novel-v202512/unidic-novel-v202512/`` のように
    一段深くなることがある。``--dicdir`` に渡すのは ``dicrc`` のある階層で，
    一段ずれていると「辞書が無い」と言われる。**よくある間違いなので，
    黙って直さずに，どこにあったかを報告する。**
    """
    if os.path.exists(os.path.join(path, 'dicrc')):
        return path
    try:
        subs = [os.path.join(path, d) for d in sorted(os.listdir(path))
                if os.path.isdir(os.path.join(path, d))]
    except OSError:
        return path
    for s in subs:
        if os.path.exists(os.path.join(s, 'dicrc')):
            return s
    return path


def human(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.1f}{unit}' if unit != 'B' else f'{n}B'
        n /= 1024
    return str(n)


def check(path: str) -> int:
    print(f'\n{"=" * 70}\n{path}\n{"=" * 70}')
    if not os.path.isdir(path):
        print(f'{FATAL} ディレクトリが無い')
        return 1
    root = find_dic_root(path)
    if root != path:
        print(f'{WARN} dicrc は1段下にあった: {root}')
        print(f'       --dicdir に渡すのは **{root}** のほう。')

    bad = 0
    for fn in REQUIRED:
        p = os.path.join(root, fn)
        if not os.path.exists(p):
            print(f'{FATAL} {fn:<14} が無い → 展開が不完全'); bad += 1
        elif os.path.getsize(p) == 0:
            print(f'{FATAL} {fn:<14} が 0 バイト → 展開が途中で切れている'); bad += 1
        else:
            print(f'{OK} {fn:<14} {human(os.path.getsize(p)):>10}')
    for fn in OPTIONAL:
        p = os.path.join(root, fn)
        if os.path.exists(p):
            print(f'{OK} {fn:<14} {human(os.path.getsize(p)):>10}')
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(root) for f in fs)
    print(f'       合計 {human(total)}')
    if bad:
        print(f'{FATAL} 必要なファイルが {bad} 件欠けている。**展開をやり直すこと。**')
        print('       docs/dictionary_comparison.md の「zip が展開できないとき」を見る。')
        return 1

    # ---- 実際に読み込んでみる ---------------------------------------------
    try:
        import fugashi
    except ImportError:
        print(f'{WARN} fugashi が無いので読み込み試験は飛ばす（pip install fugashi）')
        return 0
    try:
        tagger = fugashi.Tagger(f'-d {root}')
    except Exception as e:                                   # noqa: BLE001
        print(f'{FATAL} fugashi が読み込めない:\n       {str(e)[:400]}')
        print('       ファイルは揃っているのに読めない場合は，**別の版の '
              'MeCab で作られた辞書**の可能性がある。')
        return 1
    words = list(tagger(PROBE))
    print(f'{OK} 読み込めた。試験解析 {len(words)} 語: '
          f'{" / ".join(w.surface for w in words[:12])}')
    f = words[0].feature
    n = len(getattr(f, '_asdict', lambda: {})()) or '不明'
    print(f'       素性の数 {n}')
    have = [k for k in FEATURES if hasattr(f, k)]
    miss = [k for k in FEATURES if not hasattr(f, k)]
    print(f'       取れる素性: {"，".join(have) or "なし"}')
    if miss:
        print(f'{WARN} 取れない素性: {"，".join(miss)}')
        if 'lemma' in miss or 'orthBase' in miss:
            print('       --lemma-policy の既定（mixed）は lemma と orthBase を'
                  '使うので，\n       この辞書では表層形に切り替わる。'
                  '--lemma-policy surface を明示して使うか，\n'
                  '       12_dict_compare.py の警告を読んで方針を決めること。')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dirs', nargs='+', help='展開した辞書のディレクトリ')
    args = ap.parse_args()
    ng = sum(check(d) for d in args.dirs)
    print()
    if ng:
        print(f'{FATAL} {ng} 個の辞書が使えない状態にある。')
    else:
        print(f'{OK} {len(args.dirs)} 個すべて使える。')
    return 1 if ng else 0


if __name__ == '__main__':
    raise SystemExit(main())

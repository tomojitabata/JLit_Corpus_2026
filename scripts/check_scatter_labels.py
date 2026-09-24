#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_scatter_labels.py
=======================
散布図の注記が**別の点を指して見える**事故を，実行しないで検出する。

なぜ要るのか
------------
注記は点の真上には置けない。必ずどこかへずらすので，**ずらした先が
別の点の隣だと，読者はそちらの名前だと読む。** 図は何のエラーも出さずに
出来上がり，誤った読みだけが残る。

事故は3つの経路で起きる。

1. **添字の食い違い** — ``label_points(ax, P[pick,0], P[pick,1], names)``
   のように，座標は絞り込んだのに名前は絞り込んでいない。**この場合，
   図は全部の注記が別人の名前になる。** いちばん重く，いちばん気づかれない
2. **軸を動かす呼び出しの順序** — 注記は表示座標で重なりを避けて置くので，
   置いたあとに ``fig.tight_layout()`` を呼ぶと軸が動き，避けた位置が
   まるごとずれる。``fig.subplots_adjust()`` と ``reserve_right()``
   （面の外の凡例のために右の余白を確保する助手）も同じである
3. **素の annotate** — ``label_points()`` を通さずに ``ax.annotate()`` で
   直接打つと，重なり回避も引き出し線も効かない

1 と 3 は実行しなくても分かる。2 も呼び出しの順序を見れば分かる。
本スクリプトはノートブックのコードセルを読んでこの3つを検査する。

（実行時にしか分からない「別の点のほうが近い」は，``label_points()``
自身が図を描くたびに報告する。こちらはその静的な相棒である。）

使い方
------
    python3 scripts/check_scatter_labels.py --dir notebooks
    python3 scripts/check_scatter_labels.py --dir notebooks --quiet   # 問題だけ
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
import sys

OK, WARN, FATAL = '[ok  ]', '[warn]', '[FATAL]'


def code_cells(path: str) -> list[str]:
    nb = json.load(open(path, encoding='utf-8'))
    return [''.join(c['source']) for c in nb['cells'] if c['cell_type'] == 'code']


def unparse(node) -> str:
    try:
        return ast.unparse(node)
    except Exception:                                        # noqa: BLE001
        return '<式>'


def filter_key(node) -> str | None:
    """式が「どの部分集合を取っているか」を返す。取っていなければ ``None``。

    比べるのは**絞り込みのキー**であって，元の変数名ではない。
    ``P[pick, 0]`` と ``names.values[pick]`` は変数が違っても同じ ``pick``
    で絞っているので整合している。逆に ``P[pick, 0]`` と ``names`` は
    **座標だけを絞って名前を絞っていない**ので，全部の注記が別の作品の
    名前になる。これを捕まえるための関数である。

    ``P[:, 0]``（全件）や ``top.bungo_per10k``（列をまるごと）は
    絞り込みが無いので ``None``。両辺とも ``None`` なら整合とみなす
    （長さの一致は静的には見えないので，``label_points()`` が実行時に見る）。
    """
    if isinstance(node, ast.Subscript):
        sl = node.slice
        parts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
        for p in parts:
            if isinstance(p, ast.Slice):          # [:] は絞り込みではない
                continue
            if isinstance(p, ast.Constant) and isinstance(p.value, int):
                continue                          # 列番号。絞り込みではない
            return unparse(p)
        return filter_key(node.value)
    if isinstance(node, ast.Attribute):
        return filter_key(node.value)
    if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
        # [str(i+1) for i in range(len(top))] は top の行順そのもの
        for gen in node.generators:
            k = filter_key(gen.iter)
            if k:
                return k
        return None
    if isinstance(node, ast.Call):
        f = node.func
        nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', '')
        if nm in ('range', 'len', 'list', 'enumerate'):
            for a in node.args:
                k = filter_key(a)
                if k:
                    return k
        return None
    return None


def check_cell(src: str, where: str, out: list) -> None:
    try:
        tree = ast.parse(src)
    except SyntaxError as e:                                 # noqa: BLE001
        out.append((FATAL, where, f'構文エラー: {e}'))
        return

    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = (f.attr if isinstance(f, ast.Attribute)
                else f.id if isinstance(f, ast.Name) else '')
        # **軸を動かす呼び出しはすべて数える。** tight_layout だけでなく
        # subplots_adjust（および面の外の凡例のために余白を確保する
        # reserve_right）も軸位置を変えるので，注記のあとに呼べば同じ事故
        # になる。名前が違うだけで見逃すのがいちばん危ない。
        if name in ('label_points', 'tight_layout', 'subplots_adjust',
                    'reserve_right', 'annotate', 'scatter',
                    'text', 'save_fig'):
            calls.append((getattr(node, 'lineno', 0), name, node))
    calls.sort()

    has_scatter = any(n == 'scatter' for _, n, _ in calls)
    n_label = sum(1 for _, n, _ in calls if n == 'label_points')

    # ---- 1. 添字の食い違い ------------------------------------------------
    for lineno, name, node in calls:
        if name != 'label_points' or len(node.args) < 4:
            continue
        xs, ys, ts = (unparse(a) for a in node.args[1:4])
        kx, ky, kt = (filter_key(a) for a in node.args[1:4])
        tag = f'{where}:{lineno}'
        if kx and ky and kx != ky:
            out.append((FATAL, tag,
                        f'x と y の絞り込みが違う: {xs} / {ys}'))
        elif kx and kt and kx != kt:
            # 名前だけ絞り込みが違う ＝ 全部が別の作品の名前になる
            out.append((FATAL, tag,
                        f'座標と名前の絞り込みが違う: {xs} ／ 名前 {ts}'))
        elif kx != kt:
            out.append((FATAL, tag,
                        f'座標は {kx} で絞っているのに名前は絞っていない: {ts}'))
        else:
            out.append((OK, tag, f'絞り込みが一致: {kx or "全件（絞り込みなし）"} ← {xs} / {ys} / {ts}'))

    # ---- 2. 軸を動かす呼び出しの順序 ---------------------------------------
    LAYOUT = ('tight_layout', 'subplots_adjust', 'reserve_right')
    layout_calls = [(ln, n) for ln, n, _ in calls if n in LAYOUT]
    last_tl = max([ln for ln, n in layout_calls], default=None)
    last_name = dict(layout_calls).get(last_tl, 'tight_layout')
    first_lp = min([ln for ln, n, _ in calls if n == 'label_points'], default=None)
    if first_lp is not None:
        if last_tl is None:
            out.append((WARN, f'{where}:{first_lp}',
                        'label_points の前に tight_layout が無い'
                        '（軸位置が確定していないと注記がずれる）'))
        elif last_tl > first_lp:
            out.append((FATAL, f'{where}:{last_tl}',
                        f'{last_name} が label_points の**あと**にある。'
                        '軸が動いて注記が点からずれる'))

    # ---- 3. 素の annotate --------------------------------------------------
    for lineno, name, node in calls:
        if name != 'annotate' or not has_scatter:
            continue
        first = unparse(node.args[0]) if node.args else ''
        if first.strip() in ("''", '""'):
            continue          # 矢印だけの注記（中央値の軌跡など）は対象外
        out.append((WARN, f'{where}:{lineno}',
                    f'label_points を通さない annotate がある: {first}'
                    '（重なり回避も引き出し線も効かない）'))

    if has_scatter and not n_label:
        out.append((OK, where, '散布図（注記なし）'))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default='notebooks')
    ap.add_argument('--quiet', action='store_true', help='問題のある行だけ出す')
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, '*.ipynb')))
    if not files:
        sys.exit(f'ノートブックが無い: {args.dir}')

    out: list = []
    for f in files:
        for i, src in enumerate(code_cells(f), start=1):
            if 'scatter(' not in src and 'label_points(' not in src:
                continue
            check_cell(src, f'{os.path.basename(f)} セル{i}', out)

    n_fatal = sum(1 for lv, *_ in out if lv == FATAL)
    n_warn = sum(1 for lv, *_ in out if lv == WARN)
    for lv, where, msg in out:
        if args.quiet and lv == OK:
            continue
        print(f'{lv} {where}\n        {msg}')

    print()
    if n_fatal:
        print(f'{FATAL} 誤読を生む箇所が {n_fatal} 件ある。直すこと。')
    elif n_warn:
        print(f'{WARN} 目視で確かめる箇所が {n_warn} 件（誤読は検出されず）。')
    else:
        print(f'{OK} 注記の付いた散布図 '
              f'{sum(1 for lv, _, m in out if "絞り込みが一致" in m)} 件すべてで'
              '添字が一致し，tight_layout の順序も正しい。')
    return 1 if n_fatal else 0


if __name__ == '__main__':
    raise SystemExit(main())

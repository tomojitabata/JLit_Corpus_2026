#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_interactive.py
====================
対話版の図（``save_interactive``）を**実際に実行して**検査する。

なぜ必要か
------------
対話版の HTML は「点の位置」「吹き出しの中身」「表の行」の3つを別々の
経路で作る。**どれかがずれても図は出来上がり，エラーも出ない。**
指した点と出る語が食い違うだけである。読んだ人が気づくまで分からない。

しかも点が1万個ある図（Step 5 §4 の語彙のギャラクシー）では，HTML を
軽くするために

  * 見出し（``品詞`` ``頻度`` …）は**全点で同じなら1回だけ**書く
  * 近傍語の並びは書かず，``links`` の先の語を**JS 側で組む**

という省略をしている。この省略は速さのためだが，**省略の実装を壊すと
吹き出しが空になる**。ノートブックを配る前に一度実行して確かめること。

検査すること
------------
1. 見出しが揃うとき ``keys`` が1回だけ書かれ，点は値の並び（``v``）を持つ
2. 見出しが点ごとに違うときは ``fields`` を各点に残す（省略しない）
3. 面が2つある図（Step 7 の doc2vec）で，``links`` が**同じ面**を指す
4. 表の行が論理点のぶんだけ（面の数だけ重複しない）
5. ``table_idx`` が重複と範囲外を除き，件数を HTML に明記する
6. ``coords``（面ごとの座標）で，主成分分析と UMAP のように**面ごとに
   座標系が違う**図でも，2面めのポインタの判定範囲がその面の座標で置かれる
6. プロジェクトの外の入力から描いた図に**赤字の警告**が出る
7. （``--browser``）実際のブラウザで吹き出し・近傍の線・検索が動く

使い方
------
    python3 scripts/check_interactive.py
    python3 scripts/check_interactive.py --browser    # Playwright があれば

``--browser`` は Chromium を使う。入っていなければ 7 だけを飛ばす
（受講生のマシンでは必要ない。図を作り直したときにこちらで確かめる）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

OK, WARN, NG = '[ok  ]', '[warn]', '[NG  ]'

ROOT = Path(__file__).resolve().parent.parent


def load_preamble(outdir: Path) -> dict:
    """ノートブックの先頭セル（共通の準備）を読み込んで名前空間を返す。

    **補助関数の定義を二重に持たない。** 検査するのは配布物そのもので
    なければ意味がないので，ノートブックから取り出して実行する。
    """
    import matplotlib
    matplotlib.use('Agg')
    nbp = ROOT / 'notebooks' / '05_word2vec_basics.ipynb'
    if not nbp.exists():
        print(f'{NG} {nbp} が無い。先に make_notebooks.py を実行すること。')
        raise SystemExit(1)
    nb = json.loads(nbp.read_text(encoding='utf-8'))
    src = [''.join(c['source']) for c in nb['cells'] if c['cell_type'] == 'code']
    ns: dict = {'__name__': '__check_interactive__'}
    cwd = os.getcwd()
    os.chdir(ROOT / 'notebooks')          # ROOT の自動検出をこのリポジトリに
    try:
        exec(compile(src[0], 'preamble', 'exec'), ns)
    finally:
        os.chdir(cwd)
    ns['OUT'] = outdir
    return ns


def payload(path: Path) -> tuple[dict, str]:
    s = path.read_text(encoding='utf-8')
    m = re.search(r'id="pts-data">(.*?)</script>', s, re.S)
    if not m:
        raise AssertionError('点の JSON が HTML に無い')
    raw = json.loads(m.group(1))
    if isinstance(raw, list):             # 古い形（配列だけ）
        raw = {'keys': [], 'nhead': '', 'linknotes': False, 'pts': raw}
    return raw, s


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--browser', action='store_true',
                    help='Playwright で実際に動かす（7）')
    ap.add_argument('--keep', action='store_true', help='作った HTML を残す')
    args = ap.parse_args()

    import numpy as np
    tmp = Path(tempfile.mkdtemp(prefix='jlit_interactive_'))
    ns = load_preamble(tmp)
    plt = ns['plt']
    save_interactive = ns['save_interactive']
    INSIDE = ns['ROOT']                   # 「プロジェクト内」と判定される綴り
    ng = 0

    def chk(cond, label):
        nonlocal ng
        if cond:
            print(f'{OK} {label}')
        else:
            ng += 1
            print(f'{NG} {label}')

    xs = np.arange(5.0)
    ys = np.arange(5.0) ** 1.5
    tips = [{'term': f'語{i}', 'fields': [('頻度', i * 10), ('時代', '明治')],
             'notes': ('近傍:', f'あ い う {i}')} for i in range(5)]

    # ---- 1) 見出しの共通化 ------------------------------------------------
    fig, ax = plt.subplots()
    ax.scatter(xs, ys)
    _, html = save_interactive(fig, ax, 'chk1', xs, ys, tips, source=INSIDE)
    plt.close(fig)
    raw, s1 = payload(Path(html))
    chk(raw['keys'] == ['頻度', '時代'] and raw['nhead'] == '近傍:'
        and raw['pts'][0].get('v') == ['0', '明治']
        and raw['pts'][0].get('n') == 'あ い う 0'
        and 'fields' not in raw['pts'][0],
        '1) 見出しは1回だけ書かれ，点は値の並びを持つ')
    chk(len(re.findall(r'<tr data-i=', s1)) == 5, '4) 表の行は点のぶんだけ')
    chk('<p class="danger">' not in s1,
        '6a) プロジェクト内の入力では警告を出さない')

    # ---- 2) 面が2つ・links -----------------------------------------------
    fig, axes = plt.subplots(1, 2)
    for a in axes:
        a.scatter(xs, ys)
    tips2 = [{'term': f'作品{i}', 'fields': [('著者', 'X')],
              'notes': ('最近傍:', None), 'links': [(i + 1) % 5, (i + 2) % 5]}
             for i in range(5)]
    _, html = save_interactive(fig, list(axes), 'chk2', xs, ys, tips2,
                               id_col='作品', source=INSIDE)
    plt.close(fig)
    raw, s2 = payload(Path(html))
    chk(len(raw['pts']) == 10
        and [p['r'] for p in raw['pts']] == [0, 1, 2, 3, 4] * 2
        and raw['pts'][5]['links'] == [6, 7],
        '3) 2面の図で links が同じ面を指す')
    chk(raw['linknotes'] is True and all('n' not in p for p in raw['pts']),
        '2) 近傍語の並びは書かず JS が組む（links から）')
    chk(len(re.findall(r'<tr data-i=', s2)) == 5,
        '4) 2面でも表は論理点のぶんだけ')

    # ---- 面ごとに座標が違う図（主成分分析と UMAP の比較）------------------
    # **ここを間違えると，2面めで指した点と出る語が食い違う。**
    fig, axes = plt.subplots(1, 2)
    xs2, ys2 = xs[::-1] * 2.0, ys[::-1] + 1.0      # 別の座標系に見立てる
    axes[0].scatter(xs, ys)
    axes[1].scatter(xs2, ys2)
    tipsc = [{'term': f'語{i}', 'fields': [('頻度', i)]} for i in range(5)]
    _, html = save_interactive(fig, list(axes), 'chk6', xs, ys, tipsc,
                               coords=[(xs, ys), (xs2, ys2)], source=INSIDE)
    plt.close(fig)
    raw, s6 = payload(Path(html))
    nlog = len(tipsc)
    same = sum(1 for i in range(nlog)
               if abs(raw['pts'][i]['x'] - raw['pts'][i + nlog]['x']) < 1e-9
               and abs(raw['pts'][i]['y'] - raw['pts'][i + nlog]['y']) < 1e-9)
    chk(len(raw['pts']) == 2 * nlog and same == 0,
        f'8a) 面ごとの座標（coords）が面ごとに置かれる'
        f'（2面で同じ位置になった点 {same}／0 が正しい）')
    fig, axes = plt.subplots(1, 2)
    try:
        save_interactive(fig, list(axes), 'chk7', xs, ys, tipsc,
                         coords=[(xs, ys)], source=INSIDE)
        chk(False, '8b) coords の数が面と違えば例外')
    except ValueError:
        chk(True, '8b) coords の数が面と違えば例外')
    finally:
        plt.close(fig)

    # ---- 見出しが点ごとに違う経路 ----------------------------------------
    fig, ax = plt.subplots()
    ax.scatter(xs, ys)
    tips3 = [{'term': f'点{i}',
              'fields': [('あ', 1)] if i % 2 else [('い', 2), ('う', 3)]}
             for i in range(5)]
    _, html = save_interactive(fig, ax, 'chk3', xs, ys, tips3,
                               source='/tmp/fake', table_cols=['あ', 'い', 'う'])
    plt.close(fig)
    raw, s3 = payload(Path(html))
    chk(raw['keys'] == [] and 'fields' in raw['pts'][0],
        '2) 見出しが揃わないときは各点に残す')
    chk('<p class="danger">' in s3,
        '6b) プロジェクト外の入力には赤字の警告が出る')

    # ---- table_idx --------------------------------------------------------
    fig, ax = plt.subplots()
    ax.scatter(xs, ys)
    _, html = save_interactive(fig, ax, 'chk4', xs, ys, tips, source=INSIDE,
                               table_idx=[3, 1, 1, 99, -1])
    plt.close(fig)
    raw, s4 = payload(Path(html))
    chk(re.findall(r'<tr data-i="(\d+)">', s4) == ['3', '1']
        and '表は 2 件（図の点は 5 件' in s4,
        '5) table_idx は重複と範囲外を除き，件数を明記する')

    # ---- 長さの不一致は必ず例外にする ------------------------------------
    fig, ax = plt.subplots()
    ax.scatter(xs, ys)
    try:
        save_interactive(fig, ax, 'chk5', xs, ys, tips[:3], source=INSIDE)
        chk(False, '0) 座標と情報の数が違えば例外を投げる')
    except ValueError:
        chk(True, '0) 座標と情報の数が違えば例外を投げる')
    finally:
        plt.close(fig)

    # ---- 7) 実際のブラウザ -------------------------------------------------
    if args.browser:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print(f'{WARN} 7) Playwright が無いので飛ばす'
                  '（pip install playwright）')
        else:
            p1 = tmp / 'chk1.html'
            raw, _ = payload(p1)
            tgt = raw['pts'][2]
            errs: list[str] = []
            with sync_playwright() as pw:
                exe = os.environ.get('JLIT_CHROMIUM', '/opt/pw-browsers/chromium')
                b = (pw.chromium.launch(executable_path=exe)
                     if Path(exe).exists() else pw.chromium.launch())
                pg = b.new_page(viewport={'width': 1200, 'height': 900})
                pg.on('pageerror', lambda e: errs.append(str(e)))
                pg.goto(p1.as_uri())
                pg.wait_for_timeout(500)
                box = pg.locator('#hit').bounding_box()
                pg.mouse.move(box['x'] + tgt['x'] * box['width'],
                              box['y'] + tgt['y'] * box['height'])
                pg.wait_for_timeout(250)
                tip = pg.locator('#tip').inner_text()
                pg.fill('#q', '語3')
                pg.wait_for_timeout(250)
                cnt = pg.locator('#count').inner_text()
                marks = pg.locator('#marks circle').count()
                b.close()
            chk(not errs, f'7a) JS エラーが出ない（{errs[:1]}）')
            chk(tgt['term'] in tip and '近傍' in tip and '頻度' in tip,
                '7b) 吹き出しに語・見出し・近傍が出る')
            chk('1 件' in cnt and marks == 1,
                '7c) 検索が表と図のマーカーの両方に効く')

    print()
    if ng:
        print(f'{NG} 対話版の図に {ng} 件の食い違いがある。'
              '**配る前に直すこと。**指した点と出る語が違う図は，'
              '読んだ人が気づくまで分からない。')
    else:
        print(f'{OK} 対話版の図の経路はすべて整合している。')
    if args.keep:
        print(f'       作った HTML: {tmp}')
    else:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if ng else 0


if __name__ == '__main__':
    sys.exit(main())

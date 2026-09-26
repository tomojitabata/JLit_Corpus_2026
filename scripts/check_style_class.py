#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_style_class.py
====================
``style_class``（文語体／過渡的文体／口語体）の**測り方そのもの**を点検する。
何も書き換えない。数えて並べるだけである。

なぜ要るのか
------------
``style_class`` は文語標識と口語標識の**比**で決めている。

    bungo_ratio = 文語 / (文語 + 口語)

比は分母が小さいと暴れる。2026-09-27 に，分母（文語＋口語の出現密度）が
小さい順に6点並べると**6点とも「B_過渡的文体」**になっていることが
分かった。しかもその6点の文語密度は 13〜27／万語で，119点の中央値 26.5 を
**下回る**。文語が濃いのではなく，**口語標識が数えられていない**。

    林芙美子『晩菊』 文語 16.1  口語 197.3  比 0.075  C_口語体
    林芙美子『浮雲』 文語 17.8  口語  55.3  比 0.244  B_過渡的文体

同じ作者，文語密度はほぼ同じ。動いたのは分母だけである。

原因は口語標識の取り方にある。現行の ``KOGO`` には**「だ」の終止形も
「た」も入っていない**。「だ」止め・体言止めで書かれた作品——林芙美子
『浮雲』の乾いた文体，長塚節『土』の方言，岸田国士『紙風船』の戯曲——は，
文語でも口語でもない扱いになり，比が跳ね上がる。

このスクリプトは，口語標識を足したときに何が起きるかを**先に見る**ための
ものである。閾値は分布を見てから決める。決めてから測るのではない。

    python3 scripts/check_style_class.py \\
        --tokens data/tokens/tokens_surface \\
        --meta metadata/corpus_metadata_v3_local.csv

    # 標識を自分で足して試す
    python3 scripts/check_style_class.py --add だ,た,ない,なかっ,だろ

⚠ **これは測定法の変更の下見である。** 採用すると決めたら
``00_build_metadata_v2.py`` の ``KOGO`` と ``style_class`` の閾値を直し，
``00_extend_metadata.py`` を回し直す。docstring の言うとおり，
**閾値を変えたら ``bungo_ratio`` 列とともに報告すること。**
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 追加を検討する口語標識。**文語標識と機能で対になるもの**を選ぶ。
#:
#:   断定    なり・なれ        ↔  だ（である・です・だっ は現行にある）
#:   過去    けり・たり・ぬる  ↔  た（「読んだ」の連濁も同じ表層）
#:   否定    ざる・ざり・ざれ  ↔  ない・なかっ
#:   推量    べし・らむ・けむ  ↔  だろ（でしょ は現行にある）
#:
#: 機能の対応しないものは足さない。**分母を大きくするのが目的ではなく，
#: 文語と口語を同じ土俵で比べるのが目的**である。
DEFAULT_ADD = ('だ', 'た', 'ない', 'なかっ', 'だろ')


def load_meta_v2():
    s = importlib.util.spec_from_file_location(
        '_meta_v2', os.path.join(ROOT, 'scripts', '00_build_metadata_v2.py'))
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def default_meta() -> str:
    base = os.path.join(ROOT, 'metadata')
    for name in ('corpus_metadata_v3_local.csv', 'corpus_metadata_v3.csv',
                 'corpus_metadata_v2.csv'):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return ''


def stem_of(r: dict) -> str:
    return (f"{str(r.get('aozora_person_id', '')).zfill(6)}_"
            f"{str(r.get('aozora_work_id', '')).zfill(6)}")


def quantiles(xs: list[float]) -> str:
    xs = sorted(xs)
    if not xs:
        return '—'
    def q(p):
        return xs[min(len(xs) - 1, int(p * len(xs)))]
    return (f'最小 {xs[0]:.3f}／25% {q(.25):.3f}／中央 {statistics.median(xs):.3f}'
            f'／75% {q(.75):.3f}／最大 {xs[-1]:.3f}')


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tokens', default='data/tokens/tokens_surface',
                    help='**表層形**の分かち書き列。lemma を渡すと値が変わる')
    ap.add_argument('--meta', default=None, help='既定: *_v3_local > v3 > v2')
    ap.add_argument('--add', default=','.join(DEFAULT_ADD),
                    help='口語標識に足す語形（カンマ区切り）')
    ap.add_argument('--show', type=int, default=12, help='並べる作品数')
    ap.add_argument('--rule', default=None, metavar='文語密度,比',
                    help='二変数のルールを試す。例 --rule 90,0.06 なら'
                         '「文語密度 ≧ 90 は A／比 ≦ 0.06 は C／残りは B」。'
                         '現行の分類との混同表と，動く作品の一覧を出す')
    args = ap.parse_args()

    m = load_meta_v2()
    meta_path = args.meta or default_meta()
    if not meta_path or not os.path.exists(meta_path):
        sys.exit(f'メタデータが無い: {meta_path}')
    if not os.path.isdir(args.tokens):
        sys.exit(f'トークン列のディレクトリが無い: {args.tokens}\n'
                 '         05_tokenise_unidic.py を先に走らせること。')

    add = [x.strip() for x in args.add.split(',') if x.strip()]
    kogo_new = list(m.KOGO) + [x for x in add if x not in m.KOGO]
    print(f'[meta] {meta_path}')
    print(f'[語形] {args.tokens}')
    print(f'[標識] 文語 {len(m.BUNGO)} 種'
          f'／口語 現行 {len(m.KOGO)} 種 → 案 {len(kogo_new)} 種'
          f'（足す: {"・".join(add)}）\n')

    with open(meta_path, encoding='utf-8-sig') as fh:
        rows = [r for r in csv.DictReader(fh)
                if r.get('completeness') not in
                ('merged', 'superseded', 'too_short')]

    out = []
    for r in rows:
        p = os.path.join(args.tokens, stem_of(r) + '.txt')
        if not os.path.exists(p):
            continue
        toks = open(p, encoding='utf-8').read().split()
        n = max(1, len(toks))
        b = 10000 * m.count_markers(toks, m.BUNGO) / n
        k0 = 10000 * m.count_markers(toks, m.KOGO) / n
        k1 = 10000 * m.count_markers(toks, kogo_new) / n
        out.append({
            'who': f"{r.get('author_ja', '')}『{r.get('title_aozora', '')}』",
            'year': r.get('year_first', ''), 'cls': r.get('style_class', ''),
            'bungo': b, 'kogo0': k0, 'kogo1': k1,
            'den0': b + k0, 'den1': b + k1,
            'r0': m.bungo_ratio(round(b, 1), round(k0, 1)),
            'r1': m.bungo_ratio(round(b, 1), round(k1, 1)),
        })
    if not out:
        sys.exit('トークン列と突き合う行が1つも無い。--tokens を確かめること。')
    print(f'[ok  ] {len(out)} 作品を数えた\n')

    # ---- 1. 分母がどれだけ安定するか -----------------------------------
    d0 = [x['den0'] for x in out]
    d1 = [x['den1'] for x in out]
    def spread(d):
        return f'{max(d) / min(d):.1f} 倍' if min(d) > 0 else '—（分母 0 の作品がある）'

    print('■ 分母（文語＋口語の密度／万語）')
    print(f'   現行  最小 {min(d0):6.1f}  中央 {statistics.median(d0):6.1f}'
          f'  最大 {max(d0):6.1f}   最大/最小 = {spread(d0)}')
    print(f'   案    最小 {min(d1):6.1f}  中央 {statistics.median(d1):6.1f}'
          f'  最大 {max(d1):6.1f}   最大/最小 = {spread(d1)}')
    print('   **分母の開きが小さいほど比は信用できる。**\n')

    # ---- 2. 現行の分類ごとに，新しい比がどこに来るか ---------------------
    print('■ 現行の分類別に見た新しい比')
    print('   境界は，群と群が**重ならなければ**自然に決まる。')
    for c in ('A_文語体', 'B_過渡的文体', 'C_口語体'):
        g = [x for x in out if x['cls'] == c]
        if not g:
            continue
        print(f'   {c:<10} n={len(g):>3}  現行 {quantiles([x["r0"] for x in g])}')
        print(f'   {"":<10}        案   {quantiles([x["r1"] for x in g])}')
    print()

    # ---- 3. 分母の小さい作品がどう動くか --------------------------------
    print(f'■ 分母がいちばん小さい {args.show} 点（**今回の問題の当事者**）')
    print(f'   {"文語":>6} {"口語現":>7} {"口語案":>7} {"比現":>6} {"比案":>6}'
          f'  {"現行の分類":<10} 作品')
    for x in sorted(out, key=lambda x: x['den0'])[:args.show]:
        print(f'   {x["bungo"]:6.1f} {x["kogo0"]:7.1f} {x["kogo1"]:7.1f}'
              f' {x["r0"]:6.3f} {x["r1"]:6.3f}  {x["cls"]:<10} {x["who"]}')
    print()

    # ---- 4. 文語の濃い作品が沈まないか ----------------------------------
    print('■ 文語密度の高い 8 点（**ここが下がりすぎては困る**）')
    print(f'   {"文語":>6} {"比現":>6} {"比案":>6}  {"現行の分類":<10} 作品')
    for x in sorted(out, key=lambda x: -x['bungo'])[:8]:
        print(f'   {x["bungo"]:6.1f} {x["r0"]:6.3f} {x["r1"]:6.3f}'
              f'  {x["cls"]:<10} {x["who"]}')
    print()

    # ---- 4.5 もう一本の軸 — 文語密度 -------------------------------------
    # 比だけで A と B が切れないとき，切っているのは**文語の濃さ**である。
    # 雅俗折衷体（地の文が文語・会話が口語）は口語標識を足すと比が沈むが，
    # 文語密度は沈まない。樋口一葉『たけくらべ』がその典型。
    print('■ 文語密度（／万語）を現行の分類別に')
    for c in ('A_文語体', 'B_過渡的文体', 'C_口語体'):
        g = sorted(x['bungo'] for x in out if x['cls'] == c)
        if not g:
            continue
        print(f'   {c:<10} n={len(g):>3}  最小 {g[0]:6.1f}／中央 '
              f'{statistics.median(g):6.1f}／最大 {g[-1]:6.1f}')
    ga = sorted(x['bungo'] for x in out if x['cls'] == 'A_文語体')
    gb = sorted(x['bungo'] for x in out if x['cls'] == 'B_過渡的文体')
    if ga and gb:
        print(f'   A の最小 {ga[0]:.1f} ／ B の最大 {gb[-1]:.1f}'
              + ('  → **重ならない。この軸なら切れる**'
                 if ga[0] > gb[-1] else '  → 重なる'))
    print()

    print(f'■ 現行の A_文語体 {len([x for x in out if x["cls"] == "A_文語体"])} 点'
          '（少ないので全部並べる）')
    print(f'   {"文語":>6} {"比現":>6} {"比案":>6}  作品')
    for x in sorted((x for x in out if x['cls'] == 'A_文語体'),
                    key=lambda x: -x['bungo']):
        print(f'   {x["bungo"]:6.1f} {x["r0"]:6.3f} {x["r1"]:6.3f}  {x["who"]}')
    print()

    # ---- 4.6 二変数のルールを試す ----------------------------------------
    if args.rule:
        try:
            ta, tc = (float(v) for v in args.rule.split(','))
        except ValueError:
            sys.exit('--rule は「文語密度,比」の形で渡すこと（例 90,0.06）')

        def new_cls(x):
            if x['bungo'] >= ta:
                return 'A_文語体'
            if x['r1'] <= tc:
                return 'C_口語体'
            return 'B_過渡的文体'

        print(f'■ 試すルール: 文語密度 ≧ {ta:g} は A ／ 比 ≦ {tc:g} は C ／ 残りは B')
        order = ('A_文語体', 'B_過渡的文体', 'C_口語体')
        tab = {(a, b): 0 for a in order for b in order}
        for x in out:
            tab[(x['cls'], new_cls(x))] += 1
        print(f'   {"現行＼案":<12}' + ''.join(f'{c:>12}' for c in order))
        for a in order:
            print(f'   {a:<12}' + ''.join(f'{tab[(a, b)]:>12}' for b in order))
        moved = [x for x in out if new_cls(x) != x['cls']]
        print(f'\n   分類が変わる作品 {len(moved)} 点')
        for x in sorted(moved, key=lambda x: -x['bungo']):
            print(f'   {x["bungo"]:6.1f} 比案 {x["r1"]:6.3f}  '
                  f'{x["cls"]:<10} → {new_cls(x):<10} {x["who"]}')
        print()

    # ---- 5. 境界の候補 ---------------------------------------------------
    A = [x['r1'] for x in out if x['cls'] == 'A_文語体']
    C = [x['r1'] for x in out if x['cls'] == 'C_口語体']
    B = [x['r1'] for x in out if x['cls'] == 'B_過渡的文体']
    print('■ 境界の候補（現行の分類を手がかりにした目安）')
    if A and B:
        print(f'   A と B の間: A の最小 {min(A):.3f} ／ B の最大 {max(B):.3f}'
              + ('  → 重ならない' if min(A) > max(B) else '  → **重なる**'))
    if B and C:
        print(f'   B と C の間: B の最小 {min(B):.3f} ／ C の最大 {max(C):.3f}'
              + ('  → 重ならない' if min(B) > max(C) else '  → **重なる**'))
    print('   重なるなら，比だけでは切れないということである。'
          '\n   その場合は絶対値の条件を併用するか，判定を欠測にする。')
    print('\n   **この出力を見てから閾値を決めること。**'
          ' 決めたら 00_build_metadata_v2.py の KOGO と style_class を直し，'
          '\n   00_extend_metadata.py を回し直して bungo_ratio 列とともに報告する。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

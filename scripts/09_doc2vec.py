#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
09_doc2vec.py
=============
doc2vec（Paragraph Vector）による作品・チャンクの表現学習と，
メタデータとの関係づけ。

何を見るか
----------
1. **チャンクの document vectors** を学習し，作品ベクトルをそのチャンク平均として定義する。
   （作品を 1 文書として学習すると，長篇ほど学習量が多くなり比較が歪む）
2. document vector の空間で，メタデータの各軸（時代・ジャンル・語り・register）が
   どれだけ説明力を持つかを **シルエット係数**と**最近傍一致率**で測る。
3. 作品間類似度から，**同一作家の作品どうしが近いか（作家効果）**と
   **同一時代の作品どうしが近いか（時代効果）**を比較する。
   作家効果が時代効果を上回る場合，通時的な主張は慎重にすべきである。

使い方
------
    python3 09_doc2vec.py --chunks data/datasets/chunks \\
        --index data/datasets/chunks_index.csv --out results/d2v \\
        --dim 200 --epochs 40 --dm 0

依存
----
    pip install gensim numpy scikit-learn
"""
from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict

import numpy as np

try:
    from gensim.models.doc2vec import Doc2Vec, TaggedDocument
except ImportError:                                            # pragma: no cover
    raise SystemExit('gensim が必要である:  pip install gensim')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--chunks', required=True)
    ap.add_argument('--index', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--dim', type=int, default=300)
    # window は word2vec と揃える。**PV-DBOW では語ベクトルの
    # 学習にしか効かない**ので，作品ベクトルへの影響は小さい。
    ap.add_argument('--window', type=int, default=3)
    ap.add_argument('--min-count', type=int, default=10)
    ap.add_argument('--epochs', type=int, default=40)
    ap.add_argument('--dm', type=int, default=0, help='0=PV-DBOW（推奨）, 1=PV-DM')
    ap.add_argument('--seed', type=int, default=11)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    with open(args.index, encoding='utf-8-sig') as fh:
        index = list(csv.DictReader(fh))

    docs, meta = [], {}
    for r in index:
        p = os.path.join(args.chunks, r['chunk_id'] + '.txt')
        if not os.path.exists(p):
            continue
        toks = open(p, encoding='utf-8').read().split()
        docs.append(TaggedDocument(toks, [r['chunk_id']]))
        meta[r['chunk_id']] = r
    print(f'[fit ] {len(docs):,} チャンク / PV-{"DBOW" if args.dm == 0 else "DM"}')

    model = Doc2Vec(vector_size=args.dim, window=args.window,
                    min_count=args.min_count, dm=args.dm, workers=4,
                    epochs=args.epochs, seed=args.seed, sample=1e-4, negative=5)
    model.build_vocab(docs)
    model.train(docs, total_examples=len(docs), epochs=args.epochs)
    model.save(os.path.join(args.out, 'doc2vec.model'))
    print(f'       語彙 {len(model.wv):,} 語')

    # ---- チャンク → 作品 --------------------------------------------------
    by_work = defaultdict(list)
    for cid in meta:
        by_work[meta[cid]['work_stem']].append(model.dv[cid])
    works = sorted(by_work)
    W = np.vstack([np.mean(by_work[w], axis=0) for w in works])
    Wn = W / (np.linalg.norm(W, axis=1, keepdims=True) + 1e-12)

    wmeta = {}
    for r in index:
        wmeta.setdefault(r['work_stem'], r)

    with open(os.path.join(args.out, 'work_vectors.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        # 図を描く側が必要とする列は，ここで全部書いておく。書き落とすと
        # ノートブックが AttributeError で止まる。chunks_index.csv に
        # 載っている属性は迷わず載せる（200次元のベクトルに比べれば容量は無視できる）。
        cols = ['work_stem', 'id', 'author_ja', 'author_sex', 'title',
                'year_first', 'period', 'genre_main', 'genre_sub',
                'audience', 'narration', 'register_level', 'style_class']
        w.writerow(cols + [f'd{i}' for i in range(args.dim)])
        for i, s in enumerate(works):
            m = wmeta[s]
            w.writerow([s] + [m.get(c, '') for c in cols[1:]]
                       + [f'{x:.6f}' for x in W[i]])

    # ---- 作品間類似度 -----------------------------------------------------
    S = Wn @ Wn.T
    with open(os.path.join(args.out, 'work_similarity.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow([''] + works)
        for i, s in enumerate(works):
            w.writerow([s] + [f'{x:.5f}' for x in S[i]])

    # ---- 作家効果 vs 時代効果 ---------------------------------------------
    def within_between(field: str):
        lab = np.array([wmeta[s].get(field, '') for s in works])
        win, bet = [], []
        for i in range(len(works)):
            for j in range(i + 1, len(works)):
                (win if lab[i] == lab[j] else bet).append(S[i, j])
        if not win or not bet:
            return None
        return float(np.mean(win)), float(np.mean(bet)), len(win)

    print('\n同一カテゴリ内の平均類似度 vs 異カテゴリ間:')
    eff = []
    for field in ('author_ja', 'period', 'genre_sub', 'narration',
                  'register_level', 'author_sex'):
        r = within_between(field)
        if not r:
            continue
        wi, be, n = r
        eff.append({'field': field, 'within': round(wi, 4), 'between': round(be, 4),
                    'gap': round(wi - be, 4), 'n_within_pairs': n})
        print(f'  {field:<16} 内 {wi:.4f}  外 {be:.4f}  差 {wi - be:+.4f}  (内ペア {n})')
    if eff:
        with open(os.path.join(args.out, 'category_effects.csv'), 'w',
                  newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=list(eff[0].keys()))
            w.writeheader()
            w.writerows(sorted(eff, key=lambda r: -r['gap']))
    else:
        print('  [warn] 同一カテゴリのペアが作れない（作品数が少なすぎる）。'
              'カテゴリ効果の比較は省略する。')

    top = sorted(eff, key=lambda r: -r['gap'])
    if top and top[0]['field'] == 'author_ja':
        print('\n  [warn] 作家効果が最大である。document vector が捉えているのは主として')
        print('         「誰が書いたか」であり，時代差の主張には統制が要る。')
        print('         → 作家をランダム効果に入れる／作家あたり作品数を揃える')

    # ---- 最近傍一致率 -----------------------------------------------------
    print('\n最近傍がカテゴリを共有する割合:')
    rows = []
    if len(works) >= 2:
        for field in ('author_ja', 'period', 'genre_sub', 'narration'):
            lab = [wmeta[s].get(field, '') for s in works]
            hit = 0
            for i in range(len(works)):
                order = np.argsort(-S[i])
                nn = next(j for j in order if j != i)
                hit += (lab[nn] == lab[i])
            rows.append({'field': field, 'nn_accuracy': round(hit / len(works), 4)})
            print(f'  {field:<16} {hit / len(works):.1%}')
    if rows:
        with open(os.path.join(args.out, 'nn_accuracy.csv'), 'w',
                  newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=['field', 'nn_accuracy'])
            w.writeheader()
            w.writerows(rows)

    print(f'\n[ok  ] 出力 → {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

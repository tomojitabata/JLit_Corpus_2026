#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
08_word2vec_diachronic.py
=========================
通時的 word2vec。時代スライスごとに word embeddings を学習し，直交 Procrustes 変換で
共通空間へアラインメントして，語の意味変化を測る。

方法
----
1. **全体モデル**（``all``）を学習し，語彙と初期値の基準を作る。
2. 時代スライスごとにモデルを学習する。初期値を全体モデルで揃えることで，
   同一語のベクトルが偶然かけ離れるのを防ぐ（Kim et al. 2014 の初期化継承）。
3. 直近スライスを基準に **直交 Procrustes 変換**でアラインメントする
   （Hamilton, Leskovec & Jurafsky 2016）。回転のみを許し距離構造を保つ。
4. アラインメント後の空間で，語ごとに隣接時代間のコサイン距離 ``1 - cos`` を計算する。
   これが意味変化の度合いになる。

統制すべき交絡
--------------
* **語彙サイズの差**。スライスごとの語数が違うと，低頻度語のベクトルの質が
  変わり，見かけの「変化」が増える。``--balance`` で各スライスを最小スライスの
  語数に切り揃える。
* **作家効果**。1 スライスが 1 作家に偏ると，その作家の語法が時代変化に見える。
  ``--max-per-author`` でスライス内の作家あたり語数に上限を設ける。
* **乱数**。``--runs``（既定 10）でシードを変えて何度も学習し，**平均とばらつき**
  を出す。``drift_sd`` と変動係数 ``drift_cv`` が大きい語は，乱数を変えると
  値が変わる語である。**安定しない変化は報告しない。**
  シードの並びは ``--seeds``（既定 11 22 33 44 55 66 77 88 99 111）。
  全体モデル（語彙と初期値の基準）は1回だけ学習する。

出力
----
==============================  ==========================================
``semantic_change.csv``         語ごとの変化量。**平均 ± 標準偏差**
``semantic_change_runs.csv``    試行ごとの生の値（上位2000語）
``probe_neighbours.csv``        指定語の近傍。``jaccard_runs`` は試行間の一致
``w2v_provenance.json``         設定と**使ったシードの並び**
``w2v_all.model``               全体モデル
``w2v_<スライス>.model``        スライス別モデル（**第1試行のもの**）
``aligned_*``                   アラインメント後のベクトル・語彙・スライス名（第1試行）
==============================  ==========================================

使い方
------
    python3 08_word2vec_diachronic.py --chunks data/datasets/chunks \\
        --index data/datasets/chunks_index.csv --out results/w2v \\
        --slice period --dim 300 --window 3 --min-count 20 --balance --runs 10

依存
----
    pip install gensim numpy
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from collections import Counter, defaultdict

import numpy as np

try:
    from gensim.models import Word2Vec
except ImportError:                                            # pragma: no cover
    raise SystemExit('gensim が必要です:  pip install gensim')


def load_index(path: str) -> list[dict]:
    with open(path, encoding='utf-8-sig') as fh:
        return list(csv.DictReader(fh))


def gather(chunks_dir: str, rows: list[dict]) -> list[list[str]]:
    sents = []
    for r in rows:
        p = os.path.join(chunks_dir, r['chunk_id'] + '.txt')
        if os.path.exists(p):
            sents.append(open(p, encoding='utf-8').read().split())
    return sents


def balance(rows: list[dict], key: str, max_per_author: int, rng: random.Random):
    """スライス内の作家あたりチャンク数に上限を設ける。"""
    if not max_per_author:
        return rows
    by = defaultdict(list)
    for r in rows:
        by[r.get('author_ja', '')].append(r)
    out = []
    for a, rs in by.items():
        rng.shuffle(rs)
        out.extend(rs[:max_per_author])
    return out


# config/pipeline.yaml の word2vec.seeds と同じ並び。**報告にはこれを書く。**
# 末尾が 110 でなく 111 なのは，この並びをそのまま既定に決めたからである
# （2026-09-22）。勝手に整えないこと。数値の再現に関わる。
DEFAULT_SEEDS = [11, 22, 33, 44, 55, 66, 77, 88, 99, 111]


def seed_list(seeds, runs, first):
    """--runs 回ぶんのシードを決める。

    並びが足りないときは 11 ずつ足して伸ばす。**使ったシードは必ず出力に残す**
    （json と CSV の両方）。シードを書かない結果は再現できない。
    """
    if runs <= 1:
        return [first]
    out = list(seeds or DEFAULT_SEEDS)
    while len(out) < runs:
        out.append(out[-1] + 11)
    return out[:runs]


def procrustes(base: np.ndarray, other: np.ndarray) -> np.ndarray:
    """other を base に合わせる直交行列 R（other @ R ≈ base）を返す。"""
    M = other.T @ base
    U, _, Vt = np.linalg.svd(M)
    return U @ Vt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--chunks', required=True)
    ap.add_argument('--index', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--slice', default='period',
                    help='スライスに使う列（period / genre_main / register_level など）')
    ap.add_argument('--dim', type=int, default=300)
    ap.add_argument('--window', type=int, default=3)
    ap.add_argument('--min-count', type=int, default=20)
    ap.add_argument('--epochs', type=int, default=20)
    ap.add_argument('--sg', type=int, default=1, help='1=skip-gram, 0=CBOW')
    ap.add_argument('--balance', action='store_true',
                    help='各スライスを最小スライスのチャンク数に切り揃える')
    ap.add_argument('--max-per-author', type=int, default=0)
    ap.add_argument('--runs', type=int, default=10,
                    help='シードを変えて何回学習するか（既定 10）。'
                         '安定した変化だけを報告するため。'
                         '時間がないときは 1 にして，**報告にそう書く**')
    ap.add_argument('--min-slice-tokens', type=int, default=50_000,
                    help='この語数に満たないスライスは学習しない。'
                         '小さすぎるスライスの word embeddings は解釈に耐えないため')
    ap.add_argument('--seed', type=int, default=11,
                    help='単発（--runs 1）のときのシード。既定 11')
    ap.add_argument('--seeds', type=int, nargs='*', default=None,
                    help='複数回学習に使うシードの並び。既定は '
                         '11 22 33 44 55 66 77 88 99 111'
                         '（config/pipeline.yaml の word2vec.seeds と同じ）。'
                         '**報告にはこの並びをそのまま書く**')
    ap.add_argument('--probe', nargs='*', default=[
        '女', '男', '心', '家', '国', '学問', '汽車', '電気', '機械', '科学',
        '恋', '愛', '自由', '社会', '労働', '戦争', '神', '自然', '都会', '田舎'],
        help='意味変化を追跡する語（コーパスにないものは自動的に除く）')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    rng = random.Random(args.seed)
    index = load_index(args.index)
    # **スライス名が空のチャンクを落とす。** メタデータの突合が外れると
    # period も slice4 も空文字になる。素朴に by_slice[''] に貯めると，
    # 名無しのスライスが1つ増えて学習され，しかも --balance を付けると
    # **その件数に全スライスが切り詰められる**。時代が分からないものは
    # 時代の分析に入れない。
    unlabelled = [r for r in index
                  if not str(r.get(args.slice, '') or '').strip()]
    if unlabelled:
        works = sorted({r.get('work_stem', '?') for r in unlabelled})
        print(f'[warn] **{args.slice} が空のチャンクが {len(unlabelled):,} 件ある**'
              f'（作品 {len(works)} 点）。スライスから外す:')
        for w in works[:8]:
            print(f'         {w}')
        if len(works) > 8:
            print(f'         …ほか {len(works) - 8} 点')
        print('       06_build_datasets.py の突合漏れである。'
              'meta_unmatched.csv を確認すること。')
        index = [r for r in index
                 if str(r.get(args.slice, '') or '').strip()]
    if not index:
        sys.exit(f'{args.slice} の値を持つチャンクが1件も無い')

    by_slice = defaultdict(list)
    for r in index:
        by_slice[r[args.slice]].append(r)
    slices = sorted(by_slice)

    print('スライス別チャンク数:')
    for s in slices:
        print(f'  {s:<24} {len(by_slice[s]):>5}')

    if args.balance:
        n_min = min(len(v) for v in by_slice.values())
        for s in slices:
            rs = by_slice[s]
            rng.shuffle(rs)
            by_slice[s] = rs[:n_min]
        print(f'\n[bal ] 各スライスを {n_min} チャンクに切り揃えた')
    if args.max_per_author:
        for s in slices:
            by_slice[s] = balance(by_slice[s], args.slice, args.max_per_author, rng)
        print(f'[bal ] 作家あたり上限 {args.max_per_author} チャンク')

    # ---- 全体モデル（基準語彙と初期値）: **1回だけ** ---------------------
    # 全体モデルは語彙と初期値の基準にしか使わないので，試行ごとに
    # 学習し直さない（いちばん大きいモデルなので時間の大半を占める）。
    # 乱数を振るのは**スライス別モデル**である。
    all_sents = gather(args.chunks, index)
    print(f'\n[fit ] 全体モデル: {len(all_sents):,} チャンク（1回だけ）')
    base = Word2Vec(all_sents, vector_size=args.dim, window=args.window,
                    min_count=args.min_count, sg=args.sg, workers=4,
                    epochs=args.epochs, seed=args.seed)
    base.save(os.path.join(args.out, 'w2v_all.model'))
    print(f'       語彙 {len(base.wv):,} 語')

    # ---- スライスごとの文を1回だけ読む ------------------------------------
    sents_by = {}
    for s in slices:
        sents = gather(args.chunks, by_slice[s])
        ntok = sum(len(x) for x in sents)
        if ntok < args.min_slice_tokens:
            print(f'  [skip] {s}: {ntok:,} 語は少なすぎる'
                  f'（下限 {args.min_slice_tokens:,} 語）')
            continue
        sents_by[s] = sents
    if len(sents_by) < 2:
        print('[warn] スライスが 2 つ未満のため通時比較はできない')
        return 0

    keys = sorted(sents_by)
    anchor = keys[-1]                    # 最新スライスを基準にする
    seeds = seed_list(args.seeds, args.runs, args.seed)
    print(f'\n[runs] スライス別モデルを {len(seeds)} 回学習する'
          f'（シード: {", ".join(str(x) for x in seeds)}）')
    if len(seeds) > 1:
        print('       **スライスに割り当てるチャンクは全試行で同じ。**'
              '変わるのは学習の乱数だけである。'
              '\n       乱数以外を一緒に動かすと，何の効果を見ているのか'
              'が分からなくなる。')
    else:
        print('       **1回しか学習していない。** 報告にそう書くこと'
              '（既定は 10 回）。1回の結果だけで意味変化を論じてはいけない。')

    def cos(a, b):
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        return float(a @ b / (na * nb)) if na and nb else 0.0

    probes_want = list(args.probe)
    drift_runs = defaultdict(list)       # 語 → 試行ごとの drift
    step_runs = defaultdict(list)        # 語 → 試行ごとの [隣接時代間の距離]
    nb_runs = defaultdict(list)          # (語, スライス) → 試行ごとの近傍10語
    nb_sims = {}                         # (語, スライス) → 第1試行の類似度
    common = None

    for ri, sd in enumerate(seeds):
        models = {}
        for s in keys:
            m = Word2Vec(vector_size=args.dim, window=args.window,
                         min_count=args.min_count, sg=args.sg, workers=4,
                         epochs=args.epochs, seed=sd)
            m.build_vocab(sents_by[s])
            # 全体モデルの重みを初期値として継承する
            for w in m.wv.index_to_key:
                if w in base.wv:
                    m.wv[w] = base.wv[w]
            m.train(sents_by[s], total_examples=len(sents_by[s]),
                    epochs=args.epochs)
            models[s] = m
            if ri == 0:
                # 保存するのは**第1試行のモデルだけ**（10回ぶんは置かない）。
                # 下流で使うときは「第1試行のモデルである」と明記すること。
                m.save(os.path.join(args.out, f'w2v_{s}.model'))
                ntok = sum(len(x) for x in sents_by[s])
                print(f'  [fit ] {s:<24} {ntok:>10,}語 語彙{len(m.wv):>7,}')

        # ---- Procrustes アラインメント ------------------------------------
        cm = set(models[anchor].wv.index_to_key)
        for k in keys:
            cm &= set(models[k].wv.index_to_key)
        cm = sorted(cm)
        if common is None:
            common = cm
            print(f'\n[align] 全スライス共通語彙 {len(common):,} 語 / 基準 = {anchor}')
            if len(common) < 200:
                print('[warn] 共通語彙が少なすぎる。--min-count を下げるか，'
                      'スライスを粗くすること')
        elif cm != common:
            # 語彙は min_count と入力で決まるので普通は試行によらない。
            # 違ったら黙って進めず，共通部分を取り直して報告する。
            before = len(common)
            common = sorted(set(common) & set(cm))
            print(f'[warn] 試行 {ri + 1} で共通語彙が変わった'
                  f'（{before:,} → {len(common):,} 語）。共通部分で測る。')

        aligned = {}
        A = np.vstack([models[anchor].wv[w] for w in cm])
        for k in keys:
            B = np.vstack([models[k].wv[w] for w in cm])
            aligned[k] = B @ procrustes(A, B)
        pos = {w: i for i, w in enumerate(cm)}

        if ri == 0:
            np.save(os.path.join(args.out, 'aligned_vectors.npy'),
                    np.stack([aligned[k] for k in keys]))
            with open(os.path.join(args.out, 'aligned_vocab.txt'), 'w',
                      encoding='utf-8') as fh:
                fh.write('\n'.join(cm) + '\n')
            with open(os.path.join(args.out, 'aligned_slices.json'), 'w',
                      encoding='utf-8') as fh:
                json.dump(keys, fh, ensure_ascii=False)

        # ---- 変化量 --------------------------------------------------------
        for w in cm:
            i = pos[w]
            steps = [1 - cos(aligned[keys[j]][i], aligned[keys[j + 1]][i])
                     for j in range(len(keys) - 1)]
            drift_runs[w].append(1 - cos(aligned[keys[0]][i],
                                         aligned[keys[-1]][i]))
            step_runs[w].append(steps)

        # ---- 指定語の近傍（試行ごとに取る。一致率を測るため）---------------
        for w in probes_want:
            if w not in pos:
                continue
            i = pos[w]
            for k in keys:
                V = aligned[k]
                v = V[i]
                sims = V @ v / (np.linalg.norm(V, axis=1) * np.linalg.norm(v)
                                + 1e-12)
                top = np.argsort(-sims)[1:11]
                nb_runs[(w, k)].append([cm[j] for j in top])
                if ri == 0:
                    nb_sims[(w, k)] = [float(sims[j]) for j in top]
        if len(seeds) > 1:
            print(f'  [run ] {ri + 1}/{len(seeds)}（シード {sd}）終了')

    # ---- 試行をまとめる ---------------------------------------------------
    # **平均だけを出してはいけない。** ばらつきが大きい語は，
    # 「意味が変化した」と論じてはいけない語である。
    nrun = len(seeds)
    rows = []
    for w in common:
        ds = drift_runs[w]
        if len(ds) < nrun:
            continue                     # 全試行に出なかった語は落とす
        st = np.array(step_runs[w], dtype=float)     # 試行 × 段
        mean_steps = st.mean(axis=0)
        sd_ = float(np.std(ds, ddof=1)) if nrun > 1 else 0.0
        mean_ = float(np.mean(ds))
        rows.append({
            'term': w,
            'drift_first_last': round(mean_, 4),
            'drift_sd': round(sd_, 4),
            'drift_cv': round(sd_ / mean_, 3) if mean_ > 0 else '',
            'drift_min': round(float(np.min(ds)), 4),
            'drift_max': round(float(np.max(ds)), 4),
            'runs': nrun,
            'drift_mean_step': round(float(mean_steps.mean()), 4),
            'drift_max_step': round(float(mean_steps.max()), 4),
            'max_step_at': keys[int(np.argmax(mean_steps)) + 1],
            **{f'step_{keys[j]}→{keys[j + 1]}': round(float(mean_steps[j]), 4)
               for j in range(len(mean_steps))}})
    if not rows:
        print('[warn] 全試行に共通して現れた語が無い。'
              '--min-count を下げるか，スライスを粗くすること')
        return 0
    rows.sort(key=lambda r: -r['drift_first_last'])
    with open(os.path.join(args.out, 'semantic_change.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w_ = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w_.writeheader()
        w_.writerows(rows)
    print(f'\n[ok  ] semantic_change.csv（{nrun} 回の平均。'
          'drift_sd がばらつき）')

    # 試行ごとの生の値も残す。**平均だけでは検算できない。**
    if nrun > 1:
        with open(os.path.join(args.out, 'semantic_change_runs.csv'), 'w',
                  newline='', encoding='utf-8-sig') as fh:
            w_ = csv.writer(fh)
            w_.writerow(['term', 'run', 'seed', 'drift_first_last'])
            for r in rows[:2000]:            # 上位2000語ぶんで十分
                for ri, sd in enumerate(seeds):
                    w_.writerow([r['term'], ri + 1, sd,
                                 round(drift_runs[r['term']][ri], 4)])
        print('[ok  ] semantic_change_runs.csv（試行ごとの生の値・上位2000語）')

    print(f'\n意味変化の大きい語（上位25・{nrun} 回の平均）:')
    for r in rows[:25]:
        flag = ''
        if nrun > 1 and r['drift_cv'] != '' and float(r['drift_cv']) > 0.25:
            flag = '  ⚠ ばらつきが大きい'
        print(f'  {r["term"]:<10} {r["drift_first_last"]:.3f}'
              f' ± {r["drift_sd"]:.3f}  （最大変化 {r["max_step_at"]}）{flag}')
    if nrun > 1:
        shaky = [r for r in rows[:100]
                 if r['drift_cv'] != '' and float(r['drift_cv']) > 0.25]
        print(f'\n上位100語のうち，試行間のばらつきが大きい'
              f'（変動係数 > 0.25）語は {len(shaky)} 語:')
        print('  ' + ' '.join(r['term'] for r in shaky[:30]))
        print('  **この語について「意味が変化した」と論じてはいけない。**'
              '乱数を変えると値が変わるということは，'
              'データがその主張を支えていないということである。')

    # ---- 指定語の近傍の変遷 -----------------------------------------------
    nb_rows = []
    for (w, k), lists in nb_runs.items():
        jac = ''
        if len(lists) > 1:
            pairs = [len(set(a) & set(b)) / len(set(a) | set(b))
                     for i, a in enumerate(lists) for b in lists[i + 1:]]
            jac = round(float(np.mean(pairs)), 3)
        nb_rows.append({'term': w, 'slice': k,
                        'neighbours': ' '.join(lists[0]),
                        'sims': ' '.join(f'{x:.3f}' for x in nb_sims[(w, k)]),
                        'jaccard_runs': jac, 'runs': len(lists)})
    nb_rows.sort(key=lambda r: (probes_want.index(r['term']), r['slice']))
    if nb_rows:
        with open(os.path.join(args.out, 'probe_neighbours.csv'), 'w',
                  newline='', encoding='utf-8-sig') as fh:
            w_ = csv.DictWriter(fh, fieldnames=list(nb_rows[0].keys()))
            w_.writeheader()
            w_.writerows(nb_rows)
        print('\n指定語の近傍（第1試行の並び。jaccard_runs は試行間の一致）:')
        seen = []
        for r in nb_rows:
            if r['term'] in seen:
                continue
            if len(seen) >= 6:
                break
            print(f'  ■ {r["term"]}')
            for q in nb_rows:
                if q['term'] == r['term']:
                    j = f'  [一致 {q["jaccard_runs"]}]' if q['jaccard_runs'] != '' else ''
                    print(f'    {q["slice"]:<24} {q["neighbours"]}{j}')
            seen.append(r['term'])
        if nrun > 1:
            print('  **一致が 0.3 を下回る行の近傍語は解釈に耐えない。**'
                  '乱数を変えると別の語が並ぶということである。')

    # ---- 何をどう数えたかを残す -------------------------------------------
    # **設定が残っていない結果は再現できない。** v1 コーパスの失敗はここに
    # 由来する。シードの並びまで書く。
    with open(os.path.join(args.out, 'w2v_provenance.json'), 'w',
              encoding='utf-8') as fh:
        json.dump({'slice': args.slice, 'slices': keys, 'anchor': anchor,
                   'dim': args.dim, 'window': args.window,
                   'min_count': args.min_count, 'epochs': args.epochs,
                   'sg': args.sg, 'balance': bool(args.balance),
                   'max_per_author': args.max_per_author,
                   'runs': nrun, 'seeds': seeds,
                   'base_model_seed': args.seed,
                   'common_vocab': len(common),
                   'chunks_index': os.path.abspath(args.index)},
                  fh, ensure_ascii=False, indent=2)
    print('[ok  ] w2v_provenance.json（設定とシードの並び）')

    print(f'\n[ok  ] 出力 → {args.out}')
    print('      注意：drift 上位語には低頻度語・固有名詞が混じりやすい。')
    print('      --min-count を上げるか，vocab_stats.csv で頻度を確認して解釈すること。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

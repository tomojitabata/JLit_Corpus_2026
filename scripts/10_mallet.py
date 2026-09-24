#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
10_mallet.py
============
MALLET による LDA トピックモデルの実行と，結果のメタデータ結合。

MALLET 本体は Java 製の外部ツールである。本スクリプトは
``import-dir`` → ``train-topics`` を呼び出し，出力（doc-topics, topic-keys,
diagnostics.xml）を読んで **メタデータと結合した集計**を作る。

なぜメタデータ結合が要るか
--------------------------
``doc-topics.txt`` はチャンク ID と 50 本の確率が並ぶだけの表である。
「どのトピックがどの時代に多いか」「ジャンルとトピックの対応」は，
この表をチャンク索引と結合して初めて出る。v1 の作業では
``doc-topic-mean.csv`` を手で作っていたが，ここでは自動化する。

サブコマンド
------------
``import``   チャンクを ``.mallet`` に変換
``train``    LDA を学習
``report``   出力を読み，時代別・ジャンル別のトピック分布を作る
``sweep``    トピック数を変えて学習し，診断指標を比較する
``all``      import → train → report

使い方
------
    export MALLET=/opt/mallet/bin/mallet
    python3 10_mallet.py all --datasets data/datasets --out results/mallet \\
        --topics 50 --iterations 2000 --stoplist config/stopwords_ja.txt
    python3 10_mallet.py sweep --datasets data/datasets --out results/mallet_sweep \\
        --topic-list 20 30 40 50 60 80
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

import numpy as np


def mallet_bin() -> str:
    p = os.environ.get('MALLET') or shutil.which('mallet')
    if not p:
        sys.exit('MALLET が見つからない。環境変数 MALLET に bin/mallet のパスを設定すること。\n'
                 '  例: export MALLET=/opt/mallet/bin/mallet')
    return p


def run(cmd: list[str]) -> None:
    print('  $', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def cmd_import(args) -> str:
    os.makedirs(args.out, exist_ok=True)
    dest = os.path.join(args.out, 'topic-input.mallet')
    cmd = [mallet_bin(), 'import-dir',
           '--input', os.path.join(args.datasets, 'mallet_input'),
           '--output', dest,
           '--keep-sequence',
           '--token-regex', r'[^\s]+']        # 分かち書き済みなので空白で切る
    if args.stoplist and os.path.exists(args.stoplist):
        cmd += ['--remove-stopwords', '--stoplist-file', args.stoplist]
    run(cmd)
    return dest


def cmd_train(args, inp: str | None = None) -> None:
    inp = inp or os.path.join(args.out, 'topic-input.mallet')
    o = args.out
    os.makedirs(o, exist_ok=True)
    run([mallet_bin(), 'train-topics',
         '--input', inp,
         '--num-topics', str(args.topics),
         '--num-iterations', str(args.iterations),
         '--optimize-interval', str(args.optimize_interval),
         '--optimize-burn-in', '200',
         '--random-seed', str(args.seed),
         '--num-threads', str(args.threads),
         '--output-state', os.path.join(o, 'topic-state.gz'),
         '--output-topic-keys', os.path.join(o, 'topic-keys.txt'),
         '--output-doc-topics', os.path.join(o, 'doc-topics.txt'),
         '--topic-word-weights-file', os.path.join(o, 'topic-word-weights.txt'),
         '--word-topic-counts-file', os.path.join(o, 'word-topic-counts.txt'),
         '--diagnostics-file', os.path.join(o, 'diagnostics.xml'),
         '--num-top-words', '25'])


def read_doc_topics(path: str) -> tuple[list[str], np.ndarray]:
    """MALLET の doc-topics を読む（新旧2形式に対応）。"""
    ids, rows = [], []
    with open(path, encoding='utf-8-sig') as fh:
        for line in fh:
            if line.startswith('#') or not line.strip():
                continue
            f = line.rstrip('\n').split('\t')
            name = os.path.splitext(os.path.basename(f[1]))[0]
            vals = [x for x in f[2:] if x != '']
            try:                                  # 新形式: 確率が topic 順に並ぶ
                probs = [float(x) for x in vals]
            except ValueError:                    # 旧形式: (topic, prob) の対
                probs_d = {int(vals[i]): float(vals[i + 1])
                           for i in range(0, len(vals) - 1, 2)}
                k = max(probs_d) + 1
                probs = [probs_d.get(i, 0.0) for i in range(k)]
            ids.append(name)
            rows.append(probs)
    k = max(len(r) for r in rows)
    M = np.zeros((len(rows), k))
    for i, r in enumerate(rows):
        M[i, :len(r)] = r
    return ids, M


def read_topic_keys(path: str) -> dict[int, tuple[float, str]]:
    out = {}
    with open(path, encoding='utf-8-sig') as fh:
        for line in fh:
            f = line.rstrip('\n').split('\t')
            if len(f) >= 3:
                out[int(f[0])] = (float(f[1]), f[2])
    return out


def read_diagnostics(path: str) -> dict[int, dict]:
    if not os.path.exists(path):
        return {}
    out = {}
    for t in ET.parse(path).getroot():
        out[int(t.get('id'))] = {k: t.get(k) for k in
                                 ('tokens', 'coherence', 'uniform_dist',
                                  'corpus_dist', 'eff_num_words', 'exclusivity')}
    return out


def cmd_report(args) -> None:
    o = args.out
    ids, M = read_doc_topics(os.path.join(o, 'doc-topics.txt'))
    keys = read_topic_keys(os.path.join(o, 'topic-keys.txt'))
    diag = read_diagnostics(os.path.join(o, 'diagnostics.xml'))
    with open(os.path.join(args.datasets, 'chunks_index.csv'),
              encoding='utf-8-sig') as fh:
        idx = {r['chunk_id']: r for r in csv.DictReader(fh)}

    k = M.shape[1]
    print(f'[rep ] {len(ids):,} チャンク × {k} トピック')

    # トピック一覧＋診断
    with open(os.path.join(o, 'topics_summary.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['topic', 'alpha', 'mean_prob', 'top_words',
                    'coherence', 'exclusivity', 'eff_num_words', 'corpus_dist'])
        for t in range(k):
            a, words = keys.get(t, (0.0, ''))
            d = diag.get(t, {})
            w.writerow([t, a, round(float(M[:, t].mean()), 5), words,
                        d.get('coherence', ''), d.get('exclusivity', ''),
                        d.get('eff_num_words', ''), d.get('corpus_dist', '')])

    # メタデータ別の平均トピック確率
    def by(field: str, fname: str):
        # 値が空のチャンク（06 の突合漏れ）は集計に入れない。空文字のまま
        # 1つの群にすると，時代の表に名前の無い行ができ，作図で落ちる。
        groups, blank = defaultdict(list), 0
        for i, cid in enumerate(ids):
            r = idx.get(cid)
            if r:
                g = (r.get(field) or '').strip()
                if not g:
                    blank += 1
                    continue
                groups[g].append(M[i])
        if blank:
            print(f'  [warn] {fname}: {field} が空のチャンク {blank:,} 件を集計から外した'
                  '（06 の meta_unmatched.csv を確認すること）')
        labs = sorted(groups)
        with open(os.path.join(o, fname), 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow([field, 'n_chunks'] + [f'T{t:02d}' for t in range(k)])
            for g in labs:
                arr = np.vstack(groups[g])
                w.writerow([g, len(arr)] + [f'{x:.5f}' for x in arr.mean(axis=0)])
        return {g: np.vstack(groups[g]).mean(axis=0) for g in labs}

    per = by('period', 'topic_by_period.csv')
    by('genre_sub', 'topic_by_genre.csv')
    by('author_ja', 'topic_by_author.csv')
    by('narration', 'topic_by_narration.csv')
    by('register_level', 'topic_by_register.csv')

    # 作品ごとの平均
    wgroups = defaultdict(list)
    for i, cid in enumerate(ids):
        r = idx.get(cid)
        if r:
            wgroups[r['work_stem']].append(M[i])
    with open(os.path.join(o, 'topic_by_work.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['work_stem', 'id', 'author_ja', 'title', 'year_first', 'period',
                    'n_chunks'] + [f'T{t:02d}' for t in range(k)])
        for s in sorted(wgroups):
            r = next(v for v in idx.values() if v['work_stem'] == s)
            arr = np.vstack(wgroups[s])
            w.writerow([s, r['id'], r['author_ja'], r['title'], r['year_first'],
                        r['period'], len(arr)] + [f'{x:.5f}' for x in arr.mean(axis=0)])

    # 時代特徴的なトピック（時代平均 ÷ 全体平均）
    overall = M.mean(axis=0)
    print('\n時代ごとに特徴的なトピック（時代平均 / 全体平均 の上位3本）:')
    lines = []
    for g in sorted(per):
        ratio = per[g] / (overall + 1e-12)
        top = np.argsort(-ratio)[:3]
        for t in top:
            words = keys.get(int(t), (0, ''))[1].split()[:8]
            lines.append({'period': g, 'topic': int(t),
                          'lift': round(float(ratio[t]), 3),
                          'mean_prob': round(float(per[g][t]), 5),
                          'top_words': ' '.join(words)})
            print(f'  {g:<24} T{t:02d} lift={ratio[t]:5.2f}  {" ".join(words)}')
    with open(os.path.join(o, 'topic_lift_by_period.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=list(lines[0].keys()))
        w.writeheader()
        w.writerows(lines)

    print(f'\n[ok  ] 出力 → {o}')
    print('      注意：lift の高いトピックが固有名詞の束である場合，それは')
    print('      「時代の主題」ではなく特定作家の語彙である。topics_summary.csv の')
    print('      exclusivity と coherence を併せて判断すること。')


def cmd_sweep(args) -> None:
    os.makedirs(args.out, exist_ok=True)
    base = argparse.Namespace(**vars(args))
    base.out = args.out
    inp = cmd_import(base)
    rows = []
    for k in args.topic_list:
        sub = os.path.join(args.out, f'k{k}')
        os.makedirs(sub, exist_ok=True)
        a = argparse.Namespace(**vars(args))
        a.out, a.topics = sub, k
        cmd_train(a, inp)
        diag = read_diagnostics(os.path.join(sub, 'diagnostics.xml'))
        if not diag:
            continue
        coh = [float(d['coherence']) for d in diag.values() if d.get('coherence')]
        exc = [float(d['exclusivity']) for d in diag.values() if d.get('exclusivity')]
        rows.append({'topics': k,
                     'mean_coherence': round(float(np.mean(coh)), 4),
                     'mean_exclusivity': round(float(np.mean(exc)), 4)})
        print(f'  k={k:>3}  coherence={rows[-1]["mean_coherence"]:>8.4f}  '
              f'exclusivity={rows[-1]["mean_exclusivity"]:.4f}')
    with open(os.path.join(args.out, 'sweep.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=['topics', 'mean_coherence', 'mean_exclusivity'])
        w.writeheader()
        w.writerows(rows)
    print('\n  coherence は 0 に近いほど良い（負の値。絶対値が小さいほど良い）。')
    print('  exclusivity は高いほどトピックが分離している。両者はトレードオフになる。')


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('import', 'train', 'report', 'sweep', 'all'):
        p = sub.add_parser(name)
        p.add_argument('--datasets', default='data/datasets')
        p.add_argument('--out', required=True)
        p.add_argument('--topics', type=int, default=50)
        p.add_argument('--iterations', type=int, default=2000)
        p.add_argument('--optimize-interval', type=int, default=20)
        p.add_argument('--threads', type=int, default=4)
        p.add_argument('--seed', type=int, default=20260920)
        p.add_argument('--stoplist', default='config/stopwords_ja.txt')
        p.add_argument('--topic-list', nargs='*', type=int,
                       default=[20, 30, 40, 50, 60, 80])
        p.set_defaults(name=name)
    args = ap.parse_args()

    if args.name == 'import':
        cmd_import(args)
    elif args.name == 'train':
        cmd_train(args)
    elif args.name == 'report':
        cmd_report(args)
    elif args.name == 'sweep':
        cmd_sweep(args)
    else:
        inp = cmd_import(args)
        cmd_train(args, inp)
        cmd_report(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

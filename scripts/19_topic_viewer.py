#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
19_topic_viewer.py
==================
**MALLET の結果を，品詞と頻度帯で絞り込みながら読むビューア**（Step 8）。

1 枚の HTML を書く。外部の資源は使わない（ネットワークに接続せず，サーバも要らない）。
ブラウザで開くと，次ができる。

* **品詞**でトピックの上位語を絞る（普通名詞・動詞・形容詞・固有名詞…）
* **頻度帯**で絞る：全体の度数，出現作品の割合，1 作品への集中度
  （集中度が高い語＝その作品にしか出ない語。取りこぼした登場人物名が多い），
  散らばり dp_in（Gries の DP をいちばん濃い時代の中で測ったもの。bursty な語）
* **relevance λ**（Sievert & Shirley 2014）で並べ替える。λ=1 は p(w|t) の順，
  λ を下げるほど**そのトピックに特有の語**が上に来る
* トピックごとに **時代別の割合**・**多い作品と作家**を見る
* 語で検索して，その語を上位に持つトピックを探す
* 選んだトピックと**関連の強いトピック**を一覧する。指標は 5 つから選ぶ：
  語分布のコサイン類似度，Jensen–Shannon divergence，Burrows's Delta，
  Cosine Delta（以上は語分布の近さ），チャンク上の相関（CLR 変換後。共起）
* 複数のモデル（例：内容語すべて／名詞・動詞・形容詞）を切り替えて比べる
* トピックと作品の**ネットワーク**を描く（作品は doc2vec の作品ベクトルかトピック構成で結ぶ）
* **ラベルづけ**：トピックごとの診断資料を依頼文にまとめて生成 AI に渡し，返ってきた JSON を
  取り込む（どの AI でもよい。無料プランで足りる）。AI を使わない仮ラベルも機械的に付ける。
  ラベルは ``topic_labels.json`` に書き出し，ビューアと同じフォルダに置けば次に作るときも読み込む
* 操作マニュアル（``topic_viewer_manual.html``）をビューアと同じフォルダに書き出す。
  画面の「操作マニュアル ↗」と各欄の「？」から別のウィンドウで開く

⚠ **ビューアで語を隠すことと，その語を除いて学習し直すことは違う。**
隠した語もトピックの形成には寄与している。固有名詞が作ったトピックは，
固有名詞を隠しても「その作品のトピック」のままである。本当に除くには
18_pos_select.py で語を選び直して学習し直し，ここで 2 つのモデルを並べる。

入力
----
* MALLET の出力ディレクトリ（10_mallet.py の ``--out``）。
  ``word-topic-counts.txt`` があればそれを，無ければ ``topic-state.gz`` を読む
* ``doc-topics.txt``（チャンク × トピック）。チャンク ID から作品を引く
* メタデータ（作家・題・初出年・時代）
* ``data/tokens/lexicon.tsv``（18_pos_select.py が書く。語の品詞と頻度帯）

使い方
------
    python3 scripts/19_topic_viewer.py \\
        --model 名詞・動詞・形容詞=my_work/results/mallet_nva \\
        --model 内容語すべて=my_work/results/mallet \\
        --out my_work/results/topic_viewer.html
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import re
import shutil
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_meta() -> str:
    base = os.path.join(ROOT, 'metadata')
    for name in ('corpus_metadata_v3_local.csv', 'corpus_metadata_v3.csv',
                 'corpus_metadata_v2.csv'):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(base, 'corpus_metadata_v3.csv')


def load_meta(path: str) -> dict[str, dict]:
    """作品の語幹（000148_000794）→ メタデータの行。0 埋めの揺れを両方登録する。"""
    out = {}
    with open(path, encoding='utf-8-sig') as fh:
        for r in csv.DictReader(fh):
            pid = str(r.get('aozora_person_id') or '').strip()
            wid = str(r.get('aozora_work_id') or '').strip()
            if pid.isdigit() and wid.isdigit():
                out[f'{pid.zfill(6)}_{wid.zfill(6)}'] = r
                out.setdefault(f'{pid}_{wid}', r)
            fv = (r.get('file_v1') or '').strip()
            if fv:
                out.setdefault(os.path.splitext(fv)[0], r)
    return out


def load_lexicon(path: str) -> dict[str, tuple]:
    lex = {}
    if not path or not os.path.exists(path):
        return lex
    with open(path, encoding='utf-8-sig') as fh:
        for r in csv.DictReader(fh, delimiter='\t'):
            lex[r['lemma']] = (r['pos'], int(r['count']), float(r['doc_ratio']),
                               float(r.get('top_work_share') or 0), r.get('top_work', ''),
                               float(r.get('dp_in') or 0), float(r.get('top_author_share') or 0))
    return lex


def read_word_topic(mdir: str) -> tuple[int, dict[str, Counter]]:
    """語 → {トピック: 度数}。word-topic-counts.txt があればそれ，無ければ state。"""
    wt: dict[str, Counter] = defaultdict(Counter)
    k = 0
    p = os.path.join(mdir, 'word-topic-counts.txt')
    if os.path.exists(p):
        with open(p, encoding='utf-8') as fh:
            for line in fh:
                f = line.split()
                if len(f) < 3:
                    continue
                for tc in f[2:]:
                    t, c = tc.split(':')
                    t = int(t)
                    wt[f[1]][t] += int(c)
                    k = max(k, t + 1)
        return k, wt
    p = os.path.join(mdir, 'topic-state.gz')
    if not os.path.exists(p):
        sys.exit(f'{mdir} に word-topic-counts.txt も topic-state.gz も無い')
    with gzip.open(p, 'rt', encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split(' ')
            if len(f) < 6:
                continue
            t = int(f[-1])
            wt[f[-2]][t] += 1
            k = max(k, t + 1)
    return k, wt


def read_doc_topics(path: str) -> tuple[list[str], list[list[float]]]:
    ids, rows = [], []
    with open(path, encoding='utf-8-sig') as fh:
        for line in fh:
            if line.startswith('#') or not line.strip():
                continue
            f = line.rstrip('\n').split('\t')
            name = os.path.splitext(os.path.basename(f[1]))[0]
            vals = [x for x in f[2:] if x != '']
            try:
                probs = [float(x) for x in vals]
            except ValueError:
                d = {int(vals[i]): float(vals[i + 1]) for i in range(0, len(vals) - 1, 2)}
                probs = [d.get(i, 0.0) for i in range(max(d) + 1)]
            ids.append(name)
            rows.append(probs)
    return ids, rows


def read_selection(mdir: str) -> str:
    """学習に使った語の選び方（18 の selection.json）を，分かる範囲で探す。"""
    for cand in (os.path.join(mdir, 'selection.json'),):
        if os.path.exists(cand):
            return json.load(open(cand, encoding='utf-8')).get('name', '')
    return ''


def relatedness(k: int, wt: dict[str, Counter], rows: list[list[float]],
                mfw: int, min_count: int) -> dict:
    """トピック間の関連（K×K）を 5 つの指標で計算する。

    語分布の近さ（そのトピックがどの語でできているか）
      cos     p(w|t) のベクトルのコサイン類似度。大きいほど近い
      jsd     Jensen–Shannon divergence（底 2，0〜1）。小さいほど近い
      delta   度数上位 mfw 語の p(w|t) をトピック間で z 得点にし，
              差の絶対値を平均したもの（Burrows's Delta の考え方）。小さいほど近い
      cdelta  同じ z 得点ベクトルの 1 − コサイン類似度（Cosine Delta）。小さいほど近い
    文書の中での共起
      corr    チャンクごとのトピックの割合を CLR（centred log-ratio）で変換し，
              チャンクをまたいで取った Pearson の相関係数。大きいほど一緒に現れる
              和が 1 という制約は変換後も残るので，相関の平均は −1/(K−1) になる
    """
    import numpy as np
    words = [w for w, c in wt.items() if sum(c.values()) >= min_count]
    C = np.zeros((len(words), k))
    for i, w in enumerate(words):
        for t, n in wt[w].items():
            C[i, t] = n
    tot = C.sum(axis=0)
    tot[tot == 0] = 1
    P = C / tot                                   # p(w|t)，列ごとに和が 1

    nrm = np.linalg.norm(P, axis=0)
    nrm[nrm == 0] = 1
    Pn = P / nrm
    cos = Pn.T @ Pn

    # JSD(p, q) = H(m) − (H(p) + H(q)) / 2，m = (p + q) / 2（底 2）
    def ent(x):
        with np.errstate(divide='ignore', invalid='ignore'):
            return -np.nansum(np.where(x > 0, x * np.log2(x), 0.0), axis=0)
    H = ent(P)
    jsd = np.zeros((k, k))
    for a in range(k):
        m = (P[:, a:a + 1] + P[:, a:]) / 2
        v = ent(m) - (H[a] + H[a:]) / 2
        jsd[a, a:] = v
        jsd[a:, a] = v
    jsd = np.clip(jsd, 0, 1)

    # Delta 系：度数上位 mfw 語。z 得点はトピック（K 個）をまたいで取る
    order = np.argsort(-C.sum(axis=1))[:mfw]
    X = P[order]
    sd = X.std(axis=1, ddof=1, keepdims=True)
    sd[sd == 0] = 1
    Z = ((X - X.mean(axis=1, keepdims=True)) / sd).T   # K × mfw
    delta = np.array([np.abs(Z - Z[a]).mean(axis=1) for a in range(k)])
    zn = np.linalg.norm(Z, axis=1, keepdims=True)
    zn[zn == 0] = 1
    cdelta = 1 - (Z / zn) @ (Z / zn).T

    # 共起：θ は和が 1 の比率データ。1 つのトピックが大きいと他がそろって小さくなり，
    # 見かけの相関が生じるので，CLR（各チャンクで log θ からその平均を引く）にしてから
    # 相関を取る。ただし CLR の和は 0 なので，相関の平均は −1/(K−1) のまま残る
    T = np.maximum(np.array([r + [0.0] * (k - len(r)) for r in rows]), 1e-12)
    L = np.log(T)
    L = L - L.mean(axis=1, keepdims=True)
    with np.errstate(divide='ignore', invalid='ignore'):
        corr = np.nan_to_num(np.corrcoef(L.T))

    r4 = lambda M: [[round(float(x), 4) for x in row] for row in M]
    return {'cos': r4(cos), 'jsd': r4(jsd), 'delta': r4(delta),
            'cdelta': r4(cdelta), 'corr': r4(corr), 'mfw': int(len(order))}


def model_fingerprint(mdir: str) -> str:
    """モデルの指紋。ラベルをモデルに結び付けるのに使う。

    トピックの番号は学習のたびに変わるので，ラベルを番号だけで覚えると，
    学習し直したモデルに別のトピックのラベルが付いてしまう。topic-keys.txt
    （上位語の一覧）の中身から作るので，同じ学習結果なら置き場所が変わっても同じ指紋になる。
    """
    import hashlib
    h = hashlib.sha1()
    for name in ('topic-keys.txt', 'doc-topics.txt'):
        p = os.path.join(mdir, name)
        if os.path.exists(p):
            with open(p, 'rb') as fh:
                h.update(fh.read(4_000_000))
            break
    return h.hexdigest()[:12]


def load_labels(path: str | None) -> dict | None:
    """ビューアの「ラベルづけ」で書き出した topic_labels.json を読む。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as fh:
            d = json.load(fh)
    except (OSError, ValueError) as e:
        print(f'[warn] ラベルのファイルが読めない: {path}（{e}）')
        return None
    n = sum(len(m.get('topics', {})) for m in d.get('models', {}).values())
    print(f'[lab ] ラベル {n} 件（{path}）。指紋の合うモデルにだけ付く')
    return d


def jsd_matrix(P) -> list[list[float]]:
    """行ごとの確率分布どうしの Jensen–Shannon divergence（底 2，0〜1）。"""
    import numpy as np
    P = np.asarray(P, dtype=np.float64)
    P = P / np.maximum(P.sum(axis=1, keepdims=True), 1e-12)

    def ent(x):
        with np.errstate(divide='ignore', invalid='ignore'):
            return -np.nansum(np.where(x > 0, x * np.log2(x), 0.0), axis=-1)
    H = ent(P)
    n = P.shape[0]
    out = np.zeros((n, n))
    for a in range(n):
        m = (P[a] + P[a:]) / 2
        v = ent(m) - (H[a] + H[a:]) / 2
        out[a, a:] = v
        out[a:, a] = v
    out = np.clip(out, 0, 1)
    return [[round(float(x), 4) for x in row] for row in out]


def work_jsd(wmean) -> list[list[float]]:
    """作品のトピック構成（チャンクの θ の平均）どうしの隔たり。作品のネットワークに使う。"""
    return jsd_matrix(wmean)


def load_d2v(d2v_dir: str, meta: dict) -> dict | None:
    """Step 7（09_doc2vec.py）の作品ベクトルを読み，作品間のコサイン類似度を作る。

    作品ベクトルはチャンクの document vector の平均である（09_doc2vec.py の定義）。
    """
    import numpy as np
    p = os.path.join(d2v_dir, 'work_vectors.csv')
    if not os.path.exists(p):
        print(f'[warn] doc2vec の作品ベクトルが無い: {p}（作品のネットワークは'
              'トピック構成だけで作る。Step 7 の 09_doc2vec.py を先に実行すること）')
        return None
    works, vecs = [], []
    with open(p, encoding='utf-8-sig') as fh:
        for r in csv.DictReader(fh):
            s = r['work_stem']
            m = meta.get(s, {})
            dims = sorted((k for k in r if re.fullmatch(r'd\d+', k)), key=lambda k: int(k[1:]))
            vecs.append([float(r[k]) for k in dims])
            works.append([s, r.get('author_ja') or m.get('author_ja', '（メタデータ無し）'),
                          r.get('title') or m.get('title_aozora', s),
                          r.get('year_first') or m.get('year_first', ''),
                          r.get('period') or m.get('period', '') or '不明', 0,
                          r.get('genre_main') or m.get('genre_main', '') or '',
                          r.get('style_class') or m.get('style_class', '') or ''])
    V = np.asarray(vecs, dtype=np.float64)
    V = V / np.maximum(np.linalg.norm(V, axis=1, keepdims=True), 1e-12)
    S = V @ V.T
    print(f'[d2v ] {len(works)} 作品・{V.shape[1]} 次元（{os.path.abspath(d2v_dir)}）')
    return {'dir': os.path.abspath(d2v_dir), 'dim': int(V.shape[1]), 'works': works,
            'sim': [[round(float(x), 4) for x in row] for row in S]}


def read_diag(mdir: str) -> dict:
    """MALLET の diagnostics.xml からトピックごとの coherence と exclusivity を読む（無ければ空）。"""
    p = os.path.join(mdir, 'diagnostics.xml')
    if not os.path.exists(p):
        return {}
    import xml.etree.ElementTree as ET
    coh, exc = {}, {}
    try:
        for t in ET.parse(p).getroot():
            if t.get('id') is None:
                continue
            i = int(t.get('id'))
            for d, k in ((coh, 'coherence'), (exc, 'exclusivity')):
                try:
                    d[i] = round(float(t.get(k)), 4)
                except (TypeError, ValueError):
                    pass
    except (OSError, ET.ParseError):
        return {}
    return {'coherence': coh, 'exclusivity': exc}


def train_info(mdir: str, keys_alpha: list[float]) -> dict:
    """学習の条件（図の書き出しに添える）。

    * ``train_params.json``（10_mallet.py train が書く）：反復回数・seed・ストップリストなど
    * ``topic-state.gz`` の先頭：α（トピックごと）と β
    * ``topic-keys.txt`` の 2 列目：α（topic-state が無いとき）
    """
    info = {}
    p = os.path.join(mdir, 'train_params.json')
    if os.path.exists(p):
        try:
            info['params'] = json.load(open(p, encoding='utf-8'))
        except (OSError, ValueError):
            pass
    alpha, beta = None, None
    sp = os.path.join(mdir, 'topic-state.gz')
    if os.path.exists(sp):
        import gzip
        try:
            with gzip.open(sp, 'rt', encoding='utf-8', errors='replace') as fh:
                for _ in range(4):
                    line = fh.readline()
                    if line.startswith('#alpha'):
                        alpha = [float(x) for x in line.split(':', 1)[1].split()]
                    elif line.startswith('#beta'):
                        beta = float(line.split(':', 1)[1])
        except (OSError, ValueError, EOFError):
            pass
    if not alpha and keys_alpha:
        alpha = keys_alpha
    if alpha:
        info['alpha'] = [round(a, 5) for a in alpha]
        info['alpha_sum'] = round(sum(alpha), 4)
        info['alpha_min'] = round(min(alpha), 4)
        info['alpha_max'] = round(max(alpha), 4)
    if beta is not None:
        info['beta'] = beta
    return info


def build_model(label: str, mdir: str, meta: dict, lex: dict, top: int, min_count: int,
                rel_mfw: int = 500) -> dict:
    k, wt = read_word_topic(mdir)
    ids, rows = read_doc_topics(os.path.join(mdir, 'doc-topics.txt'))
    k = max(k, max(len(r) for r in rows))

    tot_t = [0] * k
    tot_w = {}
    for w, c in wt.items():
        s = sum(c.values())
        tot_w[w] = s
        for t, n in c.items():
            tot_t[t] += n
    N = sum(tot_t)

    # 各トピックの上位 top 語（度数順）。relevance で並べ替えても上位に来うる
    # 語を拾うため，度数の少ない語は min_count で切る
    per_topic = [[] for _ in range(k)]
    for w, c in wt.items():
        if tot_w[w] < min_count:
            continue
        for t, n in c.items():
            per_topic[t].append((n, w))
    words: dict[str, int] = {}
    tw = []
    for t in range(k):
        lst = sorted(per_topic[t], reverse=True)[:top]
        row = []
        for n, w in lst:
            if w not in words:
                words[w] = len(words)
            row.append([words[w], n])
        tw.append(row)
    vocab = []
    for w in words:
        pos, cnt, dr, ws, twk, dpi, aus = lex.get(w, ('不明', 0, 0.0, 0.0, '', 0.0, 0.0))
        vocab.append([w, pos, cnt, round(dr, 3), round(ws, 3), tot_w[w], round(dpi, 3), round(aus, 3)])

    # 文書（チャンク）→ 作品・時代
    chunk_work = [re.sub(r'__\d+$', '', i) for i in ids]
    works = sorted(set(chunk_work))
    widx = {w: i for i, w in enumerate(works)}
    wsum = [[0.0] * k for _ in works]
    wn = [0] * len(works)
    for cw, r in zip(chunk_work, rows):
        i = widx[cw]
        wn[i] += 1
        for t, p in enumerate(r):
            wsum[i][t] += p
    wmean = [[round(x / max(1, wn[i]), 4) for x in wsum[i]] for i in range(len(works))]
    winfo = []
    for s in works:
        m = meta.get(s, {})
        winfo.append([s, m.get('author_ja', '（メタデータ無し）'),
                      m.get('title_aozora', s), m.get('year_first', ''),
                      m.get('period', '') or '不明', wn[widx[s]],
                      m.get('genre_main', '') or '', m.get('style_class', '') or ''])
    periods = sorted({w[4] for w in winfo})
    psum = {p: [0.0] * k for p in periods}
    pn = Counter()
    for cw, r in zip(chunk_work, rows):
        p = winfo[widx[cw]][4]
        pn[p] += 1
        for t, x in enumerate(r):
            psum[p][t] += x
    pmean = [[round(x / max(1, pn[p]), 4) for x in psum[p]] for p in periods]
    prev = [round(sum(r[t] for r in rows) / len(rows), 5) for t in range(k)]

    keys, kalpha = {}, {}
    kp = os.path.join(mdir, 'topic-keys.txt')
    if os.path.exists(kp):
        for line in open(kp, encoding='utf-8-sig'):
            f = line.rstrip('\n').split('\t')
            if len(f) >= 3:
                keys[int(f[0])] = f[2]
                try:
                    kalpha[int(f[0])] = float(f[1])
                except ValueError:
                    pass

    print(f'[mdl ] {label}: {k} トピック・{len(ids):,} チャンク・{len(works)} 作品・'
          f'語 {len(tot_w):,}（うち表示用 {len(vocab):,}）')
    dg = read_diag(mdir)
    return {'label': label, 'dir': os.path.abspath(mdir), 'K': k, 'N': N,
            'topicTotals': tot_t, 'vocab': vocab, 'tw': tw, 'prev': prev,
            'works': winfo, 'workTopic': wmean, 'periods': periods,
            'periodN': [pn[p] for p in periods], 'periodTopic': pmean,
            'keys': [keys.get(t, '') for t in range(k)],
            'fp': model_fingerprint(mdir),
            'nChunks': len(ids), 'nVocab': len(tot_w),
            'train': train_info(mdir, [kalpha[t] for t in sorted(kalpha)]),
            'diag': {key: [dg[key].get(t) for t in range(k)] for key in dg},
            'rel': relatedness(k, wt, rows, rel_mfw, min_count),
            'workJsd': work_jsd(wmean)}


HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JLit トピックビューア</title>
<style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--mut:#6b6b66;--line:#dcdcd5;--card:#fff;--acc:#2f6f9f;--hi:#fff3c4;
--c0:#2f6f9f;--c1:#c0612b;--c2:#3f8f5a;--c3:#8a5aa8;--c4:#b0902f;--c5:#9a9a93}
@media (prefers-color-scheme:dark){:root{--bg:#1b1b1a;--fg:#ecece6;--mut:#a4a49c;--line:#3a3a37;--card:#242422;--acc:#7fb3db;--hi:#4a4220;
--c0:#7fb3db;--c1:#e0925f;--c2:#79c08f;--c3:#b893d0;--c4:#d8bd63;--c5:#8d8d86}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 "Hiragino Sans","Yu Gothic","Noto Sans CJK JP",sans-serif}
header{padding:12px 18px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header h1{font-size:17px;margin:0}
header .note{color:var(--mut);font-size:12px;max-width:760px}
select,input[type=number],input[type=text]{font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:4px;background:var(--card);color:var(--fg)}
.wrap{display:grid;grid-template-columns:270px 1fr;min-height:calc(100vh - 60px)}
aside{border-right:1px solid var(--line);padding:12px 14px;overflow:auto;max-height:calc(100vh - 60px);position:sticky;top:0}
aside h2{font-size:13px;margin:14px 0 6px;color:var(--mut);font-weight:600}
aside label{display:block;font-size:13px}
.pos label{display:flex;gap:6px;align-items:center}
.pos .n{margin-left:auto;color:var(--mut);font-size:11px}
.rng{display:flex;align-items:center;gap:6px}
.rng input[type=range]{flex:1}
.rng output{min-width:42px;text-align:right;font-variant-numeric:tabular-nums}
.hint{color:var(--mut);font-size:11.5px;margin:2px 0 0}
main{padding:12px 16px;overflow:auto}
.bar{display:flex;gap:12px;align-items:center;margin-bottom:10px;flex-wrap:wrap;color:var(--mut);font-size:12.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px 10px;cursor:pointer}
.card:hover{border-color:var(--acc)}
.card.sel{outline:2px solid var(--acc)}
.card.dim{opacity:.35}
.card h3{font-size:13px;margin:0 0 4px;display:flex;justify-content:space-between}
.card h3 span{color:var(--mut);font-weight:400}
.prevbar{height:4px;background:var(--line);border-radius:2px;margin-bottom:6px}
.prevbar i{display:block;height:100%;background:var(--acc);border-radius:2px}
.words{font-size:13px;line-height:1.7}
.words b{font-weight:500}
.words .m{background:var(--hi)}
#detail{margin-top:16px;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:12px 14px}
#detail h2{margin:0 0 6px;font-size:15px}
.cols{display:grid;grid-template-columns:minmax(300px,1fr) minmax(300px,1fr);gap:18px}
@media (max-width:900px){.wrap{grid-template-columns:1fr}aside{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--line)}.cols{grid-template-columns:1fr}}
table{border-collapse:collapse;width:100%;font-size:12.5px}
td,th{padding:3px 6px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--mut);font-weight:500}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--mut);margin:4px 0}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:3px;vertical-align:-1px}
svg text{fill:var(--fg);font-size:11px}
svg .mut{fill:var(--mut)}
.warn{color:var(--c1);font-size:12px}
.copy{color:var(--mut);font-size:11px;margin:24px 0 8px}
#tableview textarea,#labelview textarea{width:100%;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;border:1px solid var(--line);border-radius:6px;padding:8px;background:var(--card);color:var(--fg);box-sizing:border-box}
#labelview .lh{font-size:13px;margin:14px 0 6px}
.ltwrap{overflow:auto;max-height:60vh;border:1px solid var(--line);border-radius:6px}
#ltab input.lin{width:100%;min-width:140px;font:inherit;border:1px solid var(--line);border-radius:4px;padding:2px 5px;background:var(--card);color:var(--fg)}
#ltab td{vertical-align:top}
.filebtn{position:relative;overflow:hidden;border:1px solid var(--line);border-radius:5px;padding:3px 10px;background:var(--card);cursor:pointer;font-size:12px}
.filebtn input{position:absolute;inset:0;opacity:0;cursor:pointer}
.card .clab{font-size:12.5px;font-weight:600;color:var(--acc);margin:-2px 0 4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card .clab.auto{font-weight:400;color:var(--mut)}
.labbox{border:1px solid var(--line);border-left:4px solid var(--acc);border-radius:6px;padding:6px 10px;margin:6px 0 10px;font-size:13px}
.labbox.auto{border-left-color:var(--line)}
.labbox .ty{display:inline-block;border:1px solid var(--line);border-radius:4px;padding:0 5px;font-size:11.5px;margin-left:6px}
.tabs{display:flex;gap:0;margin-bottom:12px;border-bottom:1px solid var(--line);align-items:center}
.tabs button{font:inherit;font-size:13px;padding:6px 14px;border:1px solid transparent;border-bottom:0;background:none;color:var(--mut);cursor:pointer;border-radius:6px 6px 0 0}
.tabs button.on{border-color:var(--line);background:var(--card);color:var(--fg);font-weight:600;margin-bottom:-1px}
.netbar{display:flex;gap:6px 12px;flex-wrap:wrap;align-items:center;font-size:12px;margin-bottom:5px}
.netbar input[type=range]{width:78px}
.netbar select{max-width:190px}
.netbar .fa2opt.off{opacity:.4}
.netbar label{display:flex;gap:5px;align-items:center}
.netbar label[hidden]{display:none}
.netbar .ntools{display:flex;gap:5px;align-items:center}
#nlay{width:200px}
#necol{width:150px}
.netbar output{min-width:2.2em}
.netbar button{font:inherit;font-size:12px;padding:2px 8px;border:1px solid var(--line);border-radius:5px;background:var(--card);color:var(--fg);cursor:pointer}
.netwrap{position:relative;background:var(--card);border:1px solid var(--line);border-radius:6px;overflow:hidden}
#netsvg{display:block;width:100%;height:auto;aspect-ratio:900/620;touch-action:none;cursor:grab}
#netsvg{--nop:1;--eop:1;--nfs:11px}
#netsvg .edge{fill:none;stroke:var(--mut);stroke-linecap:round;opacity:var(--eop)}
#netsvg .edge:hover{stroke:var(--acc)}
#netsvg .node{stroke:var(--card);stroke-width:1.2;cursor:pointer;fill-opacity:var(--nop)}
#netsvg .node.foc{stroke:var(--fg);stroke-width:2.4}
#netsvg .edge.dim,#netsvg .node.dim,#netsvg .nlabel.dim{opacity:.15}
#netsvg .nlabel{font-size:var(--nfs);font-weight:600;pointer-events:none;paint-order:stroke;stroke:var(--card);stroke-width:calc(var(--nfs) * .27);stroke-linejoin:round}
.tip{position:absolute;max-width:270px;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:12px;line-height:1.5;box-shadow:0 2px 8px rgba(0,0,0,.12);pointer-events:none}
#ninfo h3{font-size:13px;margin:12px 0 4px}
#ninfo tr.go{cursor:pointer}
#ninfo tr.go:hover td{background:var(--hi)}
a.manual{margin-left:auto;padding:4px 11px;border:1px solid var(--line);border-radius:6px;background:var(--card);
  color:var(--mut);font-size:12.5px;text-decoration:none;white-space:nowrap}
a.manual:hover{border-color:var(--acc);color:var(--acc)}
a.help-q{display:inline-block;width:1.4em;height:1.4em;line-height:1.4em;text-align:center;border:1px solid var(--line);
  border-radius:50%;font-size:10.5px;font-weight:600;color:var(--mut);text-decoration:none;margin-left:6px;
  vertical-align:1px;background:var(--card)}
a.help-q:hover{border-color:var(--acc);color:var(--acc)}
#rel{margin-top:14px}
#rel .head{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
#rel h3{font-size:13px;margin:0}
#rel tr.go{cursor:pointer}
#rel tr.go:hover td{background:var(--hi)}
#rel td.w{font-size:12.5px}
</style>
</head>
<body>
<header>
  <h1>JLit トピックビューア</h1>
  <label>モデル <select id="model"></select></label><a class="help-q" href="topic_viewer_manual.html#model" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a>
  <a class="manual" href="topic_viewer_manual.html" target="jlit-topic-manual" title="操作マニュアルを別のウィンドウで開く。横に並べて参照しながら使える">操作マニュアル ↗</a>
  <div class="note">上位語を<b>品詞・頻度帯</b>で絞り，<b>λ</b>で並べ替えて読む。
  ⚠ ここで語を隠しても，その語は学習には寄与している。除いて学習し直した結果と比べるには，
  モデルを切り替えること。</div>
</header>
<div class="wrap">
<aside>
  <h2>品詞<a class="help-q" href="topic_viewer_manual.html#pos" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></h2>
  <div class="pos" id="pos"></div>
  <div class="hint">品詞は UniDic の解析結果。人名が普通名詞と解析されることがある（→ 集中度）</div>

  <h2>頻度帯<a class="help-q" href="topic_viewer_manual.html#band" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></h2>
  <label>全体の度数（最小）</label>
  <div class="rng"><input type="range" id="minc" min="0" max="4" step="0.1" value="0"><output id="mincO"></output></div>
  <label>出現作品の割合（最大）</label>
  <div class="rng"><input type="range" id="maxdr" min="0.05" max="1" step="0.05" value="1"><output id="maxdrO"></output></div>
  <div class="hint">多くの作品に出る語（言う・思う・顔）を除く</div>
  <label>1作品への集中度（最大）</label>
  <div class="rng"><input type="range" id="maxws" min="0.2" max="1" step="0.05" value="1"><output id="maxwsO"></output></div>
  <div class="hint">度数のうち1作品が占める割合。高い語はその作品にしか出ない（登場人物名など）</div>
  <label>1作家への集中度（最大）</label>
  <div class="rng"><input type="range" id="maxas" min="0.2" max="1" step="0.05" value="1"><output id="maxasO"></output></div>
  <div class="hint">同じ作家の複数作品に出る人物名（「素子」など）は，作品ではなく作家に集中する</div>
  <label>散らばり dp_in（最大）</label>
  <div class="rng"><input type="range" id="maxdp" min="0.2" max="1" step="0.05" value="1"><output id="maxdpO"></output></div>
  <div class="hint">Gries の DP を，その語がいちばん濃い時代の中で測ったもの。1 に近いほど少数の作品に固まる（bursty）。時代への偏りは不利に扱わない</div>

  <h2>並べ方<a class="help-q" href="topic_viewer_manual.html#lambda" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></h2>
  <label>relevance λ</label>
  <div class="rng"><input type="range" id="lam" min="0" max="1" step="0.05" value="1"><output id="lamO"></output></div>
  <div class="hint">1＝トピック内の確率順。下げるほどそのトピックに特有の語が上に来る（0.6 前後が目安）</div>
  <label>表示する語数 <input type="number" id="nw" min="5" max="50" value="12" style="width:60px"></label>

  <h2>語で探す<a class="help-q" href="topic_viewer_manual.html#search" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></h2>
  <input type="text" id="q" placeholder="例：汽車" style="width:100%">
  <div class="hint">その語を上位（表示語数以内）に持つトピックだけを濃く表示</div>

  <h2>並べ替え<a class="help-q" href="topic_viewer_manual.html#cards" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></h2>
  <select id="sort"><option value="id">トピック番号</option><option value="prev">割合の大きい順</option>
  <option value="kept">絞り込み後に残る確率の大きい順</option></select>
  <p class="hint" id="modelinfo"></p>
</aside>
<main>
  <div class="tabs" id="tabs"><button data-v="list" class="on">トピック一覧</button><button data-v="topicnet">トピックのネットワーク</button><button data-v="worknet">作品のネットワーク</button><button data-v="bipnet">作品とトピックのネットワーク</button><button data-v="label">ラベルづけ</button><button data-v="table">トピック表</button><a class="help-q" href="topic_viewer_manual.html#network" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></div>
  <section id="netview" hidden>
    <div class="netbar">
      <label id="nmeasw">指標 <select id="nmeas"></select></label>
      <label id="nsrcw" hidden>作品の表し方 <select id="nsrc"></select></label>
      <label><span id="nkL">近い順に</span> <input type="range" id="nk" min="1" max="5" step="1" value="2"> <output id="nkO"></output> <span id="nkU">本</span></label>
      <label id="ntopw" title="すべての組の近さを順位にし，上位 N% に入らない辺を消す">辺を残す：近さの上位 <input type="range" id="ntop" min="1" max="100" step="1" value="100"> <output id="ntopO"></output></label>
      <label id="nminw" hidden>作品内の割合が <input type="range" id="nmin" min="0" max="30" step="1" value="5"> <output id="nminO"></output> 以上</label>
      <label>色 <select id="ncol"></select></label>
      <label id="ntcolw" hidden>トピックの色 <select id="ntcol"><option value="gray" selected>灰色（作品と区別する）</option><option value="cat">作品の色分けに合わせる</option></select></label>
    </div>
    <div class="netbar">
      <label>線の色 <select id="necol"><option value="cat" selected>ノードの分類の色（境界は灰色の破線）</option><option value="gray">すべて灰色</option></select></label>
      <label>線の形 <select id="ncurve"><option value="line" selected>直線</option><option value="curve">曲線</option></select></label>
      <label><input type="checkbox" id="nlab" checked> ラベル</label>
      <label>文字の大きさ <input type="range" id="nfs" min="5" max="22" step="1" value="11"> <output id="nfsO"></output></label>
      <span class="ntools">大きさ：
        <label>ノード <input type="range" id="nsz" min="30" max="150" step="5" value="100"> <output id="nszO"></output></label>
        <label id="ntszw" hidden title="トピック（四角）の大きさを作品（丸）に対して変える">トピック <input type="range" id="ntsz" min="20" max="150" step="5" value="60"> <output id="ntszO"></output></label></span>
    </div>
    <div class="netbar">
      <label>レイアウト <select id="nlay">
        <option value="fr" selected>Fruchterman–Reingold（従来の配置）</option>
        <option value="fa2">ForceAtlas2（Gephi 標準の力学モデル。次数の高いノードほど強く反発）</option>
        <option value="yh">Yifan Hu（粗い配置から多段階で詰める力学モデル）</option>
        <option value="mds">MDS（全組の距離を平面上で再現する配置。stress majorisation）</option>
        <option value="circ">Circular（分類ごとに円周上に並べる）</option>
      </select></label><a class="help-q" href="topic_viewer_manual.html#netlayout" target="jlit-topic-manual" title="レイアウトと補助の操作（マニュアルを別のウィンドウで開く）">？</a>
      <label id="nlinw" class="fa2opt"><input type="checkbox" id="nlin"> LinLog</label>
      <label id="ngrw" class="fa2opt">Gravity <input type="range" style="width:60px" id="ngr" min="0" max="5" step="0.1" value="1"> <output id="ngrO"></output></label>
      <button id="nre" type="button">配置し直す</button>
      <span class="hint" id="nlayst"></span>
      <span class="ntools">濃さ：
        <label>ノード <input type="range" id="nno" min="15" max="100" step="5" value="100"> <output id="nnoO"></output></label>
        <label>線 <input type="range" id="neo" min="10" max="100" step="5" value="100"> <output id="neoO"></output></label></span>
    </div>
    <div class="netbar">
      <span class="ntools">補助：
        <button id="nexp" type="button" title="Expansion：形を保って全体を広げる">Expansion</button>
        <button id="ncon" type="button" title="Contraction：形を保って全体を縮める">Contraction</button>
        <button id="nnov" type="button" title="Noverlap：ノードの重なりを解消">Noverlap</button>
        <button id="nlad" type="button" title="Label Adjust：ラベルの重なりを解消">Label Adjust</button>
        <button id="nrot" type="button" title="Rotate：形を保って全体を回す（正の角度は時計回り，負は反時計回り）">Rotate</button>
        <input type="number" id="nrotA" value="15" step="1" min="-360" max="360" style="width:58px" title="回す角度（度）"> °</span>
      <span class="ntools">書き出し：
        <button id="nsvg" type="button" title="いまの図を SVG ファイルに保存する（凡例つき）">SVG</button>
        <button id="npdf" type="button" title="印刷の画面を開く。印刷先に「PDF に保存」を選ぶ（A4 横に収める）">PDF（印刷）</button>
        <label title="書き出す図の英数字の書体。和文はいつも和文の書体で書く">欧文 <select id="nlatin"><option value="gill" selected>Gill Sans</option><option value="same">和文と同じ書体</option></select></label>
        <label title="モデルの学習条件・グラフの作り方・表示と配置の設定を，図の下縁に小さな字で添える（再現のため）"><input type="checkbox" id="nnote" checked> 条件を添える</label></span>
    </div>
    <p class="hint" id="nhint"></p>
    <p class="hint" id="necnt"></p>
    <div class="netwrap" id="netwrap"><svg id="netsvg" role="img" aria-label="ネットワーク"></svg><div id="ntip" class="tip" hidden></div></div>
    <div class="legend" id="nleg"></div>
    <p class="hint">ノードをドラッグして動かす（放した位置に留まる。ダブルクリックで解く）・ホイールで拡大縮小・背景のドラッグで移動。
    ノードを押すと近い順の一覧が出て，つながる相手が強調される。トピックはダブルクリックで詳細へ移る。</p>
    <div id="ninfo"></div>
  </section>
  <section id="labelview" hidden>
    <p class="hint" style="margin-top:0">トピックごとの<b>診断資料</b>（上位語・担う作品と作家・時代別の割合・偏りの警告）を依頼文にまとめる。
    それを生成 AI（Claude・ChatGPT・Gemini などの無料プランでよい）に貼り付け，返ってきた JSON をここに貼り付けて取り込む。
    <b>ラベルは AI の仮説である。</b>根拠の語と作品を詳細と KWIC で確かめ，必要なら下の一覧で手で直す。<a class="help-q" href="topic_viewer_manual.html#labels" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></p>
    <div class="netbar">
      <label>対象 <select id="lscope"><option value="all">すべてのトピック</option><option value="todo">ラベルの無いトピックだけ</option></select></label>
      <label>1回に含めるトピック <select id="lbatch"><option value="5">5</option><option value="10" selected>10</option><option value="20">20</option><option value="0">すべて</option></select></label>
      <button id="lprev" type="button">← 前</button><span id="lpage" class="hint"></span><button id="lnext" type="button">次 →</button>
    </div>
    <h3 class="lh">1. 依頼文をコピーして，生成 AI に貼り付ける <span class="hint" id="lfp"></span></h3>
    <textarea id="lprompt" readonly rows="10"></textarea>
    <div class="netbar"><button id="lcopy" type="button">依頼文をコピー</button><span id="lcopied" class="hint"></span><span id="lcount" class="hint"></span></div>
    <h3 class="lh">2. 返ってきた JSON を貼り付けて取り込む</h3>
    <textarea id="lans" rows="6" placeholder='{"model": "…", "topics": [{"topic": 0, "label": "…", …}]}'></textarea>
    <div class="netbar">
      <label>使った AI <select id="lsrc"><option>Claude</option><option>ChatGPT</option><option>Gemini</option><option>Copilot</option><option>手元のモデル（Ollama など）</option><option>その他</option></select></label>
      <input type="text" id="lsrc2" placeholder="名前" hidden>
      <button id="limport" type="button">取り込む</button>
    </div>
    <p id="lmsg" class="hint"></p>
    <h3 class="lh">3. ラベルの一覧（手で直せる） <span class="hint" id="lstat"></span></h3>
    <div class="netbar">
      <button id="lexport" type="button">ラベルを書き出す（topic_labels.json）</button>
      <label class="filebtn">ラベルを読み込む <input type="file" id="lfile" accept=".json,application/json"></label>
      <button id="lclear" type="button">このモデルのラベルを消す</button>
    </div>
    <p class="hint">書き出したファイルをビューアと同じフォルダ（my_work/results/）に置けば，ビューアを作り直しても自動で読み込まれる。
    ラベルはモデルの指紋に結び付くので，学習し直したモデルには付かない。</p>
    <div class="ltwrap"><table id="ltab"></table></div>
  </section>
  <section id="tableview" hidden>
    <p class="hint" style="margin-top:0">トピックとキーワード（主表）と，トピック診断表を書き出す。キャプションにはコーパス・モデル・学習条件・キーワードの選び方が入るので，
    論文にそのまま載せられる。LaTeX は booktabs と xltabular を使い，ページをまたぐ長い表にも対応する。<a class="help-q" href="topic_viewer_manual.html#table" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></p>
    <div class="netbar">
      <label>表 <select id="ttype"><option value="main" selected>トピックとキーワード（主表）</option><option value="diag">トピック診断表</option></select></label>
      <label>見出しとキャプション <select id="tlang"><option value="ja" selected>日本語</option><option value="en">English</option></select></label>
      <label>コーパス名 <input type="text" id="tcorp" value="JLit Corpus 2026" style="width:150px"></label>
      <label>並べ方 <select id="tsort"><option value="id" selected>トピック番号</option><option value="prev">平均割合の大きい順</option></select></label>
    </div>
    <div class="netbar" id="tmainopts">
      <label>キーワード <input type="number" id="tn" min="1" max="50" value="20" style="width:56px"> 語</label>
      <label>選び方 <select id="trank"><option value="raw" selected>絞り込みなし（p(w|t) の順）</option><option value="view">いまの表示設定に従う（λ・品詞・頻度帯）</option></select></label>
      <label>語の後の値 <select id="twv"><option value="p" selected>p(w|t)</option><option value="n">度数</option></select></label>
      <label>小数の桁 <input type="number" id="tdig" min="1" max="6" value="3" style="width:46px"></label>
      <span class="ntools">列：<label><input type="checkbox" id="tcmean" checked> 平均割合</label>
        <label id="tcalphaw"><input type="checkbox" id="tcalpha" checked> α</label>
        <label><input type="checkbox" id="tclab" checked> ラベル</label></span>
    </div>
    <div class="netbar">
      <span class="ntools">書き出し：
        <button type="button" data-fmt="tex">LaTeX</button><button type="button" data-fmt="md">Markdown</button>
        <button type="button" data-fmt="csv">CSV</button><button type="button" data-fmt="json">JSON</button>
        <button type="button" data-fmt="html" title="HTML の表。Word や Pages で開くと，罫線つきの表になる">HTML（Word 用）</button></span>
      <label title="プリアンブルと \begin{document} を付け，このファイルだけで LuaLaTeX で組めるようにする"><input type="checkbox" id="tstand"> LaTeX を単独で組める文書にする</label>
    </div>
    <div class="netbar">
      <label>下の表示 <select id="tprev"><option value="tex" selected>LaTeX</option><option value="md">Markdown</option><option value="csv">CSV</option><option value="json">JSON</option><option value="html">HTML（コピーすると表として貼れる）</option></select></label>
      <button id="tcopy" type="button">コピー</button><span id="tmsg" class="hint"></span>
    </div>
    <p class="hint" id="tinfo"></p>
    <textarea id="tout" readonly rows="20" spellcheck="false"></textarea>
  </section>
  <div id="listview">
  <div class="bar"><span id="summary"></span><a class="help-q" href="topic_viewer_manual.html#cards" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></div>
  <div class="grid" id="grid"></div>
  <section id="detail" hidden></section>
  </div>
  <footer class="copy">JLit トピックビューア　&copy; Tomoji Tabata (DH UOsaka)</footer>
</main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const POSGROUPS = [
  ['普通名詞', l => l.startsWith('名詞-普通名詞')],
  ['固有名詞', l => l.startsWith('名詞-固有名詞')],
  ['動詞（自立）', l => l === '動詞-一般' || l.startsWith('動詞-一般')],
  ['動詞（非自立）', l => l.startsWith('動詞-非自立')],
  ['形容詞（自立）', l => l.startsWith('形容詞-一般')],
  ['形容詞（非自立）', l => l.startsWith('形容詞-非自立')],
  ['形状詞', l => l.startsWith('形状詞')],
  ['副詞', l => l.startsWith('副詞')],
  ['数詞', l => l.startsWith('名詞-数詞')],
  ['代名詞', l => l.startsWith('代名詞')],
  ['その他', l => true],
];
const COLORS = ['var(--c0)','var(--c5)','var(--c1)','var(--c1)','var(--c2)','var(--c2)','var(--c3)','var(--c4)','var(--c5)','var(--c5)','var(--c5)'];
const posGroup = l => POSGROUPS.findIndex(g => g[1](l));
let M = null, sel = null, gOf = [];
const st = {pos: new Set(POSGROUPS.map((_, i) => i)), minc: 1, maxdr: 1, maxws: 1, maxdp: 1, maxas: 1, lam: 1, nw: 12, q: '', sort: 'id', rel: 'jsd', nrel: 10};
// トピック間の関連。dir=+1 は大きいほど近い，−1 は小さいほど近い
const RELS = [
  ['jsd', 'Jensen–Shannon divergence', -1,
   '2つのトピックの語分布 p(w|t) の Jensen–Shannon divergence（底 2，0〜1）。0 に近いほど同じ語でできている。LDAvis のトピック間距離と同じ考え方（Sievert & Shirley 2014）。'],
  ['cos', '語分布のコサイン類似度', +1,
   'p(w|t) を並べたベクトルのコサイン類似度。1 に近いほど同じ語でできている。度数の大きい語に引きずられやすい。'],
  ['delta', "Burrows's Delta（z 得点）", -1,
   'モデル内の度数上位 __MFW__ 語について，p(w|t) をトピックをまたいで z 得点にし，差の絶対値を平均したもの（Burrows 2002 の考え方をトピックに当てはめたもの）。高頻度語の支配を抑える。小さいほど近い。'],
  ['cdelta', 'Cosine Delta', -1,
   '同じ z 得点のベクトルの 1 − コサイン類似度（Smith & Aldridge 2011）。0 に近いほど近い。'],
  ['corr', 'チャンク上の相関（CLR 変換後）', +1,
   'チャンクごとのトピックの割合を CLR（centred log-ratio; Aitchison 1986）で変換し，チャンクをまたいで取った Pearson の相関係数。同じチャンクに<b>一緒に現れやすい</b>トピックが高い。割合をそのまま使うと，1 つのトピックが大きい割合を占めたときに他がそろって小さくなり，見かけの相関が生じるので，対数比にしてから取る。ただし和が 1（CLR では和が 0）という制約は変換しても残るため，相関の平均は −1/(K−1)＝__BASE__ になる。<b>この値を基準に</b>読むこと。同じ作品のチャンクが多いので，作品・作家の偏りも拾う。'],
];

// UniDic は外来語の語彙素に原綴を付ける（テーブル-table）。表示は片仮名だけにし，
// 原綴はマウスを載せたときに出す（ロケット-locket のような解析の誤りを確かめるため）
const disp = w => String(w).replace(/-[A-Za-z][A-Za-z .'-]*$/, '');
function esc(s){return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function initModel(i){
  M = D.models[i];
  gOf = M.vocab.map(v => posGroup(v[1]));
  const cnt = POSGROUPS.map(() => 0);
  M.vocab.forEach((v, j) => cnt[gOf[j]] += v[5]);
  $('pos').innerHTML = POSGROUPS.map((g, gi) => cnt[gi] ? `<label><input type="checkbox" data-g="${gi}" ${st.pos.has(gi)?'checked':''}>
    <i style="width:10px;height:10px;border-radius:2px;background:${COLORS[gi]}"></i>${g[0]}<span class="n">${(100*cnt[gi]/M.N).toFixed(1)}%</span></label>` : '').join('');
  $('pos').querySelectorAll('input').forEach(el => el.onchange = () => {
    const g = +el.dataset.g; el.checked ? st.pos.add(g) : st.pos.delete(g); render();});
  const maxc = Math.max(...M.vocab.map(v => v[2]||v[5]));
  $('minc').max = Math.log10(maxc).toFixed(1);
  $('modelinfo').innerHTML = `${esc(M.label)}：${M.K} トピック・${M.works.length} 作品・トークン ${M.N.toLocaleString()}<br>${esc(M.dir)}`;
  sel = null; $('detail').hidden = true;
  render();
  if (typeof NET !== 'undefined' && ['topicnet', 'worknet', 'bipnet'].includes(NET.view)) { netControls(); buildNet(); }
  if (typeof NET !== 'undefined' && NET.view === 'label') labelView();
}

function keep(j){
  const v = M.vocab[j];
  if (!st.pos.has(gOf[j])) return false;
  const c = v[2] || v[5];
  if (c < st.minc) return false;
  if (v[3] > st.maxdr + 1e-9) return false;
  if (v[4] > st.maxws + 1e-9) return false;
  if (v[6] > st.maxdp + 1e-9) return false;
  if (v[7] > st.maxas + 1e-9) return false;
  return true;
}

function ranked(t){
  // relevance = λ log p(w|t) + (1−λ) log(p(w|t)/p(w))
  const Tt = M.topicTotals[t], N = M.N, lam = st.lam;
  const out = [];
  let keptMass = 0, stored = 0;
  for (const [j, n] of M.tw[t]) {
    const pwt = n / Tt;
    stored += pwt;
    if (!keep(j)) continue;
    keptMass += pwt;
    const pw = M.vocab[j][5] / N;
    out.push({j, n, pwt, r: lam * Math.log(pwt) + (1 - lam) * Math.log(pwt / pw)});
  }
  out.sort((a, b) => b.r - a.r);
  // 残存＝ビューアが持っている上位語の確率のうち，絞り込み後に残った割合
  return {list: out, keptMass: stored ? keptMass / stored : 0};
}

function render(){
  $('mincO').textContent = st.minc.toLocaleString();
  $('maxdrO').textContent = Math.round(st.maxdr * 100) + '%';
  $('maxwsO').textContent = Math.round(st.maxws * 100) + '%';
  $('maxdpO').textContent = st.maxdp.toFixed(2);
  $('maxasO').textContent = Math.round(st.maxas * 100) + '%';
  $('lamO').textContent = st.lam.toFixed(2);
  const R = [];
  for (let t = 0; t < M.K; t++) R.push({t, ...ranked(t)});
  const q = st.q.trim();
  let order = R.slice();
  if (st.sort === 'prev') order.sort((a, b) => M.prev[b.t] - M.prev[a.t]);
  if (st.sort === 'kept') order.sort((a, b) => b.keptMass - a.keptMass);
  const maxPrev = Math.max(...M.prev);
  let hits = 0;
  $('grid').innerHTML = order.map(({t, list, keptMass}) => {
    const top = list.slice(0, st.nw);
    const has = q && top.some(x => M.vocab[x.j][0] === q || disp(M.vocab[x.j][0]) === q);
    if (has) hits++;
    const dim = q && !has ? ' dim' : '';
    const ws = top.map(x => {
      const v = M.vocab[x.j];
      const hit = q && (v[0] === q || disp(v[0]) === q);
      return `<b style="color:${COLORS[gOf[x.j]]}" class="${hit?'m':''}" title="${esc(v[0])}（${esc(v[1])}）">${esc(disp(v[0]))}</b>`;}).join(' ');
    const lb = labelOf(t);
    const ltag = lb ? `<div class="clab" title="${esc(lb.type)}・確信度 ${esc(lb.confidence)}・${esc(lb.source || '')}：${esc(lb.evidence || '')}">${esc(lb.label)}</div>`
                    : `<div class="clab auto" title="仮ラベル（機械的に付けたもの）">${esc(autoLabel(t))}</div>`;
    return `<div class="card${dim}${sel===t?' sel':''}" data-t="${t}"><h3>T${String(t).padStart(2,'0')}
      <span title="全体に占める割合・絞り込み後に残った語の確率の割合">${(100*M.prev[t]).toFixed(1)}%・残存 ${(100*keptMass).toFixed(0)}%</span></h3>
      ${ltag}<div class="prevbar"><i style="width:${100*M.prev[t]/maxPrev}%"></i></div><div class="words">${ws || '<span class="warn">表示できる語が無い</span>'}</div></div>`;
  }).join('');
  $('grid').querySelectorAll('.card').forEach(el => el.onclick = () => {sel = +el.dataset.t; render(); detail(sel); $('detail').scrollIntoView({behavior:'smooth'});});
  const nk = M.vocab.filter((_, j) => keep(j)).length;
  $('summary').innerHTML = `表示対象の語 ${nk.toLocaleString()} / ${M.vocab.length.toLocaleString()}（各トピックの上位語から）` +
    (q ? `・「${esc(q)}」を上位 ${st.nw} 語に持つトピック ${hits}` : '') +
    `・「残存」＝そのトピックの上位語の確率のうち，絞り込み後に残った割合（低いトピックは隠した語でできている）`;
  if (sel !== null) detail(sel);
  if (M && document.getElementById('tableview') && !$('tableview').hidden) tblView();
}

function barsSVG(items, w, labelW, fmt){
  const h = 16, H = items.length * h + 6, max = Math.max(...items.map(x => x.v), 1e-9);
  return `<svg width="100%" viewBox="0 0 ${w} ${H}" role="img">` + items.map((x, i) =>
    `<text x="${labelW-4}" y="${i*h+12}" text-anchor="end">${esc(x.show || x.label)}<title>${esc(x.label)}</title></text>
     <rect x="${labelW}" y="${i*h+3}" width="${Math.max(1,(w-labelW-60)*x.v/max)}" height="${h-5}" rx="2" fill="${x.c||'var(--acc)'}"><title>${esc(x.label)}：${fmt(x.v)}</title></rect>
     <text x="${labelW+(w-labelW-60)*x.v/max+4}" y="${i*h+12}" class="mut">${fmt(x.v)}</text>`).join('') + '</svg>';
}

function detail(t){
  const {list, keptMass} = ranked(t);
  const top = list.slice(0, 30);
  const words = barsSVG(top.map(x => ({label: M.vocab[x.j][0], show: disp(M.vocab[x.j][0]), v: x.pwt, c: COLORS[gOf[x.j]]})), 420, 90,
    v => (100*v).toFixed(2) + '%');
  const per = barsSVG(M.periods.map((p, i) => ({label: p.replace(/^\d_/, ''), v: M.periodTopic[i][t]})), 420, 150,
    v => (100*v).toFixed(1) + '%');
  // 2つの割合を区別する
  //   作品内の割合      P(t|作品)：その作品のチャンクでこのトピックが占める割合の平均
  //   トピックに占める割合 P(作品|t)：このトピックの重み（割合×チャンク数の総和）のうち，
  //                      その作品（作家）のチャンクから来ている割合
  const mass = M.works.map((w, i) => M.workTopic[i][t] * w[5]);
  const totalMass = mass.reduce((s, x) => s + x, 0) || 1;
  const ws = M.works.map((w, i) => ({w, v: M.workTopic[i][t], sh: mass[i] / totalMass}))
    .sort((a, b) => b.v - a.v).slice(0, 12);
  const byAuthor = {};
  M.works.forEach((w, i) => { const a = (byAuthor[w[1]] ||= {v: [], m: 0}); a.v.push(M.workTopic[i][t]); a.m += mass[i]; });
  const au = Object.entries(byAuthor).map(([a, o]) => ({a, v: o.v.reduce((s, x) => s + x, 0) / o.v.length, n: o.v.length, sh: o.m / totalMass}))
    .sort((a, b) => b.sh - a.sh).slice(0, 8);
  const pct = x => (100 * x).toFixed(1) + '%';
  const wtab = `<table><tr><th>作品</th><th>作家</th><th>初出</th>
      <th class="num" title="その作品のチャンクで，このトピックが占める割合の平均">作品内の割合</th>
      <th class="num" title="このトピックの重み全体のうち，その作品から来ている割合">トピックに占める割合</th></tr>` +
    ws.map(x => `<tr><td>${esc(x.w[2])}</td><td>${esc(x.w[1])}</td><td>${esc(x.w[3])}</td><td class="num">${pct(x.v)}</td><td class="num">${pct(x.sh)}</td></tr>`).join('') + '</table>';
  const atab = `<table><tr><th>作家</th><th class="num">作品数</th>
      <th class="num" title="その作家の各作品での「作品内の割合」の平均">作品内の割合（平均）</th>
      <th class="num" title="このトピックの重み全体のうち，その作家の作品から来ている割合">トピックに占める割合</th></tr>` +
    au.map(x => `<tr><td>${esc(x.a)}</td><td class="num">${x.n}</td><td class="num">${pct(x.v)}</td><td class="num">${pct(x.sh)}</td></tr>`).join('') + '</table>';
  const [topA, topAm] = Object.entries(byAuthor).map(([a, o]) => [a, o.m]).sort((a, b) => b[1] - a[1])[0] || ['', 0];
  const topW = Math.max(...mass) / totalMass, topWi = mass.indexOf(Math.max(...mass));
  let warn = '';
  if (topW > 0.5) warn = `<p class="warn">⚠ このトピックの重みの ${(100*topW).toFixed(0)}% が1作品（${esc(M.works[topWi][2])}）から来ている（トピックに占める割合）。主題ではなく作品指標である可能性が高い。</p>`;
  else if (topAm / totalMass > 0.5) warn = `<p class="warn">⚠ このトピックの重みの ${(100*topAm/totalMass).toFixed(0)}% が1作家（${esc(topA)}）の作品から来ている（トピックに占める割合）。主題ではなく作家指標である可能性がある。</p>`;
  const note = `<p class="hint">「作品内の割合」＝その作品の中でこのトピックが占める割合（P(トピック｜作品)）。
    「トピックに占める割合」＝このトピックの重みのうちその作品から来る割合（P(作品｜トピック)）。
    向きが逆なので値は一致しない。どちらも語の絞り込みでは変わらない。</p>`;
  $('detail').hidden = false;
  $('detail').innerHTML = `<h2>T${String(t).padStart(2,'0')}　全体の ${(100*M.prev[t]).toFixed(1)}%・表示の残存 ${(100*keptMass).toFixed(0)}%<a class="help-q" href="topic_viewer_manual.html#detail" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></h2>
    ${labBox(t)}
    <div class="legend">${POSGROUPS.map((g, gi) => `<span><i style="background:${COLORS[gi]}"></i>${g[0]}</span>`).slice(0, 8).join('')}</div>
    ${warn}
    <div class="cols"><div><h3 style="font-size:13px">上位語（λ=${st.lam.toFixed(2)} の順・棒は p(w|t)）</h3>${words}
      <p class="hint">MALLET の上位語（絞り込み前）：${esc(M.keys[t] || '')}</p>
      <p class="hint">外来語は片仮名だけを表示している。語にマウスを載せると UniDic の語彙素（原綴つき）と品詞が出る。</p></div>
    <div><h3 style="font-size:13px">時代別の割合（チャンク平均）</h3>${per}
      <h3 style="font-size:13px">このトピックが多い作品（作品内の割合の順）</h3>${wtab}
      <h3 style="font-size:13px">このトピックを担う作家（トピックに占める割合の順）</h3>${atab}${note}</div></div>
    <div id="rel"></div>`;
  relTable(t);
}

function relTable(t){
  const [key, name, dir, hint] = RELS.find(r => r[0] === st.rel);
  const row = M.rel[key][t];
  const fmt = v => v.toFixed(3);
  const others = row.map((v, u) => ({u, v})).filter(x => x.u !== t)
    .sort((a, b) => dir * (b.v - a.v)).slice(0, st.nrel);
  const words = u => ranked(u).list.slice(0, 8).map(x => `<b style="color:${COLORS[gOf[x.j]]}" title="${esc(M.vocab[x.j][0])}">${esc(disp(M.vocab[x.j][0]))}</b>`).join(' ');
  $('rel').innerHTML = `<div class="head"><h3>関連の強いトピック<a class="help-q" href="topic_viewer_manual.html#related" target="jlit-topic-manual" title="この欄の使い方（マニュアルを別のウィンドウで開く）">？</a></h3>
      <select id="relm">${RELS.map(r => `<option value="${r[0]}"${r[0]===key?' selected':''}>${esc(r[1])}</option>`).join('')}</select>
      <label>件数 <input type="number" id="nrel" min="3" max="${Math.max(3, M.K-1)}" value="${st.nrel}" style="width:56px"></label></div>
    <p class="hint">${hint.replace('__MFW__', M.rel.mfw).replace('__BASE__', (-1/(M.K-1)).toFixed(3))}</p>
    <table><tr><th>順位</th><th>トピック</th><th class="num">${dir > 0 ? '値（大きいほど近い）' : '値（小さいほど近い）'}</th>
      <th class="num" title="全体に占める割合">割合</th><th>上位語（いまの絞り込みと λ で）</th></tr>` +
    others.map((x, i) => `<tr class="go" data-t="${x.u}"><td class="num">${i+1}</td><td>T${String(x.u).padStart(2,'0')}</td>
      <td class="num">${fmt(x.v)}</td><td class="num">${(100*M.prev[x.u]).toFixed(1)}%</td><td class="w">${words(x.u) || '—'}</td></tr>`).join('') +
    `</table><p class="hint">上の 4 つは<b>語分布の近さ</b>（同じ語でできているか），相関は<b>文書の中での共起</b>（同じチャンクに一緒に現れるか）で，別のものを測っている。
     行を押すとそのトピックに移る。</p>`;
  $('relm').onchange = e => { st.rel = e.target.value; relTable(t); };
  $('nrel').onchange = e => { st.nrel = Math.max(3, Math.min(M.K - 1, +e.target.value || 10)); relTable(t); };
  $('rel').querySelectorAll('tr.go').forEach(el => el.onclick = () => {
    sel = +el.dataset.t; render(); $('detail').scrollIntoView({behavior:'smooth'}); });
}


// ======================================================================
// ネットワーク（トピック間・作品間）。外部のライブラリは使わない。
// 配置は選んだレイアウト（既定は力学モデル）。ドラッグで動かし，ホイールで拡大縮小，
// 背景のドラッグで移動する。
// ======================================================================
const PAL = ['#0072B2', '#E69F00', '#009E73', '#CC79A7', '#56B4E9', '#D55E00', '#8C6D31', '#6A3D9A'];
const GRAY = '#9a9a93';
const NET = {view: 'list', kind: 'topic', nodes: [], edges: [], raf: 0, alpha: 0,
             scale: 1, tx: 0, ty: 0, focus: null, W: 900, H: 620};
const nst = {lay: 'fr', lin: false, grav: 1, curve: false, note: true, latin: 'gill', meas: 'jsd', src: 'd2v', k: 2, top: 100, col: 'period', lab: true, fs: 11, nop: 100, eop: 100, ecol: 'cat', minsh: 5, nsz: 100, tsz: 60, tcol: 'gray'};
const TGRAY = '#5f5f5a';   // 作品とトピックのネットワークでのトピック（四角）の色
// 文字の大きさと濃さは SVG の変数で持つ（配置を計算し直さずに変えられる）
function netStyle(){
  const s = $('netsvg').style;
  s.setProperty('--nfs', nst.fs + 'px'); s.setProperty('--nop', nst.nop / 100); s.setProperty('--eop', nst.eop / 100);
  $('nfsO').textContent = nst.fs + 'px'; $('nnoO').textContent = nst.nop + '%'; $('neoO').textContent = nst.eop + '%';
  $('nszO').textContent = nst.nsz + '%'; $('ntszO').textContent = nst.tsz + '%';
}
// ノードの大きさの倍率。作品とトピックのネットワークでは，トピック（四角）だけ別に縮められる
function nodeScale(nd){ return nst.nsz / 100 * (nd.kind === 't' ? nst.tsz / 100 : 1); }
// 大きさだけを変える（配置は計算し直さない）
function resizeNodes(){
  NET.nodes.forEach(p => {
    if (p.r0 == null || !p.el) return;
    p.r = p.r0 * nodeScale(p);
    if (p.kind === 't') { p.el.setAttribute('width', (2 * p.r).toFixed(1)); p.el.setAttribute('height', (2 * p.r).toFixed(1)); }
    else p.el.setAttribute('r', p.r.toFixed(1));
  });
  draw();
}

function setView(v){
  NET.view = v;
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  const net = v === 'topicnet' || v === 'worknet' || v === 'bipnet';
  $('listview').hidden = v !== 'list';
  $('netview').hidden = !net;
  $('labelview').hidden = v !== 'label';
  $('tableview').hidden = v !== 'table';
  if (net) { NET.kind = {topicnet: 'topic', worknet: 'work', bipnet: 'bip'}[v]; netControls(); buildNet(); }
  else cancelAnimationFrame(NET.raf);
  if (v === 'label') labelView();
  if (v === 'table') tblView();
}

function goTopic(t){
  setView('list');
  sel = t; render(); detail(t);
  $('detail').scrollIntoView({behavior: 'smooth'});
}

// ---- 操作欄 -----------------------------------------------------------
function netControls(){
  let opts;
  const bip = NET.kind === 'bip';
  $('ntopw').hidden = bip; $('nminw').hidden = !bip; $('ntszw').hidden = !bip; $('ntcolw').hidden = !bip; $('ntcol').value = nst.tcol;
  $('nkL').textContent = bip ? '各作品から割合の高い順に' : '近い順に';
  $('nkU').textContent = bip ? '個のトピック' : '本';
  $('nmin').value = nst.minsh; $('nminO').textContent = nst.minsh + '%';
  if (bip) {
    $('nsrcw').hidden = true; $('nmeasw').hidden = true;
  } else if (NET.kind === 'topic') {
    opts = RELS.map(r => [r[0], r[1]]);
    if (!RELS.some(r => r[0] === nst.meas)) nst.meas = 'jsd';
    $('nsrcw').hidden = true;
    $('nmeasw').hidden = false;
  } else {
    $('nsrcw').hidden = false;
    $('nmeasw').hidden = true;
    const so = [['theta', 'トピック構成の近さ（Jensen–Shannon divergence）']];
    if (D.d2v) so.unshift(['d2v', 'doc2vec の作品ベクトル（コサイン類似度）']);
    if (!so.some(x => x[0] === nst.src)) nst.src = so[0][0];
    $('nsrc').innerHTML = so.map(x => `<option value="${x[0]}"${x[0]===nst.src?' selected':''}>${esc(x[1])}</option>`).join('');
  }
  if (opts) $('nmeas').innerHTML = opts.map(x => `<option value="${x[0]}"${x[0]===nst.meas?' selected':''}>${esc(x[1])}</option>`).join('');
  const cols = NET.kind === 'topic'
    ? [['period', 'いちばん割合の高い時代区分'], ['community', 'コミュニティ']]
    : [['period', '時代区分'], ['author', '作家'], ['genre', 'ジャンル'], ['style', '文体'], ['community', 'コミュニティ']];
  if (!cols.some(c => c[0] === nst.col)) nst.col = 'period';
  $('ncol').innerHTML = cols.map(c => `<option value="${c[0]}"${c[0]===nst.col?' selected':''}>${esc(c[1])}</option>`).join('');
  $('nk').value = nst.k; $('nkO').textContent = nst.k;
  $('ntop').value = nst.top; $('ntopO').textContent = nst.top + '%';
  $('nlab').checked = nst.lab;
  $('nfs').value = nst.fs; $('nno').value = nst.nop; $('neo').value = nst.eop;
  $('nsz').value = nst.nsz; $('ntsz').value = nst.tsz; netStyle();
}

// ---- グラフを作る -----------------------------------------------------
// 近さ s(i,j) を指標の向きで揃え（大きいほど近い），各ノードから近い順に k 本。
// 重みは「全組の中での近さの順位」（0〜1）。指標ごとに値の尺度が違っても同じ扱いにできる。
// 作品とトピックの2部グラフ。作品のトピック構成（チャンクの θ の作品平均）で結ぶ
function bipData(){
  const W = M.works, n = W.length;
  const maxC = Math.max(1, ...W.map(w => w[5])), maxP = Math.max(...M.prev);
  const nodes = W.map((w, i) => ({id: i, kind: 'w', w, label: shortTitle(w[2]), r: 4 + 8 * Math.sqrt(w[5] / maxC)}))
    .concat(M.prev.map((p, t) => ({id: n + t, kind: 't', t,
      label: 'T' + String(t).padStart(2, '0') + (labelOf(t) ? ' ' + shortLabel(labelOf(t).label, 8) : ''),
      r: 6 + 11 * Math.sqrt(p / maxP)})));
  const edges = [];
  W.forEach((w, i) => {
    M.workTopic[i].map((v, t) => ({t, v})).sort((a, b) => b.v - a.v).slice(0, nst.k)
      .filter(x => x.v * 100 >= nst.minsh).forEach(x => edges.push({a: i, b: n + x.t, w: x.v, v: x.v}));
  });
  return {nodes, edges, dir: 1, mat: null};
}

function graphData(){
  if (NET.kind === 'bip') return bipData();
  let n, mat, dir, nodes;
  if (NET.kind === 'topic') {
    const r = RELS.find(x => x[0] === nst.meas);
    mat = M.rel[r[0]]; dir = r[2]; n = M.K;
    const maxP = Math.max(...M.prev);
    nodes = M.prev.map((p, t) => ({id: t, label: 'T' + String(t).padStart(2, '0') + (labelOf(t) ? ' ' + shortLabel(labelOf(t).label, 8) : ''),
      r: 5 + 13 * Math.sqrt(p / maxP)}));
  } else {
    let works;
    if (nst.src === 'd2v' && D.d2v) { works = D.d2v.works; mat = D.d2v.sim; dir = 1; }
    else { works = M.works; mat = M.workJsd; dir = -1; }
    n = works.length;
    const chunks = {}; M.works.forEach(w => { chunks[w[0]] = w[5]; });
    const maxC = Math.max(1, ...Object.values(chunks));
    nodes = works.map((w, i) => ({id: i, w, label: shortTitle(w[2]),
      r: chunks[w[0]] ? 4 + 9 * Math.sqrt(chunks[w[0]] / maxC) : 7}));
  }
  const s = (i, j) => dir * mat[i][j];
  const all = [];
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) all.push(s(i, j));
  all.sort((a, b) => a - b);
  const pct = v => { // v 以下の組の割合（二分探索）
    let lo = 0, hi = all.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (all[m] <= v) lo = m + 1; else hi = m; }
    return all.length ? lo / all.length : 1;
  };
  const cut = 1 - nst.top / 100;
  const key = new Map();
  for (let i = 0; i < n; i++) {
    const nb = [];
    for (let j = 0; j < n; j++) if (j !== i) nb.push([j, s(i, j)]);
    nb.sort((a, b) => b[1] - a[1]);
    nb.slice(0, nst.k).forEach(([j, v]) => {
      const a = Math.min(i, j), b = Math.max(i, j), id = a + ',' + b;
      if (key.has(id)) return;
      const w = pct(v);
      if (w + 1e-12 < cut) return;
      key.set(id, {a, b, w, v: mat[a][b]});
    });
  }
  return {nodes, edges: [...key.values()], dir, mat};
}

function shortTitle(t){ t = String(t || ''); return t.length > 8 ? t.slice(0, 8) + '…' : t; }

// ---- コミュニティ（Louvain 法の局所移動と集約）-------------------------
function louvain(n, edges){
  let comm = [...Array(n).keys()];
  let nodesOf = comm.map(i => [i]);         // 集約後のノード → 元のノード
  let E = edges.map(e => [e.a, e.b, e.w]);
  let N = n;
  for (let level = 0; level < 6; level++) {
    const adj = Array.from({length: N}, () => new Map());
    let m2 = 0;
    const deg = new Array(N).fill(0);
    E.forEach(([a, b, w]) => {
      if (a === b) { adj[a].set(a, (adj[a].get(a) || 0) + 2 * w); deg[a] += 2 * w; m2 += 2 * w; return; }
      adj[a].set(b, (adj[a].get(b) || 0) + w); adj[b].set(a, (adj[b].get(a) || 0) + w);
      deg[a] += w; deg[b] += w; m2 += 2 * w;
    });
    if (m2 === 0) break;
    const c = [...Array(N).keys()];
    const tot = deg.slice();
    let moved = true, any = false, pass = 0;
    while (moved && pass++ < 30) {
      moved = false;
      for (let i = 0; i < N; i++) {
        const ci = c[i];
        const wTo = new Map();
        adj[i].forEach((w, j) => { if (j !== i) wTo.set(c[j], (wTo.get(c[j]) || 0) + w); });
        tot[ci] -= deg[i];
        let best = ci, bestGain = (wTo.get(ci) || 0) - tot[ci] * deg[i] / m2;
        wTo.forEach((w, cj) => {
          const g = w - tot[cj] * deg[i] / m2;
          if (g > bestGain + 1e-12) { bestGain = g; best = cj; }
        });
        tot[best] += deg[i];
        if (best !== ci) { c[i] = best; moved = true; any = true; }
      }
    }
    if (!any) break;
    // 集約
    const ids = [...new Set(c)]; const re = new Map(ids.map((x, i) => [x, i]));
    const newNodesOf = ids.map(() => []);
    for (let i = 0; i < N; i++) newNodesOf[re.get(c[i])].push(...nodesOf[i]);
    const agg = new Map();
    E.forEach(([a, b, w]) => {
      const x = re.get(c[a]), y = re.get(c[b]);
      const k = Math.min(x, y) + ',' + Math.max(x, y);
      agg.set(k, (agg.get(k) || 0) + w);
    });
    E = [...agg.entries()].map(([k, w]) => { const [x, y] = k.split(',').map(Number); return [x, y, w]; });
    nodesOf = newNodesOf; N = ids.length;
  }
  // 大きい順に番号を振る（色が安定する）
  nodesOf.sort((a, b) => b.length - a.length || a[0] - b[0]);
  nodesOf.forEach((ns, ci) => ns.forEach(i => { comm[i] = ci; }));
  return comm;
}

// ---- 色分け ------------------------------------------------------------
function categorise(nodes, edges){
  let cat, cm = null;
  if (nst.col === 'community') {
    cm = louvain(nodes.length, edges);
    cat = cm.map((c, i) => nodes[i].kind === 't' ? 'トピック' : 'コミュニティ ' + (c + 1));
  } else if (NET.kind === 'topic') {
    cat = nodes.map(nd => {
      let best = -1, bi = -1;
      M.periods.forEach((p, i) => { if (!/不明/.test(p) && M.periodTopic[i][nd.id] > best) { best = M.periodTopic[i][nd.id]; bi = i; } });
      return bi >= 0 ? M.periods[bi].replace(/^\d_/, '') : '不明';
    });
  } else {
    const idx = {period: 4, author: 1, genre: 6, style: 7}[nst.col];
    cat = nodes.map(nd => {
      if (nd.kind === 't') return 'トピック';
      const v = String(nd.w[idx] || '');
      return (nst.col === 'period' ? v.replace(/^\d_/, '') : v) || '不明';
    });
  }
  // 色の割り当て：時代区分は時代順，ほかは多い順。9 種目以降は灰色（その他）
  const cnt = new Map(); cat.forEach(c => cnt.set(c, (cnt.get(c) || 0) + 1));
  let order = [...cnt.keys()];
  if (nst.col === 'period') {
    const pos = new Map((NET.kind === 'topic' ? M.periods : [...new Set((NET.kind === 'work' && nst.src === 'd2v' && D.d2v ? D.d2v.works : M.works).map(w => w[4]))].sort())
      .map((p, i) => [String(p).replace(/^\d_/, ''), i]));
    order.sort((a, b) => (pos.has(a) ? pos.get(a) : 99) - (pos.has(b) ? pos.get(b) : 99));
  } else {
    order.sort((a, b) => cnt.get(b) - cnt.get(a) || a.localeCompare(b, 'ja'));
  }
  const color = new Map();
  let k = 0;
  order = order.filter(c => c !== 'トピック');
  order.forEach(c => { color.set(c, (c === '不明' || k >= PAL.length) ? GRAY : PAL[k++]); });
  if (cnt.has('トピック')) { color.set('トピック', TGRAY); order.push('トピック'); }
  return {cat, color, order, cnt, cm};
}

// 作品とトピックのネットワークで，トピック（四角）を作品の色分けに合わせるときの分類。
// コミュニティのときはトピック自身のコミュニティ（作品の無いコミュニティなら下と同じ扱い），
// 時代区分・作家などのときは，線でつながる作品の分類のうち割合（線の重み）の合計が最も大きいもの。
// 線の無いトピックは灰色のまま
function topicCats(g, cat, color, cm){
  const out = [];
  if (NET.kind !== 'bip' || nst.tcol !== 'cat') return out;
  g.nodes.forEach((nd, i) => {
    if (nd.kind !== 't') return;
    if (cm) {
      const c = 'コミュニティ ' + (cm[i] + 1);
      if (color.has(c)) { out[i] = c; return; }
    }
    const sum = new Map();
    g.edges.forEach(e => { if (e.b === i) sum.set(cat[e.a], (sum.get(cat[e.a]) || 0) + e.w); });
    let best = null, bw = -1;
    sum.forEach((w, c) => { if (w > bw) { bw = w; best = c; } });
    if (best != null) out[i] = best;
  });
  return out;
}

// ---- 描く ---------------------------------------------------------------
const SVGNS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs){ const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; }

function buildNet(){
  cancelAnimationFrame(NET.raf);
  const g = graphData();
  NET.nodes = g.nodes; NET.edges = g.edges; NET.dir = g.dir; NET.mat = g.mat; NET.focus = null;
  NET.userView = false; NET.scale = 1; NET.tx = NET.ty = 0;
  const {cat, color, order, cnt, cm} = categorise(g.nodes, g.edges);
  NET.cat = cat; NET.color = color; NET.order = order;
  const tcat = topicCats(g, cat, color, cm);
  // 初期配置は円周上（再現できるように乱数を使わない）
  const n = g.nodes.length, R = Math.min(NET.W, NET.H) * 0.38;
  g.nodes.forEach((nd, i) => {
    const a = 2 * Math.PI * i / Math.max(1, n);
    nd.x = NET.W / 2 + R * Math.cos(a); nd.y = NET.H / 2 + R * Math.sin(a);
    nd.vx = 0; nd.vy = 0; nd.fixed = false;
    nd.color = color.get(cat[i]); nd.cat = cat[i];
    if (tcat[i]) { nd.color = color.get(tcat[i]); nd.tcat = tcat[i]; }
  });
  const svg = $('netsvg');
  svg.innerHTML = '';
  svg.setAttribute('viewBox', `0 0 ${NET.W} ${NET.H}`);
  const vp = svgEl('g', {id: 'netvp'});
  svg.appendChild(vp);
  const eg = svgEl('g', {}), ng = svgEl('g', {}), lg = svgEl('g', {});
  vp.append(eg, ng, lg);
  // **線の太さと濃さ＝近さ。** 近さの順位 w（全組の中で 0〜1）を，いま表示している辺の中で
  // 0〜1 に引き伸ばして太さにする（k 近傍の辺はどれも上位にあるので，そのままでは差が見えない）
  const ws = g.edges.map(e => e.w), wmin = Math.min(...ws), wmax = Math.max(...ws);
  const lab = i => (NET.kind === 'topic' || g.nodes[i].kind === 't') ? g.nodes[i].label : `${g.nodes[i].w[1]}『${g.nodes[i].w[2]}』`;
  g.edges.forEach(e => {
    e.rel = wmax > wmin ? (e.w - wmin) / (wmax - wmin) : 1;
    e.el = svgEl('path', {class: 'edge', 'stroke-width': (0.7 + 4.8 * e.rel).toFixed(2),
      'stroke-opacity': (0.28 + 0.6 * e.rel).toFixed(2)});
    // 両端が同じ分類なら内側の辺，違えば境界の辺（作家・時代などを橋渡しする辺）
    e.inside = cat[e.a] === cat[e.b];
    const tt = svgEl('title', {});
    tt.textContent = NET.kind === 'bip'
      ? `${lab(e.a)} — ${g.nodes[e.b].label}：作品内の割合 ${(100 * e.v).toFixed(1)}%`
      : `${lab(e.a)} — ${lab(e.b)}：${e.v.toFixed(3)}（全組の中で近いほうから ${(100 * (1 - e.w)).toFixed(1)}% の位置）`
        + (e.inside ? `・内側（${cat[e.a]}）` : `・境界（${cat[e.a]} — ${cat[e.b]}）`);
    e.el.appendChild(tt);
    eg.appendChild(e.el);
  });
  g.nodes.forEach((nd, i) => {
    nd.r0 = nd.r; nd.r = nd.r0 * nodeScale(nd);
    // トピック（作品とトピックのネットワーク）は四角で，作品の丸と形で分ける
    nd.el = nd.kind === 't'
      ? svgEl('rect', {width: (2 * nd.r).toFixed(1), height: (2 * nd.r).toFixed(1), rx: 2.5, fill: nd.color, class: 'node tnode'})
      : svgEl('circle', {r: nd.r.toFixed(1), fill: nd.color, class: 'node'});
    nd.el.dataset.i = i;
    ng.appendChild(nd.el);
    // ラベルの字はノードと同じ色（灰色のノードは灰色）。縁取りで背景から浮かせる
    // （共通の CSS「svg text{fill:…}」が属性より強いので，style で指定する）
    nd.tx = svgEl('text', {class: 'nlabel', 'text-anchor': 'middle'});
    nd.tx.style.fill = nd.color;
    nd.tx.textContent = nd.label;
    lg.appendChild(nd.tx);
  });
  lg.style.display = nst.lab ? '' : 'none';
  edgeColors();
  applyView();
  // 凡例
  const shown = order.filter(c => (color.get(c) !== GRAY || c === '不明') && !(c === 'トピック' && nst.tcol === 'cat'));
  const rest = order.filter(c => color.get(c) === GRAY && c !== '不明');
  $('nleg').innerHTML = shown.map(c => `<span><i style="background:${color.get(c)}"></i>${esc(c)}（${cnt.get(c)}）</span>`).join('')
    + (rest.length ? `<span title="${esc(rest.join('・'))}"><i style="background:${GRAY}"></i>その他 ${rest.length} 種（${rest.reduce((s, c) => s + cnt.get(c), 0)}）</span>` : '');
  // 説明
  const iso = g.nodes.filter((_, i) => !g.edges.some(e => e.a === i || e.b === i)).length;
  let src;
  if (NET.kind === 'bip') src = `作品（丸）とトピック（四角）を，作品のトピック構成（チャンクの θ の平均。モデル「${esc(M.label)}」）で結ぶ。`
    + `各作品から割合の高い順に ${nst.k} 個・割合 ${nst.minsh}% 以上。線の太さは作品内の割合。`
    + (nst.tcol === 'cat' ? (nst.col === 'community' ? '四角の色はトピックの属するコミュニティ（作品の無いコミュニティなら，つながる作品で重みの合計が最も大きい分類）。' : '四角の色は，線でつながる作品の分類のうち割合の合計が最も大きいもの。') : '');
  else if (NET.kind === 'topic') src = `指標：${RELS.find(x => x[0] === nst.meas)[1]}（${NET.dir > 0 ? '大きいほど近い' : '小さいほど近い'}）。円の大きさはトピックの割合。`;
  else if (nst.src === 'd2v' && D.d2v) src = `指標：doc2vec の作品ベクトル（チャンクの document vector の平均，${D.d2v.dim} 次元）のコサイン類似度。${esc(D.d2v.dir)}`;
  else src = `指標：作品のトピック構成（チャンクの θ の平均）どうしの Jensen–Shannon divergence（モデル「${esc(M.label)}」）。`
    + (D.d2v ? '' : ' doc2vec の結果はビューアに入っていない（Step 7 のあと，--d2v を付けて作り直すと選べる）。');
  $('nhint').innerHTML = NET.kind === 'bip'
    ? src + (iso ? `線の無いノード ${iso}（割合が低いトピックや作品）。` : '')
    : `線が太く濃いほど近い（線にポインタを載せると値が出る）。${g.nodes.length} ノード・${g.edges.length} 辺（各ノードから近い順に ${nst.k} 本，全組の近さの上位 ${nst.top}% まで）`
      + (iso ? `・辺の無いノード ${iso}` : '') + '。' + src;
  $('ninfo').innerHTML = '';
  startLayout();
}

// 配置が落ち着いたら，全体が画面に収まるように拡大縮小する（利用者が動かしていなければ）
function fitView(){
  const N = NET.nodes; if (!N.length) return;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  N.forEach(p => { x0 = Math.min(x0, p.x - p.r); y0 = Math.min(y0, p.y - p.r - (nst.lab ? nst.fs + 5 : 0)); x1 = Math.max(x1, p.x + p.r); y1 = Math.max(y1, p.y + p.r); });
  const pad = 24, w = Math.max(1, x1 - x0), h = Math.max(1, y1 - y0);
  const s = Math.min(1.6, Math.max(0.3, Math.min((NET.W - 2 * pad) / w, (NET.H - 2 * pad) / h)));
  NET.scale = s; NET.tx = (NET.W - s * (x0 + x1)) / 2; NET.ty = (NET.H - s * (y0 + y1)) / 2;
  applyView();
}
// 辺の色：内側の辺はその分類の色，境界の辺は灰色の破線（「すべて灰色」なら従来どおり）。
// 灰色の分類（その他・不明）どうしの辺は，色を付けずに実線の灰色にする
function edgeColors(){
  if (NET.kind === 'bip') {
    // 作品とトピックの線は作品の色（灰色の作品は灰色）
    NET.edges.forEach(e => {
      const c = NET.nodes[e.a].color;
      e.el.style.stroke = (nst.ecol === 'gray' || c === GRAY) ? '' : c;
      e.el.removeAttribute('stroke-dasharray');
    });
    const nw = NET.nodes.filter(x => x.kind === 'w').length, nt = NET.nodes.length - nw;
    const used = new Set(NET.edges.map(e => e.b)).size;
    $('necnt').textContent = `作品 ${nw}・トピック ${nt}（うち線でつながる ${used}）・線 ${NET.edges.length} 本（1作品あたり平均 ${(NET.edges.length / Math.max(1, nw)).toFixed(1)} 本）`;
    return;
  }
  let nin = 0, nb = 0;
  NET.edges.forEach(e => {
    const c = NET.nodes[e.a].color;
    if (e.inside) nin++; else nb++;
    if (nst.ecol === 'gray' || (e.inside && c === GRAY)) { e.el.style.stroke = ''; e.el.removeAttribute('stroke-dasharray'); }
    else if (e.inside) { e.el.style.stroke = c; e.el.removeAttribute('stroke-dasharray'); }
    else { e.el.style.stroke = ''; e.el.setAttribute('stroke-dasharray', '5 4'); }
  });
  NET.nin = nin; NET.nb = nb;
  const s = $('necnt');
  if (s) s.textContent = NET.edges.length ? `内側の辺 ${nin} 本・境界の辺 ${nb} 本（境界 ${(100 * nb / NET.edges.length).toFixed(0)}%）` : '';
}
function applyView(){
  const vp = document.getElementById('netvp');
  if (vp) vp.setAttribute('transform', `translate(${NET.tx},${NET.ty}) scale(${NET.scale})`);
}

// ======================================================================
// レイアウト。Fruchterman–Reingold（従来の配置）は tick() で動かしながら落ち着かせる。
// ほかは一度に計算して置く（乱数は種を固定し，同じ条件なら同じ配置になる）。
//   ForceAtlas2  Jacomy et al. (2014)。斥力は (次数+1) の積に比例，LinLog は Noack (2007)
//   Yifan Hu     Hu (2005)。辺の縮約で粗いグラフを作り，粗いほうから配置して細かくする
//   MDS          Gansner, Koren & North (2005) の stress majorisation。古典的 MDS を初期値にする
//   Circular     分類（色）ごとにまとめて円周上に並べる
// ======================================================================
function prng(seed){
  return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
}
function layEdges(){ return NET.edges.map(e => [e.a, e.b, 0.25 + e.rel]); }
function initCircle(){
  const n = NET.nodes.length, R = Math.min(NET.W, NET.H) * 0.38;
  NET.nodes.forEach((nd, i) => {
    const a = 2 * Math.PI * i / Math.max(1, n);
    nd.x = NET.W / 2 + R * Math.cos(a); nd.y = NET.H / 2 + R * Math.sin(a);
    nd.vx = 0; nd.vy = 0; nd.fixed = false;
  });
}
function layControls(){
  // LinLog と Gravity はいつも見せ，ForceAtlas2 以外では薄くして押せないようにする
  const on = nst.lay === 'fa2';
  ['nlinw', 'ngrw'].forEach(id => { $(id).classList.toggle('off', !on);
    $(id).title = on ? '' : 'ForceAtlas2 を選んだときに効く'; });
  $('nlin').disabled = !on; $('ngr').disabled = !on;
  $('nlin').checked = nst.lin; $('ngr').value = nst.grav; $('ngrO').textContent = nst.grav.toFixed(1);
  $('nlay').value = nst.lay;
}
function stopSim(){ cancelAnimationFrame(NET.raf); NET.alpha = 0; }

function startLayout(){
  stopSim();
  initCircle();
  NET.ops = []; NET.moved = new Set();
  $('nlayst').textContent = '';
  if (nst.lay === 'fr' || NET.nodes.length < 2) { NET.alpha = 1; tick(); return; }
  const f = {fa2: () => byComponents(layFA2), yh: () => byComponents(layYH), mds: layMDS, circ: layCirc}[nst.lay];
  draw();
  $('nlayst').textContent = '配置を計算しています…';
  const gen = NET.gen = (NET.gen || 0) + 1;
  setTimeout(() => {
    if (gen !== NET.gen) return;             // 途中で条件が変わったら捨てる
    const t0 = performance.now();
    const p = f();
    placeFit(p.x, p.y);
    draw();
    if (!NET.userView) fitView();
    $('nlayst').textContent = `（計算 ${((performance.now() - t0) / 1000).toFixed(2)} 秒）`;
  }, 20);
}
// 計算した座標を，縦横比を保って描画領域に収める
function placeFit(x, y){
  const n = x.length;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i < n; i++) { x0 = Math.min(x0, x[i]); x1 = Math.max(x1, x[i]); y0 = Math.min(y0, y[i]); y1 = Math.max(y1, y[i]); }
  const pad = 40, w = Math.max(1e-9, x1 - x0), h = Math.max(1e-9, y1 - y0);
  const s = Math.min((NET.W - 2 * pad) / w, (NET.H - 2 * pad) / h);
  NET.nodes.forEach((p, i) => {
    p.x = NET.W / 2 + (x[i] - (x0 + x1) / 2) * s; p.y = NET.H / 2 + (y[i] - (y0 + y1) / 2) * s;
    p.vx = p.vy = 0;
  });
}

// 力学モデルは連結成分ごとに配置し，辺の平均の長さを揃えてから棚詰めで並べる
// （成分どうしの斥力で全体が広がり，成分の中が潰れて見えるのを防ぐ）
function byComponents(fn){
  const n = NET.nodes.length, E = layEdges();
  const par = [...Array(n).keys()];
  const find = i => { while (par[i] !== i) { par[i] = par[par[i]]; i = par[i]; } return i; };
  E.forEach(([a, b]) => { const p = find(a), q = find(b); if (p !== q) par[Math.max(p, q)] = Math.min(p, q); });
  const groups = new Map();
  for (let i = 0; i < n; i++) { const r = find(i); if (!groups.has(r)) groups.set(r, []); groups.get(r).push(i); }
  const comps = [...groups.values()].sort((a, b) => b.length - a.length || a[0] - b[0]);
  const x = new Float64Array(n), y = new Float64Array(n);
  const boxes = comps.map(ids => {
    if (ids.length === 1) return {ids, px: [0], py: [0], w: 0, h: 0};
    const loc = new Map(ids.map((g, k) => [g, k]));
    const sub = E.filter(e => loc.has(e[0])).map(([a, b, w]) => [loc.get(a), loc.get(b), w]);
    const r = fn(ids.length, sub);
    let m = 0; sub.forEach(([a, b]) => { m += Math.hypot(r.x[a] - r.x[b], r.y[a] - r.y[b]); });
    m = m / sub.length || 1;
    const px = Array.from(r.x, v => v / m), py = Array.from(r.y, v => v / m);
    const x0 = Math.min(...px), y0 = Math.min(...py);
    return {ids, px: px.map(v => v - x0), py: py.map(v => v - y0), w: Math.max(...px) - x0, h: Math.max(...py) - y0};
  });
  const gap = 1.5, area = boxes.reduce((s, b) => s + (b.w + gap) * (b.h + gap), 0);
  const rowW = Math.max(boxes[0].w, Math.sqrt(area * NET.W / NET.H));
  let cx = 0, cy = 0, rh = 0;
  boxes.forEach(b => {
    if (cx > 0 && cx + b.w > rowW) { cx = 0; cy += rh + gap; rh = 0; }
    b.ids.forEach((g, k) => { x[g] = cx + b.px[k]; y[g] = cy + b.py[k]; });
    cx += b.w + gap; rh = Math.max(rh, b.h);
  });
  return {x, y};
}

// ---- ForceAtlas2 ---------------------------------------------------------
function layFA2(n, E){
  const mass = new Array(n).fill(1); E.forEach(([a, b]) => { mass[a]++; mass[b]++; });
  const rnd = prng(7);
  const x = new Float64Array(n), y = new Float64Array(n);
  for (let i = 0; i < n; i++) { x[i] = (rnd() - 0.5) * 100; y[i] = (rnd() - 0.5) * 100; }
  const kr = n > 100 ? 2 : 10, kg = nst.grav, lin = nst.lin;
  let dx = new Float64Array(n), dy = new Float64Array(n), ox = new Float64Array(n), oy = new Float64Array(n);
  let speed = 1, speedEff = 1;
  const iters = n > 600 ? 150 : n > 200 ? 300 : 600;
  for (let it = 0; it < iters; it++) {
    [ox, dx] = [dx, ox]; [oy, dy] = [dy, oy]; dx.fill(0); dy.fill(0);
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      let rx = x[i] - x[j], ry = y[i] - y[j], d2 = rx * rx + ry * ry;
      if (d2 < 1e-6) { rx = 1e-3 * (i - j - 0.5); ry = 1e-3; d2 = rx * rx + ry * ry; }
      const f = kr * mass[i] * mass[j] / d2;            // 大きさ kr·m_i·m_j / d
      dx[i] += rx * f; dy[i] += ry * f; dx[j] -= rx * f; dy[j] -= ry * f;
    }
    for (let i = 0; i < n; i++) {                       // 重力（距離によらない一定の強さ）
      const d = Math.hypot(x[i], y[i]);
      if (d > 0) { const f = kg * mass[i] / d; dx[i] -= x[i] * f; dy[i] -= y[i] * f; }
    }
    E.forEach(([a, b, w]) => {                          // 引力 d（LinLog は log(1+d)）
      const rx = x[a] - x[b], ry = y[a] - y[b], d = Math.hypot(rx, ry);
      if (d === 0) return;
      const f = lin ? w * Math.log(1 + d) / d : w;
      dx[a] -= rx * f; dy[a] -= ry * f; dx[b] += rx * f; dy[b] += ry * f;
    });
    // 速さの自動調整（swinging と traction）
    let swg = 0, tra = 0;
    const sw = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      sw[i] = Math.hypot(ox[i] - dx[i], oy[i] - dy[i]);
      swg += mass[i] * sw[i]; tra += mass[i] * Math.hypot(ox[i] + dx[i], oy[i] + dy[i]) / 2;
    }
    if (!(tra > 0)) break;
    const estJ = 0.05 * Math.sqrt(n), minJ = Math.sqrt(estJ);
    let jt = Math.max(minJ, Math.min(10, estJ * tra / (n * n)));
    if (swg / tra > 2) { if (speedEff > 0.05) speedEff *= 0.5; jt = Math.max(jt, 1); }
    const target = swg > 0 ? jt * speedEff * tra / swg : speed * 1.5;
    if (swg > jt * tra) { if (speedEff > 0.05) speedEff *= 0.7; } else if (speed < 1000) speedEff *= 1.3;
    speed = speed + Math.min(target - speed, 0.5 * speed);
    for (let i = 0; i < n; i++) {
      const fac = speed / (1 + Math.sqrt(speed * sw[i]));
      x[i] += dx[i] * fac; y[i] += dy[i] * fac;
    }
  }
  return {x, y};
}

// ---- Yifan Hu（多段階）----------------------------------------------------
function layYH(n, E){
  const levels = [{n, E}], maps = [];
  while (levels[levels.length - 1].n > 8 && levels.length < 25) {
    const L = levels[levels.length - 1];
    const adj = Array.from({length: L.n}, () => []);
    L.E.forEach(([a, b, w]) => { if (a !== b) { adj[a].push([b, w]); adj[b].push([a, w]); } });
    const order = [...Array(L.n).keys()].sort((a, b) => adj[a].length - adj[b].length || a - b);
    const par = new Array(L.n).fill(-1); let nc = 0;
    order.forEach(i => {                 // 辺の縮約（重みの大きい相手と組にする）
      if (par[i] >= 0) return;
      let best = -1, bw = -1;
      adj[i].forEach(([j, w]) => { if (par[j] < 0 && w > bw) { bw = w; best = j; } });
      par[i] = nc; if (best >= 0) par[best] = nc; nc++;
    });
    if (nc > 0.8 * L.n) break;
    const agg = new Map();
    L.E.forEach(([a, b, w]) => { const p = par[a], q = par[b]; if (p === q) return;
      const k = Math.min(p, q) + ',' + Math.max(p, q); agg.set(k, (agg.get(k) || 0) + w); });
    maps.push(par);
    levels.push({n: nc, E: [...agg].map(([k, w]) => { const [a, b] = k.split(',').map(Number); return [a, b, w]; })});
  }
  const rnd = prng(11);
  let L = levels[levels.length - 1];
  let x = new Float64Array(L.n), y = new Float64Array(L.n);
  const s0 = Math.sqrt(L.n);
  for (let i = 0; i < L.n; i++) { x[i] = rnd() * s0; y[i] = rnd() * s0; }
  huRefine(x, y, L, 400, 1);
  for (let l = levels.length - 2; l >= 0; l--) {
    const par = maps[l], F = levels[l], sc = Math.sqrt(F.n / levels[l + 1].n);
    const nx = new Float64Array(F.n), ny = new Float64Array(F.n);
    for (let i = 0; i < F.n; i++) { nx[i] = x[par[i]] * sc + (rnd() - 0.5) * 0.2; ny[i] = y[par[i]] * sc + (rnd() - 0.5) * 0.2; }
    x = nx; y = ny;
    huRefine(x, y, F, l === 0 ? 300 : 150, 0.3);
  }
  return {x, y};
}
// 斥力 C·K²/d，引力 d²/K の spring-electrical モデル。歩幅は Hu の適応的な冷却で決める
function huRefine(x, y, L, iters, step){
  const n = L.n, K = 1, C = 0.2, t = 0.9;
  const mw = L.E.length ? L.E.reduce((s, e) => s + e[2], 0) / L.E.length : 1;
  const fx = new Float64Array(n), fy = new Float64Array(n);
  let E0 = Infinity, progress = 0;
  for (let it = 0; it < iters; it++) {
    fx.fill(0); fy.fill(0);
    let cx = 0, cy = 0;
    for (let i = 0; i < n; i++) { cx += x[i]; cy += y[i]; }
    cx /= n; cy /= n;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      let rx = x[i] - x[j], ry = y[i] - y[j], d2 = rx * rx + ry * ry;
      if (d2 < 1e-9) { rx = 1e-3 * (i - j - 0.5); ry = 1e-3; d2 = rx * rx + ry * ry; }
      const f = C * K * K / d2;
      fx[i] += rx * f; fy[i] += ry * f; fx[j] -= rx * f; fy[j] -= ry * f;
    }
    L.E.forEach(([a, b, w]) => {
      const rx = x[a] - x[b], ry = y[a] - y[b], d = Math.hypot(rx, ry), f = d / K * (w / mw);
      fx[a] -= rx * f; fy[a] -= ry * f; fx[b] += rx * f; fy[b] += ry * f;
    });
    let energy = 0, moved = 0;
    for (let i = 0; i < n; i++) {
      fx[i] -= 0.02 * (x[i] - cx); fy[i] -= 0.02 * (y[i] - cy);   // 連結でない部分が離れすぎないように
      const m = Math.hypot(fx[i], fy[i]); energy += m * m;
      if (m > 0) { x[i] += step * fx[i] / m; y[i] += step * fy[i] / m; moved += step; }
    }
    if (energy < E0) { if (++progress >= 5) { progress = 0; step /= t; } } else { progress = 0; step *= t; }
    E0 = energy;
    if (moved < 1e-3 * K * n) break;
  }
}

// ---- MDS（stress majorisation）--------------------------------------------
function layMDS(){
  const n = NET.nodes.length;
  const D = Array.from({length: n}, () => new Float64Array(n));
  if (NET.mat && NET.kind !== 'bip') {
    // 指標を非類似度にする（小さいほど近い指標はそのまま，大きいほど近い指標は 1 − 値）
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const v = (NET.mat[i][j] + NET.mat[j][i]) / 2;
      D[i][j] = D[j][i] = Math.max(0, NET.dir < 0 ? v : 1 - v);
    }
  } else {
    // 作品とトピックのネットワーク：辺をたどる最短経路の長さ（つながらない組は最大値＋1）
    const adj = Array.from({length: n}, () => []);
    NET.edges.forEach(e => { adj[e.a].push(e.b); adj[e.b].push(e.a); });
    let mx = 0;
    for (let s = 0; s < n; s++) {
      const d = new Int32Array(n).fill(-1); d[s] = 0; const q = [s];
      for (let h = 0; h < q.length; h++) adj[q[h]].forEach(v => { if (d[v] < 0) { d[v] = d[q[h]] + 1; q.push(v); } });
      for (let j = 0; j < n; j++) { D[s][j] = d[j]; if (d[j] > mx) mx = d[j]; }
    }
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (D[i][j] < 0) D[i][j] = mx + 1;
  }
  let mx = 0;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (D[i][j] > mx) mx = D[i][j];
  const eps = mx > 0 ? mx * 1e-3 : 1;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (i !== j && D[i][j] < eps) D[i][j] = eps;
  // 古典的 MDS（二重中心化した −D²/2 の上位 2 固有ベクトル，べき乗法）
  const B = Array.from({length: n}, (_, i) => Float64Array.from(D[i], v => -0.5 * v * v));
  const rm = B.map(r => r.reduce((s, v) => s + v, 0) / n), gm = rm.reduce((s, v) => s + v, 0) / n;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) B[i][j] += gm - rm[i] - rm[j];
  const eig = prev => {
    let v = Float64Array.from({length: n}, (_, i) => ((i * 7919) % 97) / 97 - 0.5), lam = 0;
    for (let it = 0; it < 120; it++) {
      if (prev) { const p = v.reduce((s, x, i) => s + x * prev[i], 0); for (let i = 0; i < n; i++) v[i] -= p * prev[i]; }
      const w = new Float64Array(n);
      for (let i = 0; i < n; i++) { let s = 0; const r = B[i]; for (let j = 0; j < n; j++) s += r[j] * v[j]; w[i] = s; }
      const nm = Math.hypot(...w) || 1; lam = w.reduce((s, x, i) => s + x * v[i], 0);
      v = w.map(x => x / nm);
    }
    return {v, lam};
  };
  const e1 = eig(null), e2 = eig(e1.v);
  const rnd = prng(5);
  const x = new Float64Array(n), y = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    x[i] = e1.lam > 0 ? e1.v[i] * Math.sqrt(e1.lam) : rnd() * mx;
    y[i] = e2.lam > 0 ? e2.v[i] * Math.sqrt(e2.lam) : rnd() * mx;
  }
  // stress majorisation（重み d⁻²）
  const iters = n > 600 ? 40 : n > 200 ? 80 : 200;
  for (let it = 0; it < iters; it++) {
    for (let i = 0; i < n; i++) {
      let sx = 0, sy = 0, sw = 0;
      for (let j = 0; j < n; j++) {
        if (j === i) continue;
        const d = D[i][j], w = 1 / (d * d);
        let rx = x[i] - x[j], ry = y[i] - y[j], r = Math.hypot(rx, ry);
        if (r < 1e-12) { rx = 1e-6; ry = 0; r = 1e-6; }
        sx += w * (x[j] + d * rx / r); sy += w * (y[j] + d * ry / r); sw += w;
      }
      x[i] = sx / sw; y[i] = sy / sw;
    }
  }
  return {x, y};
}

// ---- Circular（分類ごと）---------------------------------------------------
function layCirc(){
  const N = NET.nodes, n = N.length, x = new Float64Array(n), y = new Float64Array(n);
  const rank = new Map((NET.order || []).map((c, i) => [c, i]));
  const ring = idx => {                 // idx の順に，分類の切れ目で 1 ノード分あけて並べる
    const slots = []; let prev = null;
    idx.forEach(i => { if (prev !== null && N[i].cat !== prev) slots.push(-1); slots.push(i); prev = N[i].cat; });
    if (idx.length > 1 && N[idx[0]].cat !== N[idx[idx.length - 1]].cat) slots.push(-1);
    return slots;
  };
  const byCat = arr => arr.sort((a, b) => (rank.get(N[a].cat) ?? 99) - (rank.get(N[b].cat) ?? 99) || a - b);
  if (NET.kind === 'bip') {
    // 外側に作品（分類ごと），内側にトピック（つながる作品の角度の平均の順）
    const ws = byCat(N.map((p, i) => i).filter(i => N[i].kind !== 't'));
    const ts = N.map((p, i) => i).filter(i => N[i].kind === 't');
    const sl = ring(ws), ang = new Float64Array(n);
    sl.forEach((i, k) => { if (i >= 0) { ang[i] = 2 * Math.PI * k / sl.length; x[i] = Math.cos(ang[i]); y[i] = Math.sin(ang[i]); } });
    const bary = new Map(ts.map(t => [t, [0, 0]]));
    NET.edges.forEach(e => { const b = bary.get(e.b); if (b) { b[0] += e.w * Math.cos(ang[e.a]); b[1] += e.w * Math.sin(ang[e.a]); } });
    const ta = t => { const b = bary.get(t); return (b[0] || b[1]) ? Math.atan2(b[1], b[0]) : 9 + t; };
    ts.sort((a, b) => ta(a) - ta(b) || a - b);
    const a0 = ts.length && ta(ts[0]) < 9 ? ta(ts[0]) : 0;   // 等間隔に置き，向きを作品側に合わせる
    ts.forEach((i, k) => { const a = a0 + 2 * Math.PI * k / ts.length; x[i] = 0.5 * Math.cos(a); y[i] = 0.5 * Math.sin(a); });
  } else {
    const sl = ring(byCat([...Array(n).keys()]));
    sl.forEach((i, k) => { if (i >= 0) { const a = 2 * Math.PI * k / sl.length; x[i] = Math.cos(a); y[i] = Math.sin(a); } });
  }
  return {x, y};
}

// ---- 補助の操作 -------------------------------------------------------------
function afterTool(){ draw(); if (!NET.userView) fitView(); }
function toolScale(k){
  stopSim();
  const N = NET.nodes; if (!N.length) return;
  const cx = N.reduce((s, p) => s + p.x, 0) / N.length, cy = N.reduce((s, p) => s + p.y, 0) / N.length;
  N.forEach(p => { p.x = cx + (p.x - cx) * k; p.y = cy + (p.y - cy) * k; });
  (NET.ops = NET.ops || []).push(k > 1 ? `Expansion ×${k.toFixed(2)}` : `Contraction ×${k.toFixed(3)}`);
  afterTool();
}
const nodeRad = p => p.kind === 't' ? p.r * 1.42 : p.r;
function toolNoverlap(){
  stopSim();
  const N = NET.nodes, n = N.length;
  for (let it = 0; it < 400; it++) {
    let moved = false;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const p = N[i], q = N[j];
      let dx = q.x - p.x, dy = q.y - p.y, d = Math.hypot(dx, dy);
      const m = nodeRad(p) + nodeRad(q) + 3;
      if (d >= m) continue;
      if (d < 1e-6) { dx = Math.cos(i + j); dy = Math.sin(i + j); d = 1; }
      const push = (m - d) / 2 + 0.01;
      p.x -= dx / d * push; p.y -= dy / d * push; q.x += dx / d * push; q.y += dy / d * push;
      moved = true;
    }
    if (!moved) break;
  }
  (NET.ops = NET.ops || []).push('Noverlap');
  afterTool();
}
// ラベルとノードを合わせた矩形どうしの重なりを，重なりの浅い向きに押し分ける
function toolLabelAdjust(){
  if (!nst.lab) { toolNoverlap(); return; }
  stopSim(); draw();
  const N = NET.nodes, n = N.length;
  const box = N.map(p => {
    let b; try { b = p.tx.getBBox(); } catch (e) { b = null; }
    const r = nodeRad(p);
    const l = Math.min(-r, b ? b.x - p.x : -r), rr = Math.max(r, b ? b.x + b.width - p.x : r);
    const t = Math.min(-r, b ? b.y - p.y : -r), bt = Math.max(r, b ? b.y + b.height - p.y : r);
    return {l: l - 1.5, r: rr + 1.5, t: t - 1.5, b: bt + 1.5};
  });
  for (let it = 0; it < 400; it++) {
    let moved = false;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const p = N[i], q = N[j], a = box[i], c = box[j];
      const ox = Math.min(p.x + a.r, q.x + c.r) - Math.max(p.x + a.l, q.x + c.l);
      const oy = Math.min(p.y + a.b, q.y + c.b) - Math.max(p.y + a.t, q.y + c.t);
      if (ox <= 0 || oy <= 0) continue;
      if (ox < oy) { const s = (p.x + (a.l + a.r) / 2 <= q.x + (c.l + c.r) / 2 ? 1 : -1) * (ox / 2 + 0.01); p.x -= s; q.x += s; }
      else { const s = (p.y + (a.t + a.b) / 2 <= q.y + (c.t + c.b) / 2 ? 1 : -1) * (oy / 2 + 0.01); p.y -= s; q.y += s; }
      moved = true;
    }
    if (!moved) break;
  }
  (NET.ops = NET.ops || []).push('Label Adjust');
  afterTool();
}

// 形を保って全体を回す（正の角度は時計回り。ラベルは水平のまま）
function toolRotate(deg){
  stopSim();
  const N = NET.nodes; if (!N.length || !isFinite(deg)) return;
  const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
  const cx = N.reduce((t, p) => t + p.x, 0) / N.length, cy = N.reduce((t, p) => t + p.y, 0) / N.length;
  N.forEach(p => { const dx = p.x - cx, dy = p.y - cy; p.x = cx + dx * c - dy * s; p.y = cy + dx * s + dy * c; });
  (NET.ops = NET.ops || []).push(`Rotate ${deg}°`);
  afterTool();
}

// ---- 図に添える条件（再現のための記録）----------------------------------
// モデルの学習条件・グラフの作り方・見た目の設定・配置と補助の操作を，書き出す図の下縁に小さな字で添える
const optText = id => { const s = $(id); return s && s.selectedOptions[0] ? s.selectedOptions[0].textContent.trim() : ''; };
const lastDirs = p => String(p || '').split(/[\\/]/).filter(Boolean).slice(-2).join('/');
function noteLines(){
  const T = M.train || {}, P = T.params || {}, G = D.gen || {};
  const fmt = n => Number(n).toLocaleString('en-GB');
  const L = [];
  // 1. モデル
  let m = `モデル「${M.label}」（${lastDirs(M.dir)}・指紋 ${M.fp}）：MALLET LDA，K = ${M.K}`;
  if (T.alpha_sum != null) m += `，α 合計 ${T.alpha_sum}（` + (T.alpha_min === T.alpha_max ? `全トピック ${T.alpha_min}）` : `トピックごとに ${T.alpha_min}〜${T.alpha_max}）`);
  if (T.beta != null) m += `，β = ${T.beta}`;
  if (P.num_iterations != null) {
    m += `，反復 ${P.num_iterations}，optimize-interval ${P.optimize_interval}（burn-in ${P.optimize_burn_in}），random-seed ${P.random_seed}，threads ${P.num_threads}`;
    if (P.num_threads > 1) m += '（2 以上では同じ seed でも結果が揺れる）';
    if (P.stoplist) m += `，ストップリスト ${lastDirs(P.stoplist.path)}（${P.stoplist.n_words} 語・sha1 ${P.stoplist.sha1}）`;
    if (P.trained_at) m += `，学習 ${P.trained_at.replace('T', ' ')}`;
  } else m += '，反復回数・seed・ストップリストの記録なし（10_mallet.py train で学習し直すと train_params.json に残る）';
  m += `。チャンク ${fmt(M.nChunks || 0)}・作品 ${M.works.length}・トークン ${fmt(M.N)}・語彙 ${fmt(M.nVocab || 0)}。`;
  L.push(m);
  // 2. グラフの作り方
  const nE = NET.edges.length, nN = NET.nodes.length;
  let g;
  if (NET.kind === 'bip') {
    g = `グラフ：作品とトピックの 2 部グラフ（作品のトピック構成＝チャンクの θ の作品平均）。各作品から割合の高い順に ${nst.k} 個・割合 ${nst.minsh}% 以上。ノード ${nN}・辺 ${nE}。`;
  } else {
    const how = NET.kind === 'topic'
      ? `トピック間の近さ：${optText('nmeas')}` + (/delta/.test(nst.meas) ? `（度数上位 ${G.rel_mfw} 語）` : '')
      : (nst.src === 'd2v' && D.d2v ? `作品間の近さ：doc2vec の作品ベクトル（${D.d2v.dim} 次元，${lastDirs(D.d2v.dir)}）のコサイン類似度`
                                    : '作品間の近さ：トピック構成（θ の作品平均）の Jensen–Shannon divergence');
    g = `グラフ：${how}。各ノードから近い順に k = ${nst.k} 本，全組の近さの上位 ${nst.top}% まで。ノード ${nN}・辺 ${nE}`
      + (NET.nin != null ? `（内側 ${NET.nin}・境界 ${NET.nb}）` : '') + '。';
  }
  if (nst.col === 'community') g += ' コミュニティは Louvain 法（いま張られている辺による）。';
  L.push(g);
  // 3. 見た目
  let v = `表示：色＝${optText('ncol')}（8 分類まで色，残りは灰色）`;
  if (NET.kind === 'bip') v += `，トピックの色＝${optText('ntcol')}`;
  v += `，線の色＝${optText('necol')}，線の形＝${optText('ncurve')}，線の太さと濃さ＝${NET.kind === 'bip' ? '作品内の割合' : '近さの順位'}（表示中の辺の中で 0.7〜5.5）`;
  v += `，ノードの大きさ＝${NET.kind === 'topic' ? 'トピックの割合' : 'チャンク数'}の平方根 ×${nst.nsz}%` + (NET.kind === 'bip' ? `（トピック ×${nst.tsz}%）` : '');
  v += `，濃さ：ノード ${nst.nop}%・線 ${nst.eop}%，ラベル ${nst.lab ? nst.fs + 'px' : 'なし'}。`;
  L.push(v);
  // 4. 配置
  let a = `配置：${optText('nlay').split('（')[0]}`;
  if (nst.lay === 'fa2') a += `（LinLog ${nst.lin ? 'あり' : 'なし'}，Gravity ${nst.grav.toFixed(1)}，Scaling ${nNodesScale()}，連結成分ごと，乱数の種は固定）`;
  else if (nst.lay === 'yh') a += '（連結成分ごと，乱数の種は固定）';
  else if (nst.lay === 'fr') a += '（動きながら落ち着く。初期配置は円周上）';
  a += `，補助の操作：${NET.ops && NET.ops.length ? NET.ops.join(' → ') : 'なし'}`;
  if (NET.moved && NET.moved.size) a += `，手で動かしたノード ${NET.moved.size}`;
  if (NET.focus != null) a += `，強調：${NET.nodes[NET.focus] ? NET.nodes[NET.focus].label : ''} の近傍`;
  a += '。';
  L.push(a);
  L.push(`作成：JLit トピックビューア © Tomoji Tabata (DH UOsaka)（ビューア生成 ${String(G.generated || '').replace('T', ' ')}，上位語 ${G.top}・最小度数 ${G.min_count}）。書き出し ${new Date().toLocaleString('sv-SE').slice(0, 16)}。`);
  return L;
}
function nNodesScale(){ return NET.nodes.length > 100 ? 2 : 10; }

// ---- 書き出し（SVG・PDF）-------------------------------------------------
// いま画面にある図を，拡大縮小・移動に関係なく全体が入るように切り出し，
// 見た目（色・太さ・濃さ・破線・文字）を属性に写した単独の SVG にする。
// 背景は白，凡例を下に付ける。強調（押したノードの周り）もそのまま写る
const EXPORT_PROPS = ['stroke', 'stroke-width', 'stroke-opacity', 'stroke-dasharray', 'stroke-linecap', 'stroke-linejoin',
  'fill', 'fill-opacity', 'opacity', 'font-size', 'font-weight', 'font-family', 'paint-order'];
// 欧文（英数字・欧文の記号）の連なりだけを別の書体にする。和文の書体と並べて指定するだけでは，
// 最初の書体しか見ないソフト（Illustrator など）で和文が化けるので，<tspan> に分けて書体を明示する
const LATIN_FF = "'Gill Sans', 'Gill Sans MT', 'Gill Sans Nova'";
const isLatin = ch => /[ -ɏ‐-‧‰-⁞×°]/.test(ch);
function latinRuns(str){
  const out = []; let cur = '', lat = null;
  for (const ch of String(str)) {
    const l = isLatin(ch);
    if (lat === null || l === lat || (ch === ' ' && lat)) { cur += ch; if (lat === null) lat = l; }
    else { out.push([cur, lat]); cur = ch; lat = l; }
  }
  if (cur) out.push([cur, lat]);
  return out;
}
function latinMarkup(str, esc2, jpFF){
  if (nst.latin !== 'gill') return esc2(str);
  return latinRuns(str).map(([t, l]) => l && /[0-9A-Za-z]/.test(t)
    ? `<tspan font-family="${LATIN_FF}, ${jpFF}">${esc2(t)}</tspan>` : esc2(t)).join('');
}
function exportSvgText(){
  const src = $('netsvg'), vp = document.getElementById('netvp');
  if (!vp || !NET.nodes.length) return null;
  const lightMut = '#6b6b66', rootCs = getComputedStyle(document.documentElement);
  const card = rootCs.getPropertyValue('--card').trim(), mut = rootCs.getPropertyValue('--mut').trim();
  const norm = v => { const d = document.createElement('div'); d.style.color = v; document.body.appendChild(d); const r = getComputedStyle(d).color; d.remove(); return r; };
  const cardRGB = norm(card), mutRGB = norm(mut);
  const clone = vp.cloneNode(true);
  clone.removeAttribute('transform'); clone.removeAttribute('id');
  const os = vp.querySelectorAll('*'), cs = clone.querySelectorAll('*');
  os.forEach((o, k) => {
    const c = cs[k];
    if (o.tagName === 'title') return;
    const st = getComputedStyle(o);
    if (st.display === 'none') { c.setAttribute('display', 'none'); return; }
    EXPORT_PROPS.forEach(p => {
      let v = st.getPropertyValue(p);
      if (!v || v === 'normal' || (p === 'stroke-dasharray' && v === 'none')) return;
      if (p === 'stroke' || p === 'fill') {           // 暗い配色でも白地に合う色にする
        if (v === cardRGB) v = '#ffffff'; else if (v === mutRGB) v = lightMut;
      }
      c.setAttribute(p, v);
    });
    c.removeAttribute('class'); c.removeAttribute('style'); c.removeAttribute('data-i');
  });
  // ラベルの縁取り（paint-order）を読めないソフト（Illustrator など）のために，
  // 縁取りだけの文字を下に敷き，本体の文字からは縁取りを外す
  clone.querySelectorAll('text[paint-order]').forEach(t => {
    const halo = t.cloneNode(true);
    halo.removeAttribute('paint-order'); halo.setAttribute('fill', t.getAttribute('stroke') || '#ffffff');
    t.parentNode.insertBefore(halo, t);
    ['stroke', 'stroke-width', 'stroke-linejoin', 'stroke-opacity', 'paint-order'].forEach(p => t.removeAttribute(p));
  });
  if (nst.latin === 'gill') clone.querySelectorAll('text').forEach(t => {
    const jp = t.getAttribute('font-family') || 'sans-serif', txt = t.textContent;
    [...t.childNodes].forEach(ch => { if (ch.nodeType === 3) ch.remove(); });
    latinRuns(txt).forEach(([str, l]) => {
      if (l && /[0-9A-Za-z]/.test(str)) { const sp = document.createElementNS(SVGNS, 'tspan'); sp.setAttribute('font-family', `${LATIN_FF}, ${jp}`); sp.textContent = str; t.appendChild(sp); }
      else t.appendChild(document.createTextNode(str));
    });
  });
  // 切り出す範囲（ラベルを含む）
  const bb = vp.getBBox(), pad = 16;
  const x0 = bb.x - pad, y0 = bb.y - pad, w = bb.width + 2 * pad;
  // 凡例
  const items = [...$('nleg').querySelectorAll('span')].map(sp => ({col: sp.querySelector('i') ? getComputedStyle(sp.querySelector('i')).backgroundColor : '#999', txt: sp.textContent.trim()}));
  const lfs = 11, lh = 18, charW = t => [...t].reduce((s, ch) => s + (ch.charCodeAt(0) > 0x2e7f ? lfs : lfs * 0.6), 0);
  let lx = 0, ly = 0; const pos = [];
  items.forEach(it => { const iw = 14 + charW(it.txt) + 14; if (lx > 0 && lx + iw > w - 2 * pad) { lx = 0; ly += lh; } pos.push([lx, ly]); lx += iw; });
  const legH = items.length ? ly + lh + 8 : 0;
  // 条件の注記（小さな字。幅に合わせて折り返す）
  const nfs = 8, nlh = 11, nW = w - 2 * pad, cw = ch => ch.charCodeAt(0) > 0x2e7f ? nfs : nfs * 0.56;
  const noteRows = [];
  if (nst.note) noteLines().forEach(line => {
    let cur = '', cwid = 0;
    [...line].forEach(ch => { const d = cw(ch); if (cwid + d > nW && cur) { noteRows.push(cur); cur = '　'; cwid = nfs; } cur += ch; cwid += d; });
    if (cur) noteRows.push(cur);
  });
  const noteH = noteRows.length ? noteRows.length * nlh + 10 : 0;
  const h = bb.height + 2 * pad + legH + noteH;
  const esc2 = t => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const ff = getComputedStyle(document.body).fontFamily.replace(/"/g, "'");
  let leg = '';
  items.forEach((it, k) => {
    const [px, py] = pos[k], gx = x0 + pad + px, gy = y0 + bb.height + 2 * pad + py;
    leg += `<rect x="${gx.toFixed(1)}" y="${(gy + 3).toFixed(1)}" width="10" height="10" rx="2" fill="${it.col}"/>`
      + `<text x="${(gx + 14).toFixed(1)}" y="${(gy + 12).toFixed(1)}" font-size="${lfs}" fill="#444" font-family="${ff}">${latinMarkup(it.txt, esc2, ff)}</text>`;
  });
  const s = `<?xml version="1.0" encoding="UTF-8"?>\n`
    + `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${x0.toFixed(1)} ${y0.toFixed(1)} ${w.toFixed(1)} ${h.toFixed(1)}" width="${w.toFixed(0)}" height="${h.toFixed(0)}" font-family="${ff}">\n`
    + `<rect x="${x0.toFixed(1)}" y="${y0.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" fill="#ffffff"/>\n`
    + new XMLSerializer().serializeToString(clone).replace(/ xmlns="http:\/\/www\.w3\.org\/2000\/svg"/g, '') + '\n'
    + (leg ? `<g>${leg}</g>\n` : '')
    + (noteRows.length ? `<g font-size="${nfs}" fill="#555" font-family="${ff}">` + noteRows.map((r, k) =>
        `<text x="${(x0 + pad).toFixed(1)}" y="${(y0 + bb.height + 2 * pad + legH + 4 + (k + 1) * nlh - 2).toFixed(1)}" xml:space="preserve">${latinMarkup(r, esc2, ff)}</text>`).join('') + '</g>\n' : '')
    + '</svg>\n';
  return s;
}
function exportName(ext){
  const kind = {topic: 'topics', work: 'works', bip: 'works_topics'}[NET.kind] || NET.kind;
  const safe = String(M.label || 'model').replace(/[\\/:*?"<>|\s]+/g, '_');
  return `jlit_network_${kind}_${safe}_${nst.lay}.${ext}`;
}
function exportSVG(){
  const s = exportSvgText(); if (!s) return;
  const url = URL.createObjectURL(new Blob([s], {type: 'image/svg+xml'}));
  const a = document.createElement('a'); a.href = url; a.download = exportName('svg');
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
// PDF はブラウザの印刷で作る（印刷先に「PDF に保存」を選ぶ）。図だけを 1 ページに収める。
// 線と文字は画像にならず，拡大しても粗くならない
function exportPDF(){
  const s = exportSvgText(); if (!s) return;
  const svg = s.replace(/^<\?xml[^>]*>\s*/, '').replace(/ width="[\d.]+" height="[\d.]+"/, ' width="100%" height="100%" preserveAspectRatio="xMidYMid meet"');
  const old = document.getElementById('nprint'); if (old) old.remove();
  const fr = document.createElement('iframe');
  fr.id = 'nprint'; fr.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden';
  document.body.appendChild(fr);
  const d = fr.contentDocument;
  d.open();
  d.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>${exportName('pdf')}</title><style>
@page{size:A4 landscape;margin:10mm}html,body{margin:0;height:100%;background:#fff}
.pg{width:277mm;height:190mm}svg{display:block}</style></head><body><div class="pg">${svg}</div></body></html>`);
  d.close();
  setTimeout(() => { fr.contentWindow.focus(); fr.contentWindow.print(); }, 150);
}

function tick(){
  const N = NET.nodes, E = NET.edges, a = NET.alpha;
  const cx = NET.W / 2, cy = NET.H / 2;
  for (let i = 0; i < N.length; i++) {
    const p = N[i];
    for (let j = i + 1; j < N.length; j++) {
      const q = N[j];
      let dx = p.x - q.x, dy = p.y - q.y, d2 = dx * dx + dy * dy;
      if (d2 < 1) { dx = (i - j) * 0.01 + 0.1; dy = 0.1; d2 = 1; }
      const d = Math.sqrt(d2), f = Math.min(2400 / d2, 40) * a;
      const fx = f * dx / d, fy = f * dy / d;
      p.vx += fx; p.vy += fy; q.vx -= fx; q.vy -= fy;
    }
  }
  E.forEach(e => {
    const p = N[e.a], q = N[e.b];
    const dx = q.x - p.x, dy = q.y - p.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
    const L = 40 + 70 * (1 - e.rel);
    const f = (d - L) * 0.06 * (0.4 + e.rel) * a;
    const fx = f * dx / d, fy = f * dy / d;
    p.vx += fx; p.vy += fy; q.vx -= fx; q.vy -= fy;
  });
  N.forEach(p => {
    p.vx += (cx - p.x) * 0.012 * a; p.vy += (cy - p.y) * 0.012 * a;
    if (p.fixed) { p.vx = p.vy = 0; return; }
    p.vx *= 0.6; p.vy *= 0.6;
    p.x += p.vx; p.y += p.vy;
  });
  draw();
  NET.alpha *= 0.975;
  if (NET.alpha > 0.02) NET.raf = requestAnimationFrame(tick);
  else if (!NET.userView) fitView();
}

function draw(){
  NET.edges.forEach(e => {
    const p = NET.nodes[e.a], q = NET.nodes[e.b];
    // 曲線は 2 点を結ぶ 2 次ベジエ。制御点を中点から線の長さの 18% だけ横へずらす（向きはどの線も同じ側）
    let d = `M${p.x.toFixed(1)} ${p.y.toFixed(1)} `;
    if (nst.curve) { const cx = (p.x + q.x) / 2 - (q.y - p.y) * 0.18, cy = (p.y + q.y) / 2 + (q.x - p.x) * 0.18;
      d += `Q${cx.toFixed(1)} ${cy.toFixed(1)} `; } else d += 'L';
    e.el.setAttribute('d', d + `${q.x.toFixed(1)} ${q.y.toFixed(1)}`);
  });
  NET.nodes.forEach(p => {
    if (p.kind === 't') { p.el.setAttribute('x', (p.x - p.r).toFixed(1)); p.el.setAttribute('y', (p.y - p.r).toFixed(1)); }
    else { p.el.setAttribute('cx', p.x.toFixed(1)); p.el.setAttribute('cy', p.y.toFixed(1)); }
    p.tx.setAttribute('x', p.x.toFixed(1)); p.tx.setAttribute('y', (p.y - p.r - 3).toFixed(1));
  });
}

function reheat(x){ NET.alpha = Math.max(NET.alpha, x); cancelAnimationFrame(NET.raf); NET.raf = requestAnimationFrame(tick); }

// ---- 近傍を強調する ----------------------------------------------------
function focusNode(i){
  NET.focus = i;
  const nb = new Set([i]);
  NET.edges.forEach(e => { if (e.a === i) nb.add(e.b); if (e.b === i) nb.add(e.a); });
  NET.nodes.forEach((p, j) => { p.el.classList.toggle('dim', i !== null && !nb.has(j)); p.tx.classList.toggle('dim', i !== null && !nb.has(j)); p.el.classList.toggle('foc', j === i); });
  NET.edges.forEach(e => e.el.classList.toggle('dim', i !== null && e.a !== i && e.b !== i));
  if (i === null) { $('ninfo').innerHTML = ''; return; }
  if (NET.kind === 'bip') { focusBip(i); return; }
  // 近い順の一覧（辺の有無にかかわらず上位10）
  const row = NET.mat[i];
  const lst = row.map((v, j) => ({j, v})).filter(x => x.j !== i).sort((a, b) => NET.dir * (b.v - a.v)).slice(0, 10);
  const nd = NET.nodes[i];
  let head, extra = '';
  if (NET.kind === 'topic') {
    head = `${nd.label}　全体の ${(100 * M.prev[i]).toFixed(1)}%　<a href="#" data-go="${i}">このトピックの詳細へ</a>`;
  } else {
    const w = nd.w;
    head = `${esc(w[1])}『${esc(w[2])}』${w[3] ? '（' + esc(w[3]) + '）' : ''}　${esc(String(w[4]).replace(/^\d_/, ''))}`;
    // この作品で割合の高いトピック（いま選んでいるモデル）
    const wi = M.works.findIndex(x => x[0] === w[0]);
    if (wi >= 0) {
      const tops = M.workTopic[wi].map((p, t) => ({t, p})).sort((a, b) => b.p - a.p).slice(0, 5);
      extra = `<p class="hint">この作品で割合の高いトピック（モデル「${esc(M.label)}」）：` + tops.map(x =>
        `<a href="#" data-go="${x.t}">T${String(x.t).padStart(2, '0')}</a> ${(100 * x.p).toFixed(1)}%`).join('・') + '</p>';
    }
  }
  const name = j => NET.kind === 'topic'
    ? `T${String(j).padStart(2, '0')}　<span class="hint">${ranked(j).list.slice(0, 6).map(x => esc(disp(M.vocab[x.j][0]))).join(' ')}</span>`
    : `${esc(NET.nodes[j].w[1])}『${esc(NET.nodes[j].w[2])}』`;
  $('ninfo').innerHTML = `<h3>${head}</h3>${extra}<table><tr><th>近い順</th><th></th><th class="num">値</th></tr>` +
    lst.map((x, r) => `<tr class="go" data-f="${x.j}"><td class="num">${r + 1}</td><td>${name(x.j)}</td><td class="num">${x.v.toFixed(3)}</td></tr>`).join('') +
    `</table><p class="hint">値は${NET.dir > 0 ? '大きいほど' : '小さいほど'}近い。行を押すとその${NET.kind === 'topic' ? 'トピック' : '作品'}に移る。背景を押すと強調を解く。</p>`;
  $('ninfo').querySelectorAll('[data-go]').forEach(a => a.onclick = ev => { ev.preventDefault(); goTopic(+a.dataset.go); });
  $('ninfo').querySelectorAll('tr[data-f]').forEach(tr => tr.onclick = () => focusNode(+tr.dataset.f));
}

// ---- 操作（ドラッグ・拡大縮小・ポインタ）-------------------------------
function focusBip(i){
  const nd = NET.nodes[i], nW = M.works.length;
  const pct = x => (100 * x).toFixed(1) + '%';
  let html;
  if (nd.kind === 't') {
    const t = nd.t;
    const ws = M.works.map((w, j) => ({j, v: M.workTopic[j][t]})).sort((a, b) => b.v - a.v).slice(0, 10);
    html = `<h3>${esc(nd.label)}　全体の ${pct(M.prev[t])}　<a href="#" data-go="${t}">このトピックの詳細へ</a></h3>
      <table><tr><th>このトピックの割合が高い作品</th><th class="num">作品内の割合</th></tr>` +
      ws.map(x => `<tr class="go" data-f="${x.j}"><td>${esc(M.works[x.j][1])}『${esc(M.works[x.j][2])}』</td><td class="num">${pct(x.v)}</td></tr>`).join('') + '</table>';
  } else {
    const w = nd.w;
    const ts = M.workTopic[nd.id].map((v, t) => ({t, v})).sort((a, b) => b.v - a.v).slice(0, 10);
    html = `<h3>${esc(w[1])}『${esc(w[2])}』${w[3] ? '（' + esc(w[3]) + '）' : ''}　${esc(String(w[4]).replace(/^\d_/, ''))}</h3>
      <table><tr><th>この作品で割合の高いトピック</th><th class="num">作品内の割合</th></tr>` +
      ts.map(x => `<tr class="go" data-f="${nW + x.t}"><td>T${String(x.t).padStart(2, '0')} ${esc(labelOf(x.t) ? labelOf(x.t).label : autoLabel(x.t))}</td><td class="num">${pct(x.v)}</td></tr>`).join('') + '</table>';
  }
  $('ninfo').innerHTML = html + '<p class="hint">行を押すとその作品・トピックに移る。背景を押すと強調を解く。</p>';
  $('ninfo').querySelectorAll('[data-go]').forEach(a => a.onclick = ev => { ev.preventDefault(); goTopic(+a.dataset.go); });
  $('ninfo').querySelectorAll('tr[data-f]').forEach(tr => tr.onclick = () => focusNode(+tr.dataset.f));
}

function svgPoint(ev){
  const svg = $('netsvg'), r = svg.getBoundingClientRect();
  const x = (ev.clientX - r.left) * NET.W / r.width, y = (ev.clientY - r.top) * NET.H / r.height;
  return {x, y, gx: (x - NET.tx) / NET.scale, gy: (y - NET.ty) / NET.scale};
}
let DRAG = null, LASTCLICK = {i: -1, t: 0};
// ダブルクリック：トピックは詳細へ，作品は固定を解いて力学に戻す
function nodeDouble(nd){
  if (NET.kind === 'topic') goTopic(nd.id);
  else if (nd.kind === 't') goTopic(nd.t);
  else if (nst.lay === 'fr') { nd.fixed = false; reheat(0.2); }
}
function netEvents(){
  const svg = $('netsvg');
  svg.addEventListener('pointerdown', ev => {
    const t = ev.target;
    const pt = svgPoint(ev);
    if (t.classList && t.classList.contains('node')) {
      const nd = NET.nodes[+t.dataset.i];
      DRAG = {kind: 'node', nd, moved: false, sx: pt.x, sy: pt.y, was: nd.fixed};
      nd.fixed = true;
    } else {
      DRAG = {kind: 'pan', sx: pt.x, sy: pt.y, tx: NET.tx, ty: NET.ty, moved: false};
    }
    svg.setPointerCapture(ev.pointerId);
  });
  svg.addEventListener('pointermove', ev => {
    const pt = svgPoint(ev);
    if (DRAG) {
      if (Math.abs(pt.x - DRAG.sx) + Math.abs(pt.y - DRAG.sy) > 3) DRAG.moved = true;
      if (DRAG.kind === 'node' && DRAG.moved) { NET.userView = true; (NET.moved = NET.moved || new Set()).add(DRAG.nd.id); DRAG.nd.x = pt.gx; DRAG.nd.y = pt.gy; if (nst.lay === 'fr') reheat(0.15); draw(); }
      if (DRAG.kind === 'pan' && DRAG.moved) { NET.userView = true; NET.tx = DRAG.tx + pt.x - DRAG.sx; NET.ty = DRAG.ty + pt.y - DRAG.sy; applyView(); }
      $('ntip').hidden = true;
      return;
    }
    const t = ev.target;
    if (t.classList && t.classList.contains('node')) showTip(+t.dataset.i, ev); else $('ntip').hidden = true;
  });
  svg.addEventListener('pointerup', ev => {
    if (!DRAG) return;
    const d = DRAG; DRAG = null;
    if (d.kind === 'node') {
      if (!d.moved) {
        d.nd.fixed = d.was;           // 押しただけなら固定の状態を変えない
        // ダブルクリックはここで見分ける（ポインタを捕まえているので dblclick の宛先がノードにならない）
        const now = Date.now(), idx = NET.nodes.indexOf(d.nd);
        if (LASTCLICK.i === idx && now - LASTCLICK.t < 400) { LASTCLICK = {i: -1, t: 0}; nodeDouble(d.nd); return; }
        LASTCLICK = {i: idx, t: now};
        focusNode(idx);
      }
    } else if (!d.moved) focusNode(null);
  });
  svg.addEventListener('pointerleave', () => { $('ntip').hidden = true; });

  svg.addEventListener('wheel', ev => {
    ev.preventDefault();
    const pt = svgPoint(ev);
    const k = Math.exp(-ev.deltaY * 0.0015);
    const s = Math.min(6, Math.max(0.3, NET.scale * k));
    NET.tx = pt.x - (pt.x - NET.tx) * s / NET.scale;
    NET.ty = pt.y - (pt.y - NET.ty) * s / NET.scale;
    NET.scale = s; NET.userView = true; applyView();
  }, {passive: false});
}
function showTip(i, ev){
  const nd = NET.nodes[i], tip = $('ntip');
  let h;
  if (NET.kind === 'topic' || nd.kind === 't') {
    const tid = nd.kind === 't' ? nd.t : nd.id;
    const lb = labelOf(tid);
    h = `<b>${esc(nd.label)}</b>　${(100 * M.prev[tid]).toFixed(1)}%${NET.kind === 'topic' ? '・' + esc(nd.cat) : ''}${nd.tcat ? '・色：' + esc(nd.tcat) : ''}<br>` +
      (lb ? `ラベル：${esc(lb.label)}（${esc(lb.type)}・確信度 ${esc(lb.confidence)}）<br>` : `仮ラベル：${esc(autoLabel(tid))}<br>`) +
      ranked(tid).list.slice(0, 8).map(x => esc(disp(M.vocab[x.j][0]))).join(' ');
  } else {
    const w = nd.w;
    h = `<b>${esc(w[1])}『${esc(w[2])}』</b>${w[3] ? '　' + esc(w[3]) : ''}<br>${esc(String(w[4]).replace(/^\d_/, ''))}` +
      (w[6] ? '・' + esc(w[6]) : '') + (w[7] ? '・' + esc(w[7]) : '') + `<br>色：${esc(nd.cat)}`;
  }
  tip.innerHTML = h; tip.hidden = false;
  const r = $('netwrap').getBoundingClientRect();
  let x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 12;
  if (x + 260 > r.width) x = Math.max(4, x - 280);
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}

function netInit(){
  document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => setView(b.dataset.v));
  $('nmeas').onchange = e => { nst.meas = e.target.value; buildNet(); };
  $('nsrc').onchange = e => { nst.src = e.target.value; netControls(); buildNet(); };
  $('ncol').onchange = e => { nst.col = e.target.value; buildNet(); };
  $('nk').oninput = e => { nst.k = +e.target.value; $('nkO').textContent = nst.k; buildNet(); };
  $('ntop').oninput = e => { nst.top = +e.target.value; $('ntopO').textContent = nst.top + '%'; buildNet(); };
  $('nmin').oninput = e => { nst.minsh = +e.target.value; $('nminO').textContent = nst.minsh + '%'; buildNet(); };
  $('necol').onchange = e => { nst.ecol = e.target.value; edgeColors(); };
  $('ntcol').onchange = e => { nst.tcol = e.target.value; buildNet(); };
  $('nfs').oninput = e => { nst.fs = +e.target.value; netStyle(); };
  $('nno').oninput = e => { nst.nop = +e.target.value; netStyle(); };
  $('nsz').oninput = e => { nst.nsz = +e.target.value; netStyle(); resizeNodes(); };
  $('ntsz').oninput = e => { nst.tsz = +e.target.value; netStyle(); resizeNodes(); };
  $('neo').oninput = e => { nst.eop = +e.target.value; netStyle(); };
  $('nlab').onchange = e => { nst.lab = e.target.checked; const g = document.querySelector('#netvp > g:last-child'); if (g) g.style.display = nst.lab ? '' : 'none'; };
  $('nre').onclick = () => { NET.scale = 1; NET.tx = NET.ty = 0; buildNet(); };
  $('nlay').onchange = e => { nst.lay = e.target.value; layControls(); NET.userView = false; startLayout(); };
  $('nlin').onchange = e => { nst.lin = e.target.checked; if (nst.lay === 'fa2') { NET.userView = false; startLayout(); } };
  $('ngr').oninput = e => { nst.grav = +e.target.value; $('ngrO').textContent = nst.grav.toFixed(1); };
  $('ngr').onchange = () => { if (nst.lay === 'fa2') { NET.userView = false; startLayout(); } };
  $('nexp').onclick = () => toolScale(1.2);
  $('ncon').onclick = () => toolScale(1 / 1.2);
  $('nnov').onclick = () => toolNoverlap();
  $('nlad').onclick = () => toolLabelAdjust();
  $('nrot').onclick = () => toolRotate(+$('nrotA').value);
  $('nsvg').onclick = () => exportSVG();
  $('nnote').onchange = e => { nst.note = e.target.checked; };
  $('nlatin').onchange = e => { nst.latin = e.target.value; };
  $('ncurve').onchange = e => { nst.curve = e.target.value === 'curve'; draw(); };
  $('npdf').onclick = () => exportPDF();
  layControls();
  netEvents();
}

// ======================================================================
// トピック表の書き出し。トピックとキーワード（主表）と，トピック診断表を
// CSV・LaTeX・Markdown・JSON・HTML（Word などに貼る）で書き出す。
// キャプションにはコーパス・モデル・学習条件・キーワードの選び方を入れる（再現のため）
// ======================================================================
const TW = {ja: {topic: 'トピック', mean: '平均割合', alpha: 'α', label: 'ラベル', kwP: 'キーワード（p(w|t)）', kwN: 'キーワード（度数）',
                 tokens: 'トークン数', coh: 'coherence', exc: 'exclusivity', peak: '最も濃い時代', ltype: 'ラベルの種類', conf: '確信度',
                 prov: '仮ラベル', word: '語', val: '値', cont: '（続き）', sep: '，'},
            en: {topic: 'Topic', mean: 'Mean θ', alpha: 'α', label: 'Label', kwP: 'Keywords (p(w|t))', kwN: 'Keywords (count)',
                 tokens: 'Tokens', coh: 'Coherence', exc: 'Exclusivity', peak: 'Peak period', ltype: 'Label type', conf: 'Confidence',
                 prov: 'Provisional', word: 'Word', val: 'Value', cont: '(continued)', sep: ', '}};
const LTYPE_EN = {'主題': 'Theme', '作品指標': 'Work indicator', '作家指標': 'Author indicator', '文体・機能語': 'Style/function words', '混成': 'Mixed'};
const LCONF_EN = {'高': 'high', '中': 'medium', '低': 'low'};
const tst = {type: 'main', lang: 'ja', corp: 'JLit Corpus 2026', sort: 'id', n: 20, rank: 'raw', wv: 'p', dig: 3,
             cmean: true, calpha: true, clab: true, stand: false, prev: 'tex'};

function tblRows(){
  const K = M.K, pad = Math.max(2, String(K - 1).length), T = M.train || {}, A = T.alpha || null, DG = M.diag || {};
  const ids = [...Array(K).keys()];
  if (tst.sort === 'prev') ids.sort((a, b) => M.prev[b] - M.prev[a] || a - b);
  return ids.map(t => {
    const lb = labelOf(t);
    let words;
    if (tst.rank === 'view') words = ranked(t).list.slice(0, tst.n).map(x => ({w: disp(M.vocab[x.j][0]), p: x.pwt, n: x.n}));
    else words = M.tw[t].slice().sort((a, b) => b[1] - a[1]).slice(0, tst.n)
      .map(([j, c]) => ({w: disp(M.vocab[j][0]), p: c / M.topicTotals[t], n: c}));
    const pk = topicProfile(t).peak;
    return {t, id: String(t).padStart(pad, '0'), mean: M.prev[t], alpha: A ? A[t] : null,
            label: lb ? lb.label : autoLabel(t), prov: !lb, type: lb ? lb.type : '', conf: lb ? lb.confidence : '',
            words, tokens: M.topicTotals[t], coh: DG.coherence ? DG.coherence[t] : null, exc: DG.exclusivity ? DG.exclusivity[t] : null,
            peak: pk ? pk.p : ''};
  });
}
const fnum = (v, d) => v == null || !isFinite(v) ? '' : Number(v).toFixed(d);
const fint = v => Number(v).toLocaleString('en-GB');
function tblWeight(x){ return tst.wv === 'n' ? String(x.n) : fnum(x.p, tst.dig); }

// 絞り込みの条件（「いまの表示設定に従う」のとき）
function filterDesc(lang){
  const parts = [];
  if (st.pos.size < POSGROUPS.length) parts.push((lang === 'en' ? 'parts of speech: ' : '品詞：') + POSGROUPS.filter((_, i) => st.pos.has(i)).map(g => g[0]).join('・'));
  const J = lang === 'en';
  if (st.minc > 1) parts.push((J ? 'corpus frequency ≥ ' : '全体の度数 ≥ ') + st.minc);
  if (st.maxdr < 1) parts.push((J ? 'share of works containing the word ≤ ' : '出現作品の割合 ≤ ') + st.maxdr);
  if (st.maxws < 1) parts.push((J ? 'concentration in one work ≤ ' : '1作品への集中度 ≤ ') + st.maxws);
  if (st.maxas < 1) parts.push((J ? 'concentration in one author ≤ ' : '1作家への集中度 ≤ ') + st.maxas);
  if (st.maxdp < 1) parts.push('dp_in ≤ ' + st.maxdp);
  return parts;
}
function tblCaption(rows){
  const L = tst.lang, T = M.train || {}, P = T.params || {}, K = M.K;
  const nC = fint(M.nChunks || 0), nW = M.works.length, nT = fint(M.N), nV = fint(M.nVocab || 0);
  const corp = tst.corp.trim();
  const anyProv = tst.type === 'main' ? tst.clab && rows.some(r => r.prov) : rows.some(r => r.prov);
  let s;
  if (L === 'en') {
    s = (tst.type === 'main' ? `Topics of the LDA model “${M.label}”` : `Diagnostics of the topics of the LDA model “${M.label}”`)
      + (corp ? ` trained on ${corp}` : '') + ` (${nC} text chunks from ${nW} works; ${nT} tokens; vocabulary ${nV}). MALLET; K = ${K}`;
    if (T.alpha_sum != null) s += `; α: sum ${T.alpha_sum}` + (T.alpha_min === T.alpha_max ? '' : ` (per topic ${T.alpha_min}–${T.alpha_max})`);
    if (T.beta != null) s += `; β = ${T.beta}`;
    if (P.num_iterations != null) s += `; ${fint(P.num_iterations)} iterations; optimize-interval ${P.optimize_interval} (burn-in ${P.optimize_burn_in}); random seed ${P.random_seed}`;
    else s += '; training parameters not recorded';
    s += '.';
    if (tst.type === 'main') {
      const f = filterDesc('en');
      s += tst.rank === 'view'
        ? ` Keywords: the top ${tst.n} words by relevance (λ = ${st.lam}; Sievert & Shirley 2014)` + (f.length ? ` after filtering (${f.join('; ')})` : '')
        : ` Keywords: the top ${tst.n} words by p(w|t)`;
      s += `, each followed by its ${tst.wv === 'n' ? 'count' : 'p(w|t)'}.`;
      if (tst.cmean) s += ' Mean θ: mean topic proportion over text chunks.';
    } else {
      s += ' Mean θ: mean topic proportion over text chunks; Tokens: tokens assigned to the topic';
      if (M.diag && M.diag.coherence) s += '; Coherence and Exclusivity: MALLET diagnostics (Mimno et al. 2011)';
      s += '; Peak period: the period with the highest mean θ.';
    }
    if (tst.sort === 'prev') s += ' Topics are ordered by mean θ.';
    if (anyProv) s += ' Labels marked * are provisional (assigned automatically).';
  } else {
    s = (corp ? `${corp}（` : '（') + `テクストチャンク ${nC}・作品 ${nW}・トークン ${nT}・語彙 ${nV}）で学習した LDA モデル「${M.label}」の`
      + (tst.type === 'main' ? 'トピック' : 'トピック診断') + `。MALLET，K = ${K}`;
    if (T.alpha_sum != null) s += `，α 合計 ${T.alpha_sum}` + (T.alpha_min === T.alpha_max ? '' : `（トピックごとに ${T.alpha_min}〜${T.alpha_max}）`);
    if (T.beta != null) s += `，β = ${T.beta}`;
    if (P.num_iterations != null) s += `，反復 ${fint(P.num_iterations)}，optimize-interval ${P.optimize_interval}（burn-in ${P.optimize_burn_in}），random-seed ${P.random_seed}`;
    else s += '（学習条件の記録なし）';
    s += '。';
    if (tst.type === 'main') {
      const f = filterDesc('ja');
      s += tst.rank === 'view'
        ? `キーワードは relevance（λ = ${st.lam}; Sievert & Shirley 2014）の上位 ${tst.n} 語` + (f.length ? `（絞り込み：${f.join('，')}）` : '')
        : `キーワードは p(w|t) の上位 ${tst.n} 語`;
      s += `で，各語の後に${tst.wv === 'n' ? '度数' : ' p(w|t) '}を示す。`;
      if (tst.cmean) s += '平均割合はテクストチャンクにおけるトピックの割合（θ）の平均。';
    } else {
      s += '平均割合はテクストチャンクにおける θ の平均，トークン数はトピックに割り当てられたトークンの数';
      if (M.diag && M.diag.coherence) s += '，coherence と exclusivity は MALLET の診断値（Mimno et al. 2011）';
      s += '，最も濃い時代は平均割合が最も高い時代区分。';
    }
    if (tst.sort === 'prev') s += 'トピックは平均割合の大きい順に並べた。';
    if (anyProv) s += '＊は仮ラベル（機械的に付けたもの）。';
  }
  return s.replace(/\s+/g, ' ');
}
function tblShort(){ return tst.lang === 'en' ? (tst.type === 'main' ? `Topics of the LDA model “${M.label}”` : `Topic diagnostics (“${M.label}”)`)
                                           : (tst.type === 'main' ? `LDA モデル「${M.label}」のトピック` : `LDA モデル「${M.label}」のトピック診断`); }
// 列の定義：[見出し, 値を返す関数, 揃え（r/l/X）]
function tblCols(){
  const W = TW[tst.lang], en = tst.lang === 'en';
  const lab = r => r.label + (r.prov ? '*' : '');
  if (tst.type === 'main') {
    const c = [[W.topic, r => r.id, 'r']];
    if (tst.cmean) c.push([W.mean, r => fnum(r.mean, 4), 'r']);
    if (tst.calpha && (M.train || {}).alpha) c.push([W.alpha, r => fnum(r.alpha, 4), 'r']);
    if (tst.clab) c.push([W.label, lab, 'L']);
    c.push([tst.wv === 'n' ? W.kwN : W.kwP, null, 'X']);
    return c;
  }
  const c = [[W.topic, r => r.id, 'r'], [W.label, lab, 'X']];
  if ((M.train || {}).alpha) c.push([W.alpha, r => fnum(r.alpha, 4), 'r']);
  c.push([W.mean, r => fnum(r.mean, 4), 'r'], [W.tokens, r => fint(r.tokens), 'r']);
  if (M.diag && M.diag.coherence) c.push([W.coh, r => fnum(r.coh, 2), 'r'], [W.exc, r => fnum(r.exc, 3), 'r']);
  c.push([W.peak, r => r.peak, 'l'],
         [W.ltype, r => r.prov ? (en ? 'provisional' : '仮ラベル') : (en ? (LTYPE_EN[r.type] || r.type) : r.type), 'l'],
         [W.conf, r => r.prov ? '' : (en ? (LCONF_EN[r.conf] || r.conf) : r.conf), 'l']);
  return c;
}
function provenance(){
  return `JLit トピックビューア © Tomoji Tabata (DH UOsaka)｜モデル「${M.label}」指紋 ${M.fp}｜書き出し ${new Date().toLocaleString('sv-SE').slice(0, 16)}`;
}

// ---- 形式ごとの書き出し ------------------------------------------------------
function toCSV(rows){
  const W = TW[tst.lang], q = v => { v = String(v == null ? '' : v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
  const cols = tblCols().filter(c => c[1]);
  const head = cols.map(c => c[0]);
  if (tst.type === 'main') {
    if (tst.clab) head.push(W.prov);
    for (let i = 1; i <= tst.n; i++) head.push(`${W.word} ${i}`, `${tst.wv === 'n' ? (tst.lang === 'en' ? 'Count' : '度数') : 'p(w|t)'} ${i}`); }
  const lines = ['# ' + tblCaption(rows) + ' ｜ ' + provenance(), head.map(q).join(',')];
  rows.forEach(r => {
    const v = cols.map(c => c[0] === W.label ? r.label : c[1](r));
    if (tst.type === 'main') { if (tst.clab) v.push(r.prov ? 1 : 0); r.words.forEach(x => v.push(x.w, tblWeight(x))); }
    lines.push(v.map(q).join(','));
  });
  return '\ufeff' + lines.join('\r\n') + '\r\n';
}
function mdEsc(s){ return String(s).replace(/\\/g, '\\\\').replace(/\|/g, '\\|').replace(/\*/g, '\\*').replace(/_/g, '\\_'); }
function kwText(r, esc, fmtW){
  const W = TW[tst.lang];
  return r.words.map(x => esc(x.w) + fmtW(tblWeight(x))).join(W.sep);
}
function toMD(rows){
  const cols = tblCols();
  const al = c => c[2] === 'r' ? '---:' : '---';
  const out = [`<!-- ${provenance()} -->`, '', '| ' + cols.map(c => mdEsc(c[0])).join(' | ') + ' |', '|' + cols.map(al).join('|') + '|'];
  rows.forEach(r => out.push('| ' + cols.map(c => c[1] ? mdEsc(c[1](r)) : kwText(r, mdEsc, w => ` (${w})`)).join(' | ') + ' |'));
  out.push('', ': ' + mdEsc(tblCaption(rows)) + ' {#tab:' + (tst.type === 'main' ? 'topics' : 'topic-diagnostics') + '}', '');
  return out.join('\n');
}
function lx(s){
  // LaTeX の特殊文字をエスケープし，ギリシャ文字と p(w|t) を数式にする
  s = String(s).replace(/p\(w\|t\)/g, '\u0001');
  s = s.replace(/[\\&%$#_{}~^]/g, ch => ({'\\': '\\textbackslash{}', '&': '\\&', '%': '\\%', '$': '\\$', '#': '\\#', '_': '\\_',
    '{': '\\{', '}': '\\}', '~': '\\textasciitilde{}', '^': '\\textasciicircum{}'})[ch]);
  return s.replace(/\u0001/g, '$p(w\\mid t)$').replace(/α/g, '$\\alpha$').replace(/β/g, '$\\beta$').replace(/θ/g, '$\\theta$')
    .replace(/λ/g, '$\\lambda$').replace(/≥/g, '$\\geq$').replace(/≤/g, '$\\leq$').replace(/×/g, '$\\times$')
    .replace(/\*$/, '\\textsuperscript{*}').replace(/\$\$/g, '');
}
function toTeX(rows){
  const cols = tblCols(), W = TW[tst.lang], nc = cols.length;
  const spec = cols.map(c => c[2] === 'r' ? 'r' : c[2] === 'X' ? '>{\\raggedright\\arraybackslash}X'
    : c[2] === 'L' ? '>{\\raggedright\\arraybackslash}p{' + (tst.type === 'main' ? '6.5em' : '7em') + '}' : 'l').join(' ');
  const head = cols.map(c => lx(c[0])).join(' & ') + ' \\\\';
  const key = tst.type === 'main' ? 'tab:topics' : 'tab:topic-diagnostics';
  const body = rows.map(r => cols.map(c => c[1] ? lx(c[1](r)).replace(/^-(?=\d)/, '$-$') : kwText(r, lx, w => `\\wt{${w}}`)).join(' & ') + ' \\\\').join('\n');
  const tbl = [
    `% ${provenance()}`,
    '% 必要なパッケージ：booktabs, xltabular, array。日本語を含むので LuaLaTeX + luatexja（または upLaTeX）で組む。',
    '\\providecommand{\\wt}[1]{\\,{\\footnotesize(#1)}}',
    '\\begingroup', tst.type === 'main' ? '\\small' : '\\footnotesize', '\\setlength{\\tabcolsep}{4pt}', '\\setlength{\\LTcapwidth}{\\linewidth}',
    `\\begin{xltabular}{\\linewidth}{@{}${spec}@{}}`,
    `\\caption[${lx(tblShort())}]{${lx(tblCaption(rows))}}\\label{${key}}\\\\`,
    '\\toprule', head, '\\midrule', '\\endfirsthead',
    `\\multicolumn{${nc}}{@{}l}{\\footnotesize\\itshape ${lx(W.cont)}}\\\\`, '\\toprule', head, '\\midrule', '\\endhead',
    '\\bottomrule', '\\endlastfoot',
    body, '\\end{xltabular}', '\\endgroup'].join('\n');
  if (!tst.stand) return tbl + '\n';
  return ['% LuaLaTeX で組む：lualatex ' + exportTblName('tex'),
    '\\documentclass[a4paper,10pt]{article}', '\\usepackage[margin=18mm]{geometry}', '\\usepackage{luatexja}',
    '\\usepackage{booktabs,xltabular,array}', ...(tst.lang === 'ja' ? ['\\renewcommand{\\tablename}{表}'] : []), '\\begin{document}', tbl, '\\end{document}', ''].join('\n');
}
function toJSON(rows){
  return JSON.stringify({caption: tblCaption(rows), provenance: provenance(), table: tst.type, lang: tst.lang,
    model: {label: M.label, fingerprint: M.fp, K: M.K, chunks: M.nChunks, works: M.works.length, tokens: M.N, vocabulary: M.nVocab,
            train: M.train || {}},
    keywords: tst.type === 'main' ? {n: tst.n, ranking: tst.rank === 'view' ? {relevance_lambda: st.lam, filters: filterDesc('en')} : 'p(w|t)', value: tst.wv === 'n' ? 'count' : 'p(w|t)'} : undefined,
    topics: rows.map(r => ({topic: r.t, mean_theta: r.mean, alpha: r.alpha, label: r.label, provisional: r.prov, label_type: r.type || null,
      confidence: r.conf || null, tokens: r.tokens, coherence: r.coh, exclusivity: r.exc, peak_period: r.peak,
      keywords: tst.type === 'main' ? r.words.map(x => ({word: x.w, p: +x.p.toFixed(6), count: x.n})) : undefined}))}, null, 1);
}
function toHTML(rows){
  const cols = tblCols(), e = esc;
  const t = `<table style="border-collapse:collapse;font-size:10pt">
<caption style="caption-side:top;text-align:left;padding-bottom:4pt">${e(tblCaption(rows))}</caption>
<thead><tr style="border-top:1.5pt solid #000;border-bottom:.75pt solid #000">${cols.map(c => `<th style="padding:2pt 6pt;text-align:${c[2] === 'r' ? 'right' : 'left'}">${e(c[0])}</th>`).join('')}</tr></thead>
<tbody>${rows.map((r, i) => `<tr${i === rows.length - 1 ? ' style="border-bottom:1.5pt solid #000"' : ''}>` + cols.map(c => `<td style="padding:2pt 6pt;vertical-align:top;text-align:${c[2] === 'r' ? 'right' : 'left'}">`
    + (c[1] ? e(c[1](r)).replace(/^-(?=\d)/, '\u2212') : kwText(r, e, w => ` <span style="font-size:8pt">(${w})</span>`)) + '</td>').join('') + '</tr>').join('\n')}</tbody></table>`;
  return t;
}
function toHTMLDoc(rows){
  return `<!DOCTYPE html><html lang="${tst.lang}"><head><meta charset="utf-8"><title>${esc(tblShort())}</title></head>
<body style="font-family:'Hiragino Sans','Yu Gothic','Noto Sans CJK JP',sans-serif"><!-- ${esc(provenance())} -->
${toHTML(rows)}</body></html>`;
}
function exportTblName(ext){
  const safe = String(M.label || 'model').replace(/[\\/:*?"<>|\s]+/g, '_');
  return `jlit_${tst.type === 'main' ? 'topics' : 'topic_diagnostics'}_${safe}_${tst.lang}.${ext}`;
}
const TFMT = {csv: [toCSV, 'text/csv'], tex: [toTeX, 'application/x-tex'], md: [toMD, 'text/markdown'], json: [toJSON, 'application/json'], html: [toHTMLDoc, 'text/html']};
function tblText(fmt){ return TFMT[fmt][0](tblRows()); }
function tblDownload(fmt){
  const url = URL.createObjectURL(new Blob([tblText(fmt)], {type: TFMT[fmt][1] + ';charset=utf-8'}));
  const a = document.createElement('a'); a.href = url; a.download = exportTblName(fmt);
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
}
function tblView(){
  if (!M) return;
  $('tmainopts').hidden = tst.type !== 'main';
  $('tcalphaw').hidden = !(M.train || {}).alpha;
  const rows = tblRows();
  $('tout').value = tst.prev === 'html' ? toHTML(rows) : TFMT[tst.prev][0](rows).replace(/^\ufeff/, '');
  const noDiag = tst.type === 'diag' && !(M.diag && M.diag.coherence);
  const noPar = !((M.train || {}).params);
  $('tinfo').textContent = [noPar ? '学習条件（反復回数・seed など）の記録が無い：10_mallet.py train で学習し直すと入る。' : '',
    noDiag ? 'diagnostics.xml が無いので coherence と exclusivity は出ない。' : '',
    tst.rank === 'view' ? `いまの表示設定（λ = ${st.lam}${filterDesc('ja').length ? '・' + filterDesc('ja').join('・') : ''}）で選んでいる。` : ''].join(' ');
}
async function tblCopy(){
  const txt = $('tout').value, msg = $('tmsg');
  try {
    if (tst.prev === 'html' && window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({'text/html': new Blob([txt], {type: 'text/html'}), 'text/plain': new Blob([txt], {type: 'text/plain'})})]);
      msg.textContent = '表としてコピーした（Word などに貼り付けられる）';
    } else { await navigator.clipboard.writeText(txt); msg.textContent = 'コピーした'; }
  } catch (e) { $('tout').select(); document.execCommand('copy'); msg.textContent = 'コピーした'; }
  setTimeout(() => { msg.textContent = ''; }, 2500);
}
function tblInit(){
  const bind = (id, key, ev, conv) => { $(id)[ev] = e => { tst[key] = conv(e.target); tblView(); }; };
  bind('ttype', 'type', 'onchange', t => t.value); bind('tlang', 'lang', 'onchange', t => t.value);
  bind('tcorp', 'corp', 'oninput', t => t.value); bind('tsort', 'sort', 'onchange', t => t.value);
  bind('tn', 'n', 'oninput', t => Math.max(1, Math.min(50, +t.value || 20))); bind('trank', 'rank', 'onchange', t => t.value);
  bind('twv', 'wv', 'onchange', t => t.value); bind('tdig', 'dig', 'oninput', t => Math.max(1, Math.min(6, +t.value || 3)));
  bind('tcmean', 'cmean', 'onchange', t => t.checked); bind('tcalpha', 'calpha', 'onchange', t => t.checked);
  bind('tclab', 'clab', 'onchange', t => t.checked); bind('tstand', 'stand', 'onchange', t => t.checked);
  bind('tprev', 'prev', 'onchange', t => t.value);
  document.querySelectorAll('#tableview [data-fmt]').forEach(b => b.onclick = () => tblDownload(b.dataset.fmt));
  $('tcopy').onclick = tblCopy;
}

// ======================================================================
// ラベルづけ。① 診断資料（依頼文）を作って生成 AI に渡し，回答（JSON）を取り込む。
// ② AI を使わない仮ラベルを機械的に付ける。ラベルはモデルの指紋（fp）に結び付ける
// （トピックの番号は学習のたびに変わるので，別のモデルには当てはめない）。
// ======================================================================
const LTYPES = ['主題', '作品指標', '作家指標', '文体・機能語', '混成'];
// 旧い名前（2026年9月まで）で保存・回答されたラベルの種類は，読み込むときに新しい名前にする
const LTYPE_OLD = {'作品の目印': '作品指標', '作家の目印': '作家指標'};
const normType = x => LTYPE_OLD[x] || x;
const normRec = r => (r && r.type && LTYPE_OLD[r.type] ? Object.assign({}, r, {type: LTYPE_OLD[r.type]}) : r);
const LCONF = ['高', '中', '低'];
const LSTORE = 'jlit-topic-labels';
let LAB = {};
const lst = {scope: 'all', batch: 10, page: 0, src: 'Claude'};

function loadLabels(){
  const base = (D.labels && D.labels.models) || {};
  Object.entries(base).forEach(([fp, m]) => { LAB[fp] = {}; Object.entries(m.topics || {}).forEach(([t, r]) => { LAB[fp][t] = normRec(r); }); });
  try {
    const loc = JSON.parse(localStorage.getItem(LSTORE) || '{}');
    Object.entries(loc).forEach(([fp, tops]) => {
      const cur = LAB[fp] || (LAB[fp] = {});
      Object.entries(tops).forEach(([t, r]) => { r = normRec(r); if (!cur[t] || String(r.date || '') >= String(cur[t].date || '')) cur[t] = r; });
    });
  } catch (e) { /* ブラウザが保存を許さないときは，書き出したファイルだけが頼り */ }
}
// 日付は手元の時刻で（toISOString は UTC なので，日本の午前9時前は前日になる）
function today(){ const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function saveLocal(){ try { localStorage.setItem(LSTORE, JSON.stringify(LAB)); } catch (e) {} }
function labelsOf(){ return LAB[M.fp] || (LAB[M.fp] = {}); }
function labelOf(t){ return labelsOf()[t] || null; }

// ---- トピックの輪郭（依頼文と仮ラベルの材料。画面の絞り込みには左右されない）----
function topWords(t, lam, n){
  const Tt = M.topicTotals[t], N = M.N;
  return M.tw[t].map(([j, c]) => {
    const pwt = c / Tt, pw = M.vocab[j][5] / N;
    return {j, pwt, r: lam * Math.log(pwt) + (1 - lam) * Math.log(pwt / pw)};
  }).sort((a, b) => b.r - a.r).slice(0, n);
}
function topicProfile(t){
  const mass = M.works.map((w, i) => M.workTopic[i][t] * w[5]);
  const tot = mass.reduce((s, x) => s + x, 0) || 1;
  const works = M.works.map((w, i) => ({w, v: M.workTopic[i][t], sh: mass[i] / tot}));
  const byInside = works.slice().sort((a, b) => b.v - a.v);
  const byShare = works.slice().sort((a, b) => b.sh - a.sh);
  const au = {};
  works.forEach(x => { const a = (au[x.w[1]] ||= {a: x.w[1], sh: 0, n: 0}); a.sh += x.sh; a.n++; });
  const authors = Object.values(au).sort((a, b) => b.sh - a.sh);
  const per = M.periods.map((p, i) => ({p: p.replace(/^\d_/, ''), v: M.periodTopic[i][t], raw: p}));
  const known = per.filter(x => !/不明/.test(x.raw));
  const mean = known.reduce((s, x) => s + x.v, 0) / Math.max(1, known.length);
  const peak = known.slice().sort((a, b) => b.v - a.v)[0];
  const rel = M.rel.jsd[t].map((v, u) => ({u, v})).filter(x => x.u !== t).sort((a, b) => a.v - b.v).slice(0, 3);
  return {byInside, byShare, authors, per, peak, peakRatio: peak && mean ? peak.v / mean : 1, rel,
          topW: byShare[0], topA: authors[0]};
}
function autoLabel(t){
  const p = topicProfile(t);
  // 仮ラベルも短く：特有の語（λ=0.6）の上位2語。時代区分は年の範囲を外して添える
  const w = topWords(t, 0.6, 2).map(x => disp(M.vocab[x.j][0]));
  // 作家名・作品名はラベルに入れない（ネットワークや詳細を見れば分かり，入れると図が読みにくい）。
  // 作品指標・作家指標であることだけを［ ］で示す
  if (p.topW && p.topW.sh > 0.5) return `${w.join('・')}［作品］`;
  if (p.topA && p.topA.sh > 0.5 && !/メタデータ無し/.test(p.topA.a)) return `${w.join('・')}［作家］`;
  return w.join('・') + (p.peak && p.peakRatio >= 1.3 ? `［${p.peak.p.replace(/[（(].*$/, '')}］` : '');
}
// ラベルに入っている作家名・作品名（2字以上の題）を探す。見つかれば一覧で注意を出す
function namesIn(label){
  const s = String(label || ''), hit = new Set();
  M.works.forEach(w => {
    const a = String(w[1] || '');
    if (a && !/メタデータ無し/.test(a)) {
      // 姓・号だけで書かれることが多い（藤村・漱石・鴎外）。4字以上の名は前後2字も見る
      const parts = a.length >= 4 ? [a, a.slice(0, 2), a.slice(-2)] : [a];
      if (parts.some(x => s.includes(x))) hit.add(a);
    }
    const ti = String(w[2] || '').replace(/[（(].*$/, '');
    if (ti.length >= 2 && s.includes(ti)) hit.add('『' + ti + '』');
  });
  return [...hit];
}
function shortLabel(s, n){ s = String(s || ''); return s.length > n ? s.slice(0, n) + '…' : s; }

// ---- 依頼文（診断資料）-------------------------------------------------
function dossier(t){
  const p = topicProfile(t);
  const wl = (lam, n) => topWords(t, lam, n).map(x => `${disp(M.vocab[x.j][0])}（${M.vocab[x.j][1].split('-')[0] || '?'}）`).join('、');
  const pct = x => (100 * x).toFixed(1) + '%';
  const wrow = x => `${x.w[1]}『${x.w[2]}』（${x.w[3] || '初出年不明'}・${String(x.w[4]).replace(/^\d_/, '')}${x.w[6] ? '・' + x.w[6] : ''}${x.w[7] ? '・' + x.w[7] : ''}）作品内 ${pct(x.v)}／トピックに占める ${pct(x.sh)}`;
  let warn = '';
  if (p.topW && p.topW.sh > 0.5) warn = `このトピックの重みの ${pct(p.topW.sh)} が1作品（${p.topW.w[1]}『${p.topW.w[2]}』）から来ている。`;
  else if (p.topA && p.topA.sh > 0.5) warn = `このトピックの重みの ${pct(p.topA.sh)} が1作家（${p.topA.a}）の作品から来ている。`;
  return [
    `### トピック ${t}（コーパス全体の ${pct(M.prev[t])}）`,
    `- 上位語（トピック内の確率の順）：${wl(1, 15)}`,
    `- 特有の語（relevance λ=0.6 の順）：${wl(0.6, 15)}`,
    `- 時代区分ごとの割合：${p.per.map(x => `${x.p} ${pct(x.v)}`).join('、')}`,
    `- このトピックの割合が高い作品：\n` + p.byInside.slice(0, 6).map(x => '  - ' + wrow(x)).join('\n'),
    `- このトピックを担う作品（トピックに占める割合の順）：\n` + p.byShare.slice(0, 6).map(x => '  - ' + wrow(x)).join('\n'),
    `- 担う作家：${p.authors.slice(0, 5).map(a => `${a.a} ${pct(a.sh)}`).join('、')}`,
    warn ? `- 偏りの警告：${warn}` : '- 偏りの警告：なし',
    `- 語分布の近いトピック：${p.rel.map(x => `トピック ${x.u}（${topWords(x.u, 0.6, 5).map(y => disp(M.vocab[y.j][0])).join('・')}）`).join('、')}`,
  ].join('\n');
}
function promptFor(topics){
  const head = `あなたは日本近代文学とテキスト分析に詳しい研究補助者です。
近代日本文学のコーパス（青空文庫の作品，明治〜昭和戦後）を約2,000語のチャンクに分け，
LDA（MALLET）でトピックモデルを学習しました。下の「診断資料」をもとに，各トピックを診断し，
ラベルを付けてください。

## 診断の手順
1. 上位語と特有の語から，そのトピックが何でできているかを見る。
2. 担う作品・作家と偏りの警告から，それが**主題**なのか，1作品・1作家の**指標**（作品指標・作家指標）
   （登場人物名・固有の語彙）なのか，**文体・機能語**の偏り（文語・会話体など）なのかを判断する。
3. 時代区分ごとの割合も参考にする。

## 守ること
- 根拠は**資料にある語と作品だけ**から挙げる。作品について一般に知られていることを使ったときは，
  「一般知識」と明記する。資料から言えないことは推測しない。
- 判断がつかないときは，確信度を「低」にし，caution に理由を書く。
- ラベルは**できるだけ短く**する。日本語の名詞句で**2〜8字**を目安とし，**10字を超えない**
  （ネットワークの図にそのまま載るので，短いほど読みやすい）。説明は evidence に回す。
- **作家名・作品名はラベルに入れない**（どの作品・作家に偏るかは図と詳細で分かる）。作品指標・作家指標で
  あっても，何の束かを内容で表す（例：「探偵団」「方言の会話」「漢語の論説」）。
  作品指標・作家指標であることは type で示し，作家名・作品名は evidence に書く。

## 回答の形式
次の JSON **だけ**を返してください（説明の文章は付けない）。

{"model": "${M.fp}", "topics": [
  {"topic": 番号, "label": "ラベル", "type": "${LTYPES.join('｜')} のどれか",
   "confidence": "高｜中｜低", "evidence": "根拠（資料の語・作品を挙げて60字以内）",
   "caution": "注意点（無ければ空文字）"}
]}

## 診断資料（モデル「${M.label}」・${M.K} トピック・指紋 ${M.fp}）
`;
  return head + '\n' + topics.map(dossier).join('\n\n') + '\n';
}

// ---- 回答を取り込む ------------------------------------------------------
function parseAnswer(text){
  let s = String(text || '').trim();
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) s = fence[1];
  const a = s.indexOf('{'), b = s.lastIndexOf('}'), c = s.indexOf('[');
  let obj;
  try { obj = JSON.parse(s); }
  catch (e) {
    try { obj = JSON.parse(a >= 0 && (c < 0 || a < c) ? s.slice(a, b + 1) : s.slice(c, s.lastIndexOf(']') + 1)); }
    catch (e2) { throw new Error('JSON として読めない。回答の JSON の部分だけを貼り付けること。（' + e2.message + '）'); }
  }
  const model = Array.isArray(obj) ? null : obj.model;
  const list = Array.isArray(obj) ? obj : (obj.topics || []);
  return {model, list};
}
function importAnswer(){
  const msg = $('lmsg');
  let r;
  try { r = parseAnswer($('lans').value); }
  catch (e) { msg.className = 'warn'; msg.textContent = e.message; return; }
  if (r.model && r.model !== M.fp) {
    msg.className = 'warn';
    msg.textContent = `別のモデル（指紋 ${r.model}）への回答である。いま開いているのはモデル「${M.label}」（指紋 ${M.fp}）。取り込まなかった。`;
    return;
  }
  const L = labelsOf(), ok = [], ng = [];
  const date = today();
  const src = $('lsrc').value === 'その他' ? ($('lsrc2').value.trim() || 'その他') : $('lsrc').value;
  r.list.forEach(x => {
    const t = +x.topic;
    if (!Number.isInteger(t) || t < 0 || t >= M.K || !String(x.label || '').trim()) { ng.push(x.topic); return; }
    L[t] = {label: String(x.label).trim().slice(0, 40),
            type: LTYPES.includes(normType(x.type)) ? normType(x.type) : '混成',
            confidence: LCONF.includes(x.confidence) ? x.confidence : '低',
            evidence: String(x.evidence || '').slice(0, 300),
            caution: String(x.caution || '').slice(0, 300),
            source: src, date, auto: autoLabel(t)};
    ok.push(t);
  });
  saveLocal();
  const named = ok.filter(t => namesIn(L[t].label).length);
  const long = ok.filter(t => [...L[t].label].length > 10);
  msg.className = ng.length || named.length || long.length ? 'warn' : 'hint';
  msg.textContent = `${ok.length} 件を取り込んだ`
    + (named.length ? `。作家名・作品名の入ったラベルがある：${named.map(t => 'T' + String(t).padStart(2, '0')).join('，')}（一覧で直すとよい）` : '')
    + (long.length ? `。10字を超えるラベルがある：${long.map(t => 'T' + String(t).padStart(2, '0')).join('，')}（短く直すとよい）` : '')
    + '' + (ng.length ? `。読めなかった項目：${ng.join('，')}（番号の誤り・ラベルが空）` : '。')
    + ' ラベルは AI の仮説である。根拠の語と作品を，詳細と KWIC で確かめること。';
  render(); labelView();
  if (NET.view === 'topicnet' || NET.view === 'bipnet') buildNet();
}

// ---- 書き出し・読み込み ---------------------------------------------------
function exportLabels(){
  const models = {};
  Object.entries(LAB).forEach(([fp, tops]) => {
    if (!Object.keys(tops).length) return;
    const m = D.models.find(x => x.fp === fp);
    models[fp] = {label: m ? m.label : '', dir: m ? m.dir : '', topics: tops};
  });
  const out = {schema: 'jlit-topic-labels/1', exported: new Date().toISOString(), models};
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], {type: 'application/json'}));
  a.download = 'topic_labels.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function importFile(f){
  const rd = new FileReader();
  rd.onload = () => {
    try {
      const o = JSON.parse(rd.result);
      let n = 0;
      Object.entries(o.models || {}).forEach(([fp, m]) => {
        const cur = LAB[fp] || (LAB[fp] = {});
        Object.entries(m.topics || {}).forEach(([t, r]) => { cur[t] = normRec(r); n++; });
      });
      saveLocal(); render(); labelView();
      $('lmsg').className = 'hint'; $('lmsg').textContent = `ファイルから ${n} 件を読み込んだ（いまのモデルに当たるものだけが表示される）。`;
    } catch (e) { $('lmsg').className = 'warn'; $('lmsg').textContent = 'ラベルのファイルとして読めない: ' + e.message; }
  };
  rd.readAsText(f);
}

// ---- ラベルづけの画面 ------------------------------------------------------
function targets(){
  const L = labelsOf();
  const all = [...Array(M.K).keys()];
  return lst.scope === 'todo' ? all.filter(t => !L[t]) : all;
}
function labelView(){
  const tg = targets();
  const size = lst.batch === 0 ? Math.max(1, tg.length) : lst.batch;
  const pages = Math.max(1, Math.ceil(tg.length / size));
  lst.page = Math.min(lst.page, pages - 1);
  const cur = tg.slice(lst.page * size, lst.page * size + size);
  $('lpage').textContent = tg.length ? `${lst.page + 1} / ${pages} 回目（トピック ${cur.join('，')}）` : '対象のトピックが無い';
  $('lprev').disabled = lst.page <= 0; $('lnext').disabled = lst.page >= pages - 1;
  $('lprompt').value = cur.length ? promptFor(cur) : '';
  $('lcount').textContent = `${$('lprompt').value.length.toLocaleString()} 字`;
  $('lfp').textContent = `モデル「${M.label}」の指紋 ${M.fp}`;
  // 一覧（手で直せる）
  const L = labelsOf();
  $('ltab').innerHTML = `<tr><th>トピック</th><th>仮ラベル（機械的）</th><th>ラベル</th><th>種類</th><th>確信度</th><th>根拠・注意</th><th>出典</th></tr>` +
    [...Array(M.K).keys()].map(t => {
      const r = L[t];
      return `<tr data-t="${t}"><td><a href="#" data-go="${t}">T${String(t).padStart(2, '0')}</a></td>
        <td class="hint">${esc(autoLabel(t))}</td>
        <td><input type="text" class="lin" value="${esc(r ? r.label : '')}" placeholder="（未）">${r && namesIn(r.label).length ? `<div class="warn">⚠ ${esc(namesIn(r.label).join('・'))} が入っている</div>` : ''}${r && [...r.label].length > 10 ? `<div class="warn">⚠ 長い（${[...r.label].length}字）。10字以内に</div>` : ''}</td>
        <td><select class="lty">${['', ...LTYPES].map(x => `<option${r && r.type === x ? ' selected' : ''}>${x}</option>`).join('')}</select></td>
        <td><select class="lcf">${['', ...LCONF].map(x => `<option${r && r.confidence === x ? ' selected' : ''}>${x}</option>`).join('')}</select></td>
        <td class="hint">${r ? esc(r.evidence || '') + (r.caution ? '<br>⚠ ' + esc(r.caution) : '') : ''}</td>
        <td class="hint">${r ? esc(r.source || '') + '<br>' + esc(r.date || '') : ''}</td></tr>`;
    }).join('');
  $('ltab').querySelectorAll('tr[data-t]').forEach(tr => {
    const t = +tr.dataset.t;
    const upd = () => {
      const lab = tr.querySelector('.lin').value.trim();
      if (!lab) { delete labelsOf()[t]; }
      else {
        const old = labelsOf()[t] || {};
        const changed = old.label !== lab || old.type !== tr.querySelector('.lty').value || old.confidence !== tr.querySelector('.lcf').value;
        labelsOf()[t] = Object.assign({}, old, {label: lab, type: tr.querySelector('.lty').value || old.type || '混成',
          confidence: tr.querySelector('.lcf').value || old.confidence || '中', auto: autoLabel(t)},
          changed ? {source: (old.source && !/手で修正/.test(old.source) ? old.source + '→' : '') + '手で修正', date: today()} : {});
      }
      saveLocal(); render();
      $('lstat').textContent = `ラベルの付いたトピック ${Object.keys(labelsOf()).length} / ${M.K}`;
    };
    tr.querySelector('.lin').onchange = upd; tr.querySelector('.lty').onchange = upd; tr.querySelector('.lcf').onchange = upd;
  });
  $('ltab').querySelectorAll('[data-go]').forEach(a => a.onclick = ev => { ev.preventDefault(); goTopic(+a.dataset.go); });
  const nl = Object.keys(L).length;
  $('lstat').textContent = `ラベルの付いたトピック ${nl} / ${M.K}`;
}
async function copyPrompt(){
  const s = $('lprompt').value;
  try { await navigator.clipboard.writeText(s); $('lcopied').textContent = 'コピーした'; }
  catch (e) { $('lprompt').select(); try { document.execCommand('copy'); $('lcopied').textContent = 'コピーした'; } catch (e2) { $('lcopied').textContent = '選択したので ⌘C（Ctrl+C）でコピーすること'; } }
  setTimeout(() => { $('lcopied').textContent = ''; }, 2500);
}
function labelInit(){
  loadLabels();
  $('lscope').onchange = e => { lst.scope = e.target.value; lst.page = 0; labelView(); };
  $('lbatch').onchange = e => { lst.batch = +e.target.value; lst.page = 0; labelView(); };
  $('lprev').onclick = () => { lst.page--; labelView(); };
  $('lnext').onclick = () => { lst.page++; labelView(); };
  $('lcopy').onclick = copyPrompt;
  $('lsrc').onchange = e => { $('lsrc2').hidden = e.target.value !== 'その他'; };
  $('limport').onclick = importAnswer;
  $('lexport').onclick = exportLabels;
  $('lfile').onchange = e => { if (e.target.files[0]) importFile(e.target.files[0]); e.target.value = ''; };
  $('lclear').onclick = () => {
    if (!Object.keys(labelsOf()).length) return;
    LAB[M.fp] = {}; saveLocal(); render(); labelView();
    $('lmsg').className = 'hint'; $('lmsg').textContent = 'このモデルのラベルを消した（書き出したファイルは残っている）。';
  };
}

function labBox(t){
  const lb = labelOf(t);
  if (!lb) return `<div class="labbox auto">仮ラベル：${esc(autoLabel(t))}　<span class="hint">（機械的に付けたもの。「ラベルづけ」で生成 AI に診断させられる）</span></div>`;
  return `<div class="labbox"><b>${esc(lb.label)}</b><span class="ty">${esc(lb.type)}</span><span class="ty">確信度 ${esc(lb.confidence)}</span>
    <span class="hint">　${esc(lb.source || '')}・${esc(lb.date || '')}</span><br>
    <span class="hint">根拠：${esc(lb.evidence || '')}${lb.caution ? '　⚠ ' + esc(lb.caution) : ''}　／仮ラベル：${esc(autoLabel(t))}</span></div>`;
}

$('model').innerHTML = D.models.map((m, i) => `<option value="${i}">${esc(m.label)}</option>`).join('');
$('model').onchange = e => { initModel(+e.target.value); if (!$('tableview').hidden) tblView(); };
const logInput = el => Math.round(Math.pow(10, +el.value));
$('minc').oninput = e => { st.minc = +e.target.value === 0 ? 1 : logInput(e.target); render(); };
$('maxdr').oninput = e => { st.maxdr = +e.target.value; render(); };
$('maxws').oninput = e => { st.maxws = +e.target.value; render(); };
$('maxdp').oninput = e => { st.maxdp = +e.target.value; render(); };
$('maxas').oninput = e => { st.maxas = +e.target.value; render(); };
$('lam').oninput = e => { st.lam = +e.target.value; render(); };
$('nw').oninput = e => { st.nw = Math.max(5, Math.min(50, +e.target.value || 12)); render(); };
$('q').oninput = e => { st.q = e.target.value; render(); };
$('sort').onchange = e => { st.sort = e.target.value; render(); };
netInit();
labelInit();
tblInit();
initModel(0);
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', action='append', required=True, metavar='名前=ディレクトリ',
                    help='MALLET の出力ディレクトリ。複数与えると切り替えて比べられる')
    ap.add_argument('--meta', default=None, help='既定: *_v3_local.csv > v3 > v2')
    ap.add_argument('--lexicon', default=os.path.join(ROOT, 'data', 'tokens', 'lexicon.tsv'),
                    help='18_pos_select.py が書く語の品詞・頻度帯の表')
    ap.add_argument('--top', type=int, default=400,
                    help='各トピックについて保持する語の数（多いほど λ を下げたときに正確）')
    ap.add_argument('--min-count', type=int, default=3,
                    help='モデル内の度数がこれ未満の語はビューアに入れない')
    ap.add_argument('--d2v', default=None, metavar='DIR',
                    help='Step 7 の doc2vec の出力（work_vectors.csv のあるディレクトリ）。'
                         '作品のネットワークに使う')
    ap.add_argument('--labels', default=None, metavar='JSON',
                    help='ビューアで書き出したトピックのラベル（topic_labels.json）。'
                         '既定: --out と同じフォルダの topic_labels.json があれば読む')
    ap.add_argument('--rel-mfw', type=int, default=500,
                    help='トピック間の Delta・Cosine Delta に使う語の数（モデル内の度数の上位）')
    ap.add_argument('--out', default=os.path.join(ROOT, 'my_work', 'results', 'topic_viewer.html'))
    args = ap.parse_args()

    meta = load_meta(args.meta or default_meta())
    lex = load_lexicon(args.lexicon)
    if not lex:
        print(f'[warn] 語の品詞表が無い: {args.lexicon}\n'
              '       品詞と頻度帯の絞り込みが効かない。18_pos_select.py を先に実行すること。')
    models = []
    for spec in args.model:
        label, _, d = spec.partition('=')
        if not d:
            label, d = os.path.basename(os.path.normpath(spec)), spec
        if not os.path.isdir(d):
            sys.exit(f'MALLET の出力が無い: {d}')
        models.append(build_model(label, d, meta, lex, args.top, args.min_count, args.rel_mfw))
    unknown = sum(1 for m in models for v in m['vocab'] if v[1] == '不明')
    if lex and unknown:
        print(f'[warn] 品詞表に無い語が {unknown:,} ある（別の辞書や別の 05 の出力で学習した可能性がある）')

    d2v = load_d2v(args.d2v, meta) if args.d2v else None
    lab_path = args.labels or os.path.join(os.path.dirname(os.path.abspath(args.out)),
                                           'topic_labels.json')
    labels = load_labels(lab_path)
    import datetime
    gen = {'generated': datetime.datetime.now().isoformat(timespec='minutes'),
           'top': args.top, 'min_count': args.min_count, 'rel_mfw': args.rel_mfw}
    data = json.dumps({'models': models, 'd2v': d2v, 'labels': labels, 'gen': gen},
                      ensure_ascii=False, separators=(',', ':'))
    data = data.replace('</', '<\\/')
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        fh.write(HTML.replace('__DATA__', data))
    # 操作マニュアルをビューアと同じフォルダに置く（ビューアから相対リンクで開く）
    man_src = os.path.join(ROOT, 'scripts', 'topic_viewer_manual.html')
    man_dst = os.path.join(os.path.dirname(os.path.abspath(args.out)), 'topic_viewer_manual.html')
    if os.path.exists(man_src):
        shutil.copyfile(man_src, man_dst)
        print(f'[ok  ] 操作マニュアル → {man_dst}')
    else:
        print(f'[warn] 操作マニュアルが無い: {man_src}（ビューアの「操作マニュアル」は開かない）')
    print(f'[ok  ] {args.out}（{os.path.getsize(args.out) / 1e6:.1f} MB）')
    print('       ブラウザで開く。サーバは要らない。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
12_dict_compare.py
==================
解析辞書を替えて同じテクストを解析し，**内在的な**指標で比べる。

何を比べるのか
--------------
``unidic-cwj``（現代書き言葉）と ``unidic-kindai-bungo``（近代文語）は，
語彙も単位認定も違う別の辞書である。どちらが「良い」かは，**このコーパスの
どの層で**と限定しないと答えが出ない。本スクリプトは層別の材料を作る。

指標は4種類。

1. **未知語率** — 辞書に無い語の割合。低いほどよい……**とは限らない**
2. **平均語長・1字語率** — 未知語率とセットで見る。辞書は未知語を避けるために
   既知の短い単位へ細かく刻むことができる。刻めば未知語率は下がるが，
   「私は」を「私」「は」ではなく「わ」「た」「し」「は」と切っても
   未知語率は 0 になる。**未知語率だけを見て選ぶと，細かく刻む辞書が勝つ**
3. **境界一致率** — 2つの辞書が同じ位置で切っているか（Jaccard）。
   正解データが無くても，**どこで判断が分かれたか**は分かる
4. **品詞構成** — 名詞・動詞・助動詞の比率。文語辞書は助動詞（けり・べし）を
   1語として立てるので，口語辞書とは分布が変わる

そして**層別に**集計する。正書法（新字新仮名／新字旧仮名／旧字旧仮名）と
文体（A_文語体／B_過渡／C_口語体）は未知語率と強く相関するので，
コーパス全体の平均だけを見ても意味がない。

使い方
------
    python3 12_dict_compare.py \\
        --plain data/plain/full \\
        --dict cwj=/Users/Shared/jlit/unidic-cwj-202512 \\
        --dict kindai=/Users/Shared/jlit/unidic-kindai-bungo-v202512 \\
        --meta metadata/corpus_metadata_v3.csv \\
        --out results/dict_compare

``--dict`` は何個でも並べられる。3つ以上（近現代口語小説UniDic，
旧仮名口語UniDic など）を同時に比べてよい。

出力
----
``dict_summary.csv``        辞書 × 全体の指標
``dict_by_stratum.csv``     辞書 × 層（正書法・文体）の指標
``dict_by_work.csv``        辞書 × 作品の指標
``dict_agreement.csv``      辞書ペア × 作品の境界一致率
``dict_disagreements.csv``  切り方が分かれた箇所の実例（既定 400 件）
``dict_features.csv``       各辞書の素性の並び（互換性の記録）

**``dict_disagreements.csv`` は必ず目で見ること。** 数値だけでは，
どちらが日本語として正しく切っているかは決まらない。
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from collections import Counter

try:
    import fugashi
except ImportError:                                            # pragma: no cover
    sys.exit('fugashi が要る: pip install fugashi')

SKIP_POS = {'補助記号', '空白'}
FEATURE_NAMES = ('pos1', 'pos2', 'pos3', 'pos4', 'cType', 'cForm',
                 'lForm', 'lemma', 'orth', 'pron', 'orthBase', 'pronBase',
                 'goshu', 'iType', 'iForm', 'fType', 'fForm', 'kana')


def feat(f, name: str) -> str:
    v = getattr(f, name, None)
    return '' if v in (None, '*') else str(v)


def probe(tagger, name: str) -> dict:
    """辞書の素性の並びを調べる。

    **古文・近代文語の UniDic は現代語版と素性の数が違う。** 版によって
    17 / 26 / 29 と並びが変わり，``orthBase`` や ``goshu`` が無いことがある。
    無い素性を当てにしたまま走らせると，``lemma_key`` が黙って表層形に
    落ち，「語彙素で数えたつもりが表層形だった」という結果になる。
    先に確かめて記録に残す。
    """
    w = next(iter(tagger('國語の研究をしたり。')))
    have = {k: hasattr(w.feature, k) and feat(w.feature, k) != '' or hasattr(w.feature, k)
            for k in FEATURE_NAMES}
    n = len(getattr(w.feature, '_asdict', lambda: {})()) or None
    row = {'dict': name, 'n_features': n or '不明'}
    row.update({k: ('あり' if have.get(k) else '**なし**') for k in
                ('lemma', 'orthBase', 'lForm', 'pos1', 'goshu', 'cType')})
    return row


def analyse(tagger, text: str):
    """1作品を解析し，(境界の集合, 統計) を返す。

    境界は**文字位置**で持つ。辞書ごとにトークン列の長さが違うので，
    トークン番号では比べられない。
    """
    bounds, lens, pos1c, unk, n_tok = set(), [], Counter(), 0, 0
    pos = 0
    toks = []
    for line in text.split('\n'):
        if not line.strip():
            pos += len(line) + 1
            continue
        for w in tagger(line):
            s = w.surface
            p1 = feat(w.feature, 'pos1')
            start = text.find(s, pos)
            if start < 0:
                start = pos
            pos = start + len(s)
            bounds.add(pos)
            toks.append((start, s, p1))
            if p1 in SKIP_POS:
                continue
            n_tok += 1
            lens.append(len(s))
            pos1c[p1] += 1
            if not feat(w.feature, 'lemma'):
                unk += 1
        pos += 1
    stat = {
        'tokens': n_tok,
        'unknown': unk,
        'unknown_rate': unk / max(1, n_tok),
        'mean_len': sum(lens) / max(1, len(lens)),
        'len1_rate': sum(1 for x in lens if x == 1) / max(1, len(lens)),
        'noun_rate': pos1c['名詞'] / max(1, n_tok),
        'verb_rate': pos1c['動詞'] / max(1, n_tok),
        'aux_rate': pos1c['助動詞'] / max(1, n_tok),
    }
    return bounds, stat, toks


def load_meta(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding='utf-8-sig') as fh:
        for r in csv.DictReader(fh):
            pid = str(r.get('aozora_person_id') or '').strip()
            wid = str(r.get('aozora_work_id') or '').strip()
            lab = f"{r.get('author_ja', '?')}『{str(r.get('title_aozora'))[:12]}』"
            row = {'label': lab,
                   'orthography': r.get('kana_orthography', '') or '不明',
                   'style_class': r.get('style_class', '') or '不明',
                   'period': r.get('period', '') or '不明'}
            for k in (f'{pid.zfill(6)}_{wid.zfill(6)}', f'{pid}_{wid}'):
                if pid and wid:
                    out[k] = row
            fv = r.get('file_v1')
            if isinstance(fv, str) and fv.strip():
                out[os.path.splitext(fv)[0]] = row
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plain', required=True, help='04 が書いた data/plain/full')
    ap.add_argument('--dict', action='append', required=True, metavar='名前=パス',
                    help='比べる辞書。複数指定できる')
    ap.add_argument('--meta', default='metadata/corpus_metadata_v3.csv')
    ap.add_argument('--out', required=True)
    ap.add_argument('--samples', type=int, default=400,
                    help='食い違いの実例を何件書き出すか')
    ap.add_argument('--seed', type=int, default=20260920)
    args = ap.parse_args()

    if not os.path.isdir(args.plain):
        sys.exit(f'入力が無い: {args.plain}\n  04_normalise.py を先に走らせること。')
    dicts = []
    for spec in args.dict:
        if '=' not in spec:
            sys.exit(f'--dict は 名前=パス の形で書くこと: {spec}')
        name, path = spec.split('=', 1)
        if not os.path.isdir(path):
            sys.exit(f'辞書のディレクトリが無い: {path}')
        dicts.append((name, path))
    if len(dicts) < 2:
        sys.exit('--dict を2つ以上指定すること（比較なので）')
    os.makedirs(args.out, exist_ok=True)
    meta = load_meta(args.meta)

    # ---- 辞書を用意し，素性の並びを記録する -------------------------------
    taggers, feat_rows = {}, []
    for name, path in dicts:
        t0 = time.time()
        taggers[name] = fugashi.Tagger(f'-d {path}')
        feat_rows.append(probe(taggers[name], name) | {'path': path,
                                                       'load_sec': round(time.time() - t0, 2)})
        print(f'[dic ] {name:<10} {path}')
    with open(os.path.join(args.out, 'dict_features.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=list(feat_rows[0].keys()))
        w.writeheader(); w.writerows(feat_rows)
    for r in feat_rows:
        miss = [k for k in ('lemma', 'orthBase', 'lForm', 'goshu')
                if r.get(k, '').startswith('**')]
        if miss:
            print(f'[warn] {r["dict"]}: 素性 {"，".join(miss)} が無い。'
                  '--lemma-policy の既定（mixed）は lemma と orthBase を使うので，'
                  'この辞書では表層形に落ちる箇所が出る。')

    files = sorted(f for f in os.listdir(args.plain) if f.endswith('.txt'))
    if not files:
        sys.exit(f'テクストが無い: {args.plain}')
    print(f'[run ] {len(files)} 作品 × {len(dicts)} 辞書')

    rng = random.Random(args.seed)
    by_work, agree_rows, samples = [], [], []
    elapsed = Counter()
    for k, name_file in enumerate(files, 1):
        stem = name_file[:-4]
        text = open(os.path.join(args.plain, name_file), encoding='utf-8').read()
        m = meta.get(stem, {'label': stem, 'orthography': '不明',
                            'style_class': '不明', 'period': '不明'})
        res = {}
        for name, _ in dicts:
            t0 = time.time()
            res[name] = analyse(taggers[name], text)
            elapsed[name] += time.time() - t0
            by_work.append({'dict': name, 'work_stem': stem, 'label': m['label'],
                            'orthography': m['orthography'],
                            'style_class': m['style_class'], 'period': m['period'],
                            **{kk: (round(vv, 5) if isinstance(vv, float) else vv)
                               for kk, vv in res[name][1].items()}})
        # ---- 境界一致率とその実例 ----------------------------------------
        for i in range(len(dicts)):
            for j in range(i + 1, len(dicts)):
                a, b = dicts[i][0], dicts[j][0]
                A, B = res[a][0], res[b][0]
                jac = len(A & B) / max(1, len(A | B))
                agree_rows.append({'dict_a': a, 'dict_b': b, 'work_stem': stem,
                                   'label': m['label'],
                                   'orthography': m['orthography'],
                                   'style_class': m['style_class'],
                                   'boundary_jaccard': round(jac, 4),
                                   'tokens_a': res[a][1]['tokens'],
                                   'tokens_b': res[b][1]['tokens']})
                # 食い違った位置から実例を拾う
                diff = sorted(A ^ B)
                rng.shuffle(diff)
                for p in diff[:max(0, args.samples // max(1, len(files)) + 1)]:
                    lo, hi = max(0, p - 12), min(len(text), p + 12)
                    # **辞書名を列名にしない。** 辞書が3つ以上あると
                    # ペアの組合せごとに列の集合が変わり，1つの表に収まらない
                    # （4辞書なら6ペア）。どのペアかは値として持つ。
                    # Excel で「辞書ペアで絞る」のもこの形のほうが楽である。
                    samples.append({
                        'work_stem': stem, 'label': m['label'], 'char_pos': p,
                        'dict_a': a, 'dict_b': b,
                        'seg_a': ' '.join(s for st, s, _ in res[a][2]
                                          if lo <= st < hi),
                        'seg_b': ' '.join(s for st, s, _ in res[b][2]
                                          if lo <= st < hi),
                        'context': text[lo:hi].replace('\n', '／')})
        if k % 20 == 0 or k == len(files):
            print(f'       {k}/{len(files)}')

    def write(fn, rows):
        """行を CSV にする。**列名は全行の鍵の和集合から作る。**

        先頭行の鍵だけを列名にすると，行によって鍵の集合が違うときに
        ``dict contains fields not in fieldnames`` で落ちる。しかも
        **落ちるのは書き出しの段になってから**なので，何十分かけた解析を
        まるごと捨てることになる。和集合を取れば，形の違う行が混ざっても
        空欄のまま通る。
        """
        if not rows:
            return
        keys: list = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with open(os.path.join(args.out, fn), 'w', newline='',
                  encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction='ignore')
            w.writeheader(); w.writerows(rows)
        print(f'[ok  ] {fn}  {len(rows)} 行')

    write('dict_by_work.csv', by_work)
    write('dict_agreement.csv', agree_rows)

    # ---- 層別・全体の集計 -------------------------------------------------
    def agg(rows, keys):
        out = {}
        for r in rows:
            k = tuple(r[x] for x in keys)
            d = out.setdefault(k, {'works': 0, 'tokens': 0, 'unknown': 0,
                                   'len_sum': 0.0, 'len1_sum': 0.0,
                                   'aux_sum': 0.0})
            d['works'] += 1
            d['tokens'] += r['tokens']
            d['unknown'] += r['unknown']
            # 語数で重みづける。短い作品と長い作品を同じ重みで平均しない
            d['len_sum'] += r['mean_len'] * r['tokens']
            d['len1_sum'] += r['len1_rate'] * r['tokens']
            d['aux_sum'] += r['aux_rate'] * r['tokens']
        rows_out = []
        for k, d in sorted(out.items()):
            n = max(1, d['tokens'])
            rows_out.append(dict(zip(keys, k)) | {
                'works': d['works'], 'tokens': d['tokens'],
                'unknown_rate': round(d['unknown'] / n, 5),
                'mean_token_len': round(d['len_sum'] / n, 4),
                'len1_rate': round(d['len1_sum'] / n, 4),
                'aux_rate': round(d['aux_sum'] / n, 4)})
        return rows_out

    summary = agg(by_work, ['dict'])
    for r in summary:
        r['sec'] = round(elapsed[r['dict']], 1)
    write('dict_summary.csv', summary)

    # 層の種類が違っても**1つの表に収める**。正書法別と文体別を別の列名で
    # 並べると，列の集合が違う行が混ざって DictWriter が落ちる。
    # 「層の種類」「層の値」という2列に畳めば，どんな層別でも同じ形になる。
    strata = []
    for kind, col in (('正書法', 'orthography'), ('文体', 'style_class'),
                      ('時代', 'period')):
        for r in agg(by_work, ['dict', col]):
            strata.append({'dict': r['dict'], 'stratum_kind': kind,
                           'stratum': r[col],
                           **{k: v for k, v in r.items()
                              if k not in ('dict', col)}})
    write('dict_by_stratum.csv', strata)

    # 実例は**最後に書く**。集計表より壊れやすいので，ここで失敗しても
    # 解析の成果（上の4表）が失われないようにする。
    write('dict_disagreements.csv', samples[:args.samples])

    # ---- 画面にも出す -----------------------------------------------------
    print('\n全体（語数で重みづけ）')
    print(f'{"辞書":<12}{"語数":>12}{"未知語率":>10}{"平均語長":>10}'
          f'{"1字語率":>9}{"助動詞率":>9}{"秒":>8}')
    for r in summary:
        print(f'{r["dict"]:<12}{r["tokens"]:>12,}{r["unknown_rate"]:>10.2%}'
              f'{r["mean_token_len"]:>10.3f}{r["len1_rate"]:>9.1%}'
              f'{r["aux_rate"]:>9.1%}{r["sec"]:>8.1f}')
    if agree_rows:
        import statistics
        print('\n境界一致率（作品ごとの中央値）')
        pairs = sorted({(r['dict_a'], r['dict_b']) for r in agree_rows})
        for a, b in pairs:
            v = [r['boundary_jaccard'] for r in agree_rows
                 if r['dict_a'] == a and r['dict_b'] == b]
            print(f'  {a} ⇄ {b}: {statistics.median(v):.3f}'
                  f'（最小 {min(v):.3f} / 最大 {max(v):.3f}）')
    print('\n**未知語率だけで決めないこと。** 細かく刻む辞書は未知語率が下がる。')
    print('  平均語長・1字語率と必ず並べて見て，dict_disagreements.csv の')
    print('  実例を読んでから判断すること。')
    print(f'\n[ok  ] 出力 → {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

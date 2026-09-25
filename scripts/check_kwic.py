#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_kwic.py
=============
KWIC コンコーダンサ（``kwic_core.py``）の**意味論**を検査する。

なぜ必要か
------------
コンコーダンサの誤りは**もっとも見つけにくい種類の誤り**である。
用例は出るし，件数も出る。**間違っているのは「何を数えたか」だけ**で，
それは画面を見ても分からない。たとえば

  * 語彙素で検索したつもりが表層形に当たっていた
  * 「言う た」の連なりが，**文をまたいで**当たっていた
  * 左文脈の並べ替えが，隣の文の語で並んでいた
  * 出典の著者名が，0埋めの綴り違いで**別の作品のもの**になっていた

どれも「もっともらしい用例」を返すので，論文に載るまで気づかない。
そこで合成コーパスを作り，**答えが分かっている検索**で当たりを確かめる。

使い方
------
    python3 scripts/check_kwic.py
    python3 scripts/check_kwic.py --keep     # 作った合成索引を残す
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np                                      # noqa: E402
import kwic_core as K                                   # noqa: E402

OK, NG = '[ok  ]', '[NG  ]'

COLUMNS = ['i', 'surface', 'lemma_key', 'lemma', 'orthBase', 'lForm',
           'pos1', 'pos2', 'pos3', 'pos4', 'cType', 'cForm', 'goshu']

# 合成コーパス。(表層形, 語彙素, 品詞) の3つ組を文ごとに並べる。
# **答えが分かっている**ように作ってある。
W1 = [  # 000119_001743（0埋め済みの綴り。メタデータ側は 0 埋めなし）
    [('私', '私', '代名詞'), ('は', 'は', '助詞'), ('汽車', '汽車', '名詞'),
     ('に', 'に', '助詞'), ('乗つ', '乗る', '動詞'), ('た', 'た', '助動詞'),
     ('。', '。', '補助記号')],
    [('汽車', '汽車', '名詞'), ('は', 'は', '助詞'), ('白い', '白い', '形容詞'),
     ('煙', '煙', '名詞'), ('を', 'を', '助詞'), ('吐い', '吐く', '動詞'),
     ('た', 'た', '助動詞'), ('。', '。', '補助記号')],
    [('乗る', '乗る', '動詞'), ('。', '。', '補助記号')],
]
W2 = [  # 000035_001567（増補分。メタデータ側も 0 埋め）
    [('電車', '電車', '名詞'), ('に', 'に', '助詞'), ('乗り', '乗る', '動詞'),
     ('ます', 'ます', '助動詞'), ('。', '。', '補助記号')],
    [('汽車', '汽車', '名詞'), ('に', 'に', '助詞'), ('乗る', '乗る', '動詞'),
     ('。', '。', '補助記号')],
]
# 異綴形と品詞の中分類の検査用。(表層形, 語彙素, 品詞, 書字形基本形, 中分類)。
# 「言う」の書字形基本形は 云う×2・言う×1・いう×1（活用の違いは数えない）。
# 合成データなので，書字形基本形は実際の辞書の出力とは限らない
W4 = [  # 000081_000456（メタデータあり。作家は森鷗外）
    [('云つ', '言う', '動詞', '云う', '一般'), ('た', 'た', '助動詞'),
     ('。', '。', '補助記号')],
    [('云ふ', '言う', '動詞', '云う', '一般'), ('。', '。', '補助記号')],
    [('言つ', '言う', '動詞', '言う', '一般'), ('た', 'た', '助動詞'),
     ('。', '。', '補助記号')],
    [('いふ', '言う', '動詞', 'いう', '一般'), ('汽車', '汽車', '名詞'),
     ('が', 'が', '助詞'), ('来', '来る', '動詞'), ('て', 'て', '助詞'),
     ('いる', 'いる', '動詞', 'いる', '非自立可能'), ('。', '。', '補助記号')],
] + [  # 共起語は頻度 3 以上だけを出すので，いる・が・来る を 3 回以上にする
    [('汽車', '汽車', '名詞'), ('が', 'が', '助詞'), ('来', '来る', '動詞'),
     ('て', 'て', '助詞'), ('いる', 'いる', '動詞', 'いる', '非自立可能'),
     ('。', '。', '補助記号')]] * 2
W3 = [  # メタデータに無い作品（出典が出ないことの検査）
    [('汽車', '汽車', '名詞'), ('が', 'が', '助詞'), ('来', '来る', '動詞'),
     ('た', 'た', '助動詞'), ('。', '。', '補助記号')],
]


def write_tsv(path: Path, sents: list[list[tuple]]) -> None:
    with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
        fh.write('# dictionary=unidic-novel\tversion=v202512\t'
                 'lemma_policy=mixed\tdicdir=/x\tfugashi=1.5.2\n')
        w = csv.writer(fh, delimiter='\t', lineterminator='\n')
        w.writerow(COLUMNS)
        i = 0
        for s in sents:
            for tok in s:
                sur, lem, pos = tok[:3]
                # 書字形基本形の既定は語彙素（＝揺れなし），中分類の既定は品詞ごとに
                orth = tok[3] if len(tok) > 3 else lem
                pos2 = tok[4] if len(tok) > 4 else {
                    '名詞': '普通名詞', '動詞': '一般', '形容詞': '一般',
                    '補助記号': '句点'}.get(pos, '*')
                i += 1
                w.writerow([i, sur, lem, lem, orth, sur, pos,
                            pos2, '', '', '', '連用形' if pos == '動詞' else '',
                            '和'])
            w.writerow(['', '', '', '', '', '', 'EOS', '', '', '', '', '', ''])


def build(tmp: Path, with_w4: bool = False) -> K.KwicIndex:
    tsv = tmp / 'tsv'
    tsv.mkdir(parents=True, exist_ok=True)
    write_tsv(tsv / '000119_001743.tsv', W1)
    write_tsv(tsv / '000035_001567.tsv', W2)
    write_tsv(tsv / '000999_009999.tsv', W3)
    if with_w4:
        write_tsv(tsv / '000081_000456.tsv', W4)
    meta = tmp / 'meta.csv'
    with open(meta, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=[
            'aozora_person_id', 'aozora_work_id', 'file_v1', 'author_ja',
            'title_ja', 'year_first', 'period', 'genre_main', 'style_class',
            'completeness'])
        w.writeheader()
        # ⚠ v1 由来の行は作品 ID が**0埋めされていない**（実際のデータと同じ）
        w.writerow({'aozora_person_id': '119', 'aozora_work_id': '1743',
                    'file_v1': '', 'author_ja': '夏目漱石',
                    'title_ja': '三四郎', 'year_first': '1908',
                    'period': '2_明治後期', 'genre_main': 'Fiction',
                    'style_class': 'C_口語体', 'completeness': 'ok'})
        # 増補分は0埋めされている
        w.writerow({'aozora_person_id': '000035', 'aozora_work_id': '001567',
                    'file_v1': '', 'author_ja': '太宰治',
                    'title_ja': '走れメロス', 'year_first': '1940',
                    'period': '4_昭和戦前', 'genre_main': 'Fiction',
                    'style_class': 'C_口語体', 'completeness': 'ok'})
        w.writerow({'aozora_person_id': '000081', 'aozora_work_id': '000456',
                    'file_v1': '', 'author_ja': '森鷗外',
                    'title_ja': '雁', 'year_first': '1911',
                    'period': '2_明治後期', 'genre_main': 'Fiction',
                    'style_class': 'C_口語体', 'completeness': 'ok'})
    K.build_index(tsv, meta, tmp / 'kwic', quiet=True)
    return K.KwicIndex(tmp / 'kwic')


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix='jlit_kwic_'))
    ng = 0

    def chk(cond, label, extra=''):
        nonlocal ng
        if cond:
            print(f'{OK} {label}')
        else:
            ng += 1
            print(f'{NG} {label}' + (f'  → {extra}' if extra else ''))

    kw = build(tmp)
    def hits(q, **kw_):
        return kw.search(q, limit=100, **kw_)

    # ---- 1. 語彙素と表層形は**別の列である** ------------------------------
    a = hits('乗る', stream='lemma')
    b = hits('乗る', stream='surface')
    chk(a['total'] == 4, '1a) 語彙素「乗る」は4件（乗つ・乗り・乗る・乗る）',
        a['total'])
    chk(b['total'] == 2, '1b) 表層形「乗る」は2件（活用形は別の語）', b['total'])
    chk(hits('乗つ', stream='surface')['total'] == 1,
        '1c) 表層形「乗つ」は1件（語彙素では引けない綴り）')
    try:
        hits('乗つ', stream='lemma')
        chk(False, '1d) 語彙素に無い語形は**理由つきの例外**になる')
    except K.QueryError as e:
        chk('語彙' in str(e), '1d) 語彙素に無い語形は理由つきの例外になる', e)

    # ---- 2. 連なりは**文境界を越えない** ---------------------------------
    # 「た 汽車」は W1 の第1文末「た。」と第2文頭「汽車」で，文をまたぐ。
    # 補助記号「。」を数えるので隣接もしていない。**0件が正しい。**
    chk(hits('た 汽車', stream='lemma')['total'] == 0,
        '2a) 文をまたぐ連なりは当たらない')
    chk(hits('汽車 に 乗る', stream='lemma')['total'] == 2,
        '2b) 「汽車 に 乗る」は2件（両作品に1件ずつ）',
        hits('汽車 に 乗る', stream='lemma')['total'])
    seq = hits('汽車 に 乗る', stream='lemma')
    chk(all(len(r['key']) == 3 for r in seq['rows']),
        '2c) 連なりのキーワードは3語ぶん')

    # ---- 3. 品詞とワイルドカード -----------------------------------------
    chk(hits('/動詞', stream='lemma')['total'] == 6,
        '3a) 品詞だけの指定（動詞は6件）', hits('/動詞')['total'])
    chk(hits('乗る/動詞', stream='lemma')['total'] == 4,
        '3b) 語形と品詞の併用')
    chk(hits('乗る/名詞', stream='lemma')['total'] == 0,
        '3c) 品詞が合わなければ0件（例外ではない）')
    # 合成コーパスの「汽車」は4件（W1に2・W2に1・W3に1），「電車」は1件
    chk(hits('*車', stream='lemma')['total'] == 5,
        '3d) 後方一致 *車 は汽車4＋電車1', hits('*車')['total'])
    chk(hits('汽車|電車', stream='lemma')['total'] == 5,
        '3e) 選択肢 |', hits('汽車|電車')['total'])
    chk(hits('re:^汽.$', stream='lemma')['total'] == 4,
        '3f) 正規表現', hits('re:^汽.$')['total'])
    chk(hits('汽車 *', stream='lemma')['total'] == 4,
        '3g) 任意の1語 *（汽車の直後に語がある4件）',
        hits('汽車 *')['total'])
    # 項ごとの列指定（語彙素と表層形を混ぜる）
    chk(hits('S:乗つ', stream='lemma')['total'] == 1,
        '3h) S: でその項だけ表層形に当てる')
    chk(hits('L:乗る', stream='surface')['total'] == 4,
        '3i) L: でその項だけ語彙素に当てる')

    # ---- 4. 出典（0埋めの綴り違いに左右されないこと）------------------------------
    r = hits('汽車', stream='lemma')
    src = {(x['author'], x['title']) for x in r['rows']}
    chk(('夏目漱石', '三四郎') in src,
        '4a) **0埋めされていないメタデータの行**にも突合できる', src)
    chk(('太宰治', '走れメロス') in src, '4b) 0埋め済みの行にも突合できる')
    chk(any(not x['has_meta'] for x in r['rows']),
        '4c) メタデータに無い作品は has_meta=False で出る')
    chk(any(x['author'] == '（メタデータ無し）' for x in r['rows']),
        '4d) 出典が無いことを**表示に出す**（空欄にしない）')
    chk(len(kw.prov['unmatched_meta']) == 1,
        '4e) 突合できない作品を由来に記録する')

    # ---- 5. 時代区分 -----------------------------------------------------
    bands = {x['label']: x['hits'] for x in r['by_band']}
    chk(bands.get('1900–1911 明治後期') == 2,
        '5a) 明治後期に2件（三四郎）', bands)
    chk(bands.get('1926–1944 昭和戦前') == 1, '5b) 昭和戦前に1件（走れメロス）')
    chk(bands.get('初出年不明') == 1, '5c) 初出年不明にも行が立つ（黙って消さない）')
    chk(all(x['tokens'] >= 0 for x in r['by_band']),
        '5d) 時代区分ごとの形態素数が出る（1万語あたりの分母）')

    # ---- 6. 並べ替え -----------------------------------------------------
    left = hits('汽車', stream='lemma', sort='left1')
    firsts = [(''.join(t['surf'] for t in x['left'][-1:]) or '') for x in left['rows']]
    chk(firsts == sorted(firsts), '6a) 左1語で並ぶ', firsts)
    right = hits('汽車', stream='lemma', sort='right1')
    r1 = [(x['right'][0]['surf'] if x['right'] else '') for x in right['rows']]
    chk(r1 == sorted(r1), '6b) 右1語で並ぶ', r1)
    yr = hits('汽車', stream='lemma', sort='year')
    chk([x['year'] for x in yr['rows']][:2] == ['1908', '1908'],
        '6c) 初出年で並ぶ（不明は最後）', [x['year'] for x in yr['rows']])

    # ---- 7. 絞り込み -----------------------------------------------------
    chk(hits('汽車', stream='lemma', authors=['夏目漱石'])['total'] == 2,
        '7a) 著者で絞る')
    chk(hits('汽車', stream='lemma', bands=[1])['total'] == 2,
        '7b) 時代区分で絞る')
    chk(hits('。', stream='lemma', exclude_punct=True)['total'] == 0,
        '7c) 句読点を外す')
    chk(hits('。', stream='lemma')['total'] == 6,
        '7d) 既定では句読点も索引にある（読み返すのに必要）')

    # ---- 8. 間引きは**間引いたと言う** -----------------------------------
    s = hits('。', stream='lemma', sample=2)
    chk(s['total'] == 6 and s['sampled'] and s['shown'] == 2,
        '8a) 総数は総数のまま，間引いたことを返す',
        (s['total'], s['sampled'], s['shown']))
    s2 = kw.search('。', stream='lemma', sample=2, seed=7)
    s3 = kw.search('。', stream='lemma', sample=2, seed=7)
    chk([x['pos_i'] for x in s2['rows']] == [x['pos_i'] for x in s3['rows']],
        '8b) 同じシードなら同じ標本（再現する）')

    # ---- 9. テクストに戻る -----------------------------------------------
    p = kw.passage(r['rows'][0]['pos_i'], before=50, after=50)
    chk(any(t['key'] for t in p['tokens']), '9a) 広い文脈でキーワードが分かる')
    chk(p['work']['author'] in ('夏目漱石', '太宰治', ''),
        '9b) 広い文脈にも出典が付く')
    chk(all('lem' in t and 'pos' in t for t in p['tokens']),
        '9c) 各語に語彙素と品詞が付く（原文の表記は surf）')
    # 作品の境界を越えない
    w0 = kw.works[0]
    pe = kw.passage(w0['end'] - 1, before=5, after=500)
    chk(pe['to'] <= w0['end'], '9d) 広い文脈が**次の作品に漏れない**',
        (pe['to'], w0['end']))

    # ---- 10. 共起語 ------------------------------------------------------
    c = hits('汽車', stream='lemma', collocates=10, coll_window=3)
    forms = [x['form'] for x in c['collocates']]
    chk('に' in forms, '10a) 共起語が出る', forms)
    chk(all(x['co'] >= 2 for x in c['collocates']),
        '10b) 共起2回未満は出さない（偶然を並べない）')

    # ---- 11. 表と CSV ----------------------------------------------------
    df = kw.to_frame(r)
    chk(list(df.columns) == ['時代', '著者', '作品', '左文脈', 'キーワード',
                             '右文脈', '位置'],
        '11a) ノートブック用の表の列', list(df.columns))
    csvp = K.to_csv(kw, r, tmp / 'out.csv')
    head = open(csvp, encoding='utf-8-sig').readline()
    chk(head.startswith('# query'), '11b) CSV の1行目に検索式と由来を書く')

    # ---- 12. 異綴形（書字形基本形）---------------------------------------
    kv = build(tmp / 'v2', with_w4=True)
    chk(kv.v2, '12a) 版 2 の索引は書字形基本形と品詞の中分類を持つ')
    v = kv.search('言う', stream='lemma', limit=10)['variants']
    got = {f['form']: f['hits'] for f in v['forms']}
    chk(got == {'云う': 2, '言う': 1, 'いう': 1},
        '12b) 「言う」の異綴形は 云う2・言う1・いう1（云つ・云ふ は同じ書字形基本形）', got)
    chk(v['forms'][0]['form'] == '云う', '12c) 多い順に並ぶ')
    vj = kv.search('乗る', stream='lemma', limit=10)['variants']
    chk([f['form'] for f in vj['forms']] == ['乗る'],
        '12d) **活用の違いは異綴形に数えない**（乗つ・乗り・乗る → 乗る）',
        vj['forms'])
    for key in ('by_band', 'by_author', 'by_work'):
        chk(sum(r['total'] for r in v[key]) == v['total']
            and all(sum(r['counts']) == r['total'] for r in v[key]),
            f'12e) 内訳（{key}）の合計が総数と一致する')
    au = {r['label']: r['counts'] for r in v['by_author']}
    chk(au.get('森鷗外') == [2, 1, 1], '12f) 作家ごとの内訳', au)
    pos_iu = np.flatnonzero(np.asarray(kv.a['lem']) == kv._rev['lem']['言う'])
    few = kv._variants(pos_iu, 1, top=1)
    chk(few['other'] == {'types': 2, 'hits': 2, 'share': 0.5}
        and all(len(r['counts']) == 2 for r in few['by_author']),
        '12g) 上位より後は「その他」にまとめ，まとめたことを返す', few['other'])

    tab = kv.variant_table(min_freq=1, min_var=1)
    row = {r['lemma']: r for r in tab['rows']}.get('言う')
    chk(row is not None and row['types'] == 3 and row['main'] == '云う'
        and abs(row['minor_share'] - 0.5) < 1e-9,
        '12h) コーパス全体の一覧に「言う」（3種・主要形 云う・主要形以外 50%）', row)
    chk([r['lemma'] for r in tab['rows']] == ['言う'],
        '12i) 揺れの無い語彙素は一覧に出ない', [r['lemma'] for r in tab['rows']])
    tab2 = kv.variant_table(min_freq=1, min_var=2)
    chk(not tab2['rows'],
        '12j) 最小回数 2 なら 言う・いう は異綴形に数えず，一覧から外れる')
    chk(not kv.variant_table(min_freq=1, min_var=1, pos=['名詞'])['rows'],
        '12k) 品詞で絞れる（名詞には揺れが無い）')

    # ---- 13. 共起語の品詞による絞り込み ---------------------------------
    def cforms(**k):
        return [x['form'] for x in kv.search('汽車', stream='lemma', collocates=50,
                                             coll_window=4, coll_min=1,
                                             **k)['collocates']]
    allf = cforms()
    chk('いる' in allf and 'が' in allf, '13a) 絞らなければ助詞も非自立の動詞も出る', allf)
    vf = cforms(coll_pos=['動詞'])
    chk(set(vf) <= {'乗る', '来る', '吐く', 'いる', '言う'} and 'いる' in vf,
        '13b) 大分類「動詞」は非自立の動詞も含む', vf)
    vg = cforms(coll_pos=['動詞-一般'])
    chk('いる' not in vg and '来る' in vg,
        '13c) 中分類「動詞-一般」で非自立（いる）を外せる', vg)
    chk(not set(cforms(coll_pos=['名詞'])) & {'が', 'に', 'は', '乗る', 'いる'},
        '13d) 名詞だけにすると助詞・動詞が出ない')
    try:
        kv.search('汽車', collocates=5, coll_pos=['名詞-存在しない'])
        chk(False, '13e) 索引に無い品詞は理由つきの例外になる')
    except K.QueryError as e:
        chk('品詞' in str(e), '13e) 索引に無い品詞は理由つきの例外になる', e)

    # ---- 14. 指標は**素朴な数え方と一致する** ----------------------------
    import math
    res = kv.search('汽車', stream='lemma', collocates=100, coll_window=3,
                    coll_min=1, coll_sort='co')
    info = res['coll_info']
    lem = [kv.v['lem'][int(x)] for x in kv.a['lem']]
    posl = [kv.v['pos'][int(x)] for x in kv.a['pos']]
    sent = [int(x) for x in kv.a['sent']]
    content = [p not in K.PUNCT_POS for p in posl]
    N = sum(content)
    node = [i for i, w in enumerate(lem) if w == '汽車']
    from collections import Counter as _C
    co, W = _C(), 0
    for i in node:
        for o in (-3, -2, -1, 1, 2, 3):
            j = i + o
            if 0 <= j < len(lem) and sent[j] == sent[i] and content[j]:
                W += 1
                co[lem[j]] += 1
    fc = _C(w for w, c in zip(lem, content) if c)
    chk(info['W'] == W and info['N'] == N and info['node'] == len(node),
        '14a) 窓の延べ W・母数 N・検索語の件数が素朴な数え方と一致',
        (info['W'], W, info['N'], N))
    bad = []
    for r in res['collocates']:
        o11, f = co[r['form']], fc[r['form']]
        e11 = W * f / N
        o12, o21 = W - o11, f - o11
        o22 = N - W - o21
        cells = [(o11, e11), (o12, W * (N - f) / N), (o21, (N - W) * f / N),
                 (o22, (N - W) * (N - f) / N)]
        g2 = 2 * sum(o * math.log(o / e) for o, e in cells if o > 0)
        g2 = -g2 if o11 < e11 else g2
        want = {'co': o11, 'freq': f,
                'logdice': 14 + math.log2(2 * o11 / (len(node) + f)),
                'mi': math.log2(o11 / e11), 'mi3': math.log2(o11 ** 3 / e11),
                't': (o11 - e11) / math.sqrt(o11), 'g2': g2,
                'dp_fwd': o11 / W - o21 / (N - W),
                'dp_bwd': o11 / f - o12 / (N - f)}
        for k, x in want.items():
            tol = 1e-4 if k.startswith('dp') else 0.01
            if abs(r[k] - x) > tol:
                bad.append((r['form'], k, r[k], round(x, 4)))
    chk(not bad and res['collocates'],
        '14b) LogDice・MI・MI3・t・G²・ΔP（両向き）が定義どおり', bad[:5])
    # 品詞で絞ると f(c) も同じ品詞で数える（N は変えない）
    rn = kv.search('汽車', stream='lemma', collocates=100, coll_window=3,
                   coll_min=1, coll_pos=['名詞'])
    chk(rn['coll_info']['N'] == N and rn['coll_info']['W'] == W,
        '14c) 品詞で絞っても W と N は変わらない（窓の大きさは同じ）')

    # ---- 15. 指標を替えると**選び直す** --------------------------------
    for m in K.COLL_MEASURES:
        rr = kv.search('汽車', stream='lemma', collocates=3, coll_window=3,
                       coll_min=1, coll_sort=m)['collocates']
        full = kv.search('汽車', stream='lemma', collocates=100, coll_window=3,
                         coll_min=1, coll_sort=m)['collocates']
        vals = [x[m] for x in full]
        chk(vals == sorted(vals, reverse=True)
            and [x['form'] for x in rr] == [x['form'] for x in full[:3]],
            f'15) {m} の順に上位を選ぶ（上位3語は全体の上位3語と同じ）')
    try:
        kv.search('汽車', collocates=5, coll_sort='xx')
        chk(False, '15z) 知らない指標は例外')
    except K.QueryError:
        chk(True, '15z) 知らない指標は例外')

    # ---- 16. 版 1 の索引も読める（新しい機能は理由を言って止まる）---------
    old = tmp / 'v2' / 'kwic' / 'arrays'
    for n_ in ('orth.npy', 'pos12.npy'):
        (old / n_).rename(old / (n_ + '.bak'))
    k1 = K.KwicIndex(tmp / 'v2' / 'kwic')
    r1 = k1.search('言う', stream='lemma', collocates=5, coll_pos=['動詞'])
    chk(not k1.v2 and r1['variants']['available'] is False
        and '作り直す' in r1['variants']['reason'],
        '16a) 版 1 の索引では異綴形は「作り直すこと」と返す')
    try:
        k1.search('汽車', collocates=5, coll_pos=['動詞-一般'])
        chk(False, '16b) 版 1 の索引で中分類を指定すると理由つきの例外')
    except K.QueryError as e:
        chk('作り直す' in str(e), '16b) 版 1 の索引で中分類を指定すると理由つきの例外', e)

    print()
    if ng:
        print(f'{NG} KWIC に {ng} 件の食い違いがある。**用例は出るのに'
              '数えているものが違う**種類の誤りなので，直してから使うこと。')
    else:
        print(f'{OK} KWIC の意味論はすべて期待どおり'
              f'（合成コーパス {kw.n} 形態素・{len(kw.works)} 作品で検査）。')
    if args.keep:
        print(f'       合成索引: {tmp}')
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if ng else 0


if __name__ == '__main__':
    raise SystemExit(main())

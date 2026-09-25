#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kwic_core.py
============
KWIC コンコーダンサの中身（索引づくりと検索）。**画面もサーバもここには無い。**

  * 索引を作る   … ``scripts/15_kwic_index.py``（この module を呼ぶ）
  * 画面を出す   … ``scripts/16_kwic_server.py``（同じく）
  * ノートブック … ``from kwic_core import KwicIndex`` で表として使える

なぜ必要か
------------
数えたあとに**テクストに戻る**ための道具である。word embedding や特徴語で
「この語が効いている」と分かっても，**その語が本文でどう振る舞っているか**を
読まなければ何も言えない。頻度表は問いを作る道具で，答えは本文にある。

設計の約束
----------
1. **入力は ``data/tokens/tsv``**（05 の出力）。表層形・語彙素・品詞・活用形・
   語種・文境界がすべて1行1形態素で揃っている唯一の場所である。
   ``tokens_lemma`` や ``tokens_surface``（空白区切りの列）からは品詞が
   復元できないので使わない。**同じ解析結果から両方の列を引く**ので，
   語彙素で検索して表層形を表示することもできる。
2. **辞書名と版を索引に記録する。** どの辞書で切った本文を読んでいるのかが
   分からない用例は，証拠にならない。
3. **メタデータの突合は0埋めの有無に左右されないキーで行い，外れた作品は必ず報告する。**
   0埋めの綴りが揃わないと，作品が例外も出さずに黙って除外されるからである
   （``stem_keys()`` の項）。突合できない作品は「メタデータ無し」と**表示に出す**。
4. **句読点も索引に入れる。** 読み返すのに必要である（``keep_punct`` は
   トークン列の設定であって，TSV には最初から入っている）。検索では
   ``--no-punct`` 相当の絞り込みで外せる。

検索の書き方（画面のヘルプと同じ）
----------------------------------
==================  ======================================================
``汽車``            そのままの一致（選んだ列＝語彙素または表層形）
``汽車|電車``       どちらか
``乗*`` ``*車``     前方・後方一致（``*``は0文字以上）
``re:^汽.$``        正規表現（語彙に対して当てる）
``*``               任意の1語
``/動詞``           品詞だけで指定（大分類の前方一致）
``言う/動詞``       語形と品詞の両方
``L:言う``          この項だけ語彙素で当てる
``S:言つた``        この項だけ表層形で当てる
``言う た``         **語の連なり**（空白区切り。文境界は越えない）
==================  ======================================================
"""
from __future__ import annotations

import csv
import datetime as _dt
import fnmatch
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

INDEX_VERSION = 2
# 版 2 で加えたもの：書字形基本形（orthBase。異綴形の集計に使う）と
# 品詞の中分類（pos1-pos2。共起語の品詞による絞り込みに使う）。
# 版 1 の索引も読めるが，この2つを使う機能は「索引を作り直すこと」と言って止まる。
EOS = 'EOS'
PUNCT_POS = {'補助記号', '空白'}

# 画面（kwic_app.html）とサーバの取り決めの版。画面の側にも同じ番号を書く。
# 返す項目を変えたら上げる。食い違えば画面は「サーバが古い」と言って止まる
API_VERSION = 2


def code_signature(index_dir: str | os.PathLike) -> str:
    """このサーバが使っている**プログラムと索引の指紋**。

    サーバはカーネルを再起動しても止まらないので，教材を更新したり索引を
    作り直したりしたあとも，古いプログラム・古い索引のまま動き続けうる。
    ノートブックはこの指紋を見て，食い違えばサーバを起動し直す。
    """
    import hashlib
    here = Path(__file__).resolve().parent
    h = hashlib.sha1()
    for name in ('kwic_core.py', '16_kwic_server.py', 'kwic_app.html',
                 'kwic_manual.html'):
        p = here / name
        h.update(name.encode())
        if p.exists():
            h.update(p.read_bytes())
    j = Path(index_dir) / 'kwic_index.json'
    if j.exists():
        st = j.stat()
        h.update(f'{st.st_size}:{int(st.st_mtime)}'.encode())
    return h.hexdigest()[:16]


# 共起語の指標（名前 → 画面の表示名）。並べ替えの基準に選べる
COLL_MEASURES = {
    'logdice': 'LogDice',          # Rychlý (2008)。14 + log2(2·O11/(f(node)+f(c)))
    'mi': 'MI',                    # log2(O11/E11)。低頻度語を過大に評価する
    'mi3': 'MI3',                  # log2(O11³/E11)。MI の低頻度語への偏りを補正
    't': 't',                      # (O11−E11)/√O11。高頻度の語が上に来る
    'g2': 'G²',                    # 対数尤度比。反発（O11<E11）は負にする
    'dp_fwd': 'ΔP（検索語→共起語）',  # Gries (2013)。O11/W − O21/(N−W)
    'dp_bwd': 'ΔP（共起語→検索語）',  # O11/f(c) − O12/(N−f(c))
    'co': '共起頻度',
}

# 時代区分の切れ目（YEAR_EDGES と同じ。図と表で同じ区分を使う）
YEAR_EDGES = [1900, 1912, 1926, 1945]
BAND_LABELS = ['〜1899 明治中期', '1900–1911 明治後期', '1912–1925 大正',
               '1926–1944 昭和戦前', '1945– 昭和戦後']


def year_band(year) -> int:
    """初出年を5区分にまとめる。不明は -1。"""
    try:
        y = int(float(year))
    except (TypeError, ValueError):
        return -1
    for i, e in enumerate(YEAR_EDGES):
        if y < e:
            return i
    return len(YEAR_EDGES)


# ---------------------------------------------------------------------------
# メタデータの突合（0埋めの有無に左右されないキー）
# ---------------------------------------------------------------------------
def stem_keys(r: dict) -> list[str]:
    """メタデータの1行から，トークンファイルの語幹になりうるキーをすべて作る。

    ⚠ ``corpus_metadata_v3.csv`` の作品 ID は，v1 由来の行では0埋めされて
    おらず（``1743``），増補した行では0埋めされている（``001504``）。
    素朴に ``f'{pid}_{wid}'`` と書くと**1件も引けない**のに例外も出ない。
    だから綴りを全部作って試す。
    """
    keys = []
    fv = (r.get('file_v1') or '').strip()
    if fv:
        keys.append(os.path.splitext(fv)[0])
    pid = (r.get('aozora_person_id') or '').strip()
    wid = (r.get('aozora_work_id') or '').strip()
    if pid and wid and pid.lower() != 'nan' and wid.lower() != 'nan':
        keys += [f'{pid.zfill(6)}_{wid.zfill(6)}', f'{pid}_{wid}',
                 f'{pid.zfill(6)}_{wid}', f'{pid}_{wid.zfill(6)}']
    return keys


def load_meta_index(meta_path: str | os.PathLike) -> dict[str, dict]:
    """メタデータを「語幹 → 行」の索引にする。"""
    idx: dict[str, dict] = {}
    with open(meta_path, encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            drop = str(row.get('completeness', '')).strip()
            for k in stem_keys(row):
                idx.setdefault(k, dict(row, _drop=drop))
    return idx


# ---------------------------------------------------------------------------
# 索引づくり
# ---------------------------------------------------------------------------
def build_index(tsv_dir: str | os.PathLike, meta_path: str | os.PathLike,
                out_dir: str | os.PathLike, *, quiet: bool = False) -> dict:
    """``data/tokens/tsv`` から索引を作って ``out_dir`` に書く。

    返り値は由来（provenance）の辞書。**必ず作品数と突合の結果を報告する。**
    """
    tsv_dir, out_dir = Path(tsv_dir), Path(out_dir)
    files = sorted(tsv_dir.glob('*.tsv'))
    if not files:
        raise SystemExit(
            f'TSV が無い: {tsv_dir}\n'
            '  05_tokenise_unidic.py を先に実行すること'
            '（--out data/tokens で tsv/ ができる）。')
    meta = load_meta_index(meta_path)

    say = (lambda *a: None) if quiet else print

    # 語彙は出現順に番号を振る（頻度順に並べ替える必要は無い）
    s_ids: dict[str, int] = {}
    l_ids: dict[str, int] = {}
    p_ids: dict[str, int] = {}
    c_ids: dict[str, int] = {}
    g_ids: dict[str, int] = {}
    o_ids: dict[str, int] = {}
    q_ids: dict[str, int] = {}

    def sid(d: dict, k: str) -> int:
        v = d.get(k)
        if v is None:
            v = d[k] = len(d)
        return v

    surf: list[int] = []
    lem: list[int] = []
    pos: list[int] = []
    cfm: list[int] = []
    gos: list[int] = []
    wrk: list[int] = []
    snt: list[int] = []
    orth: list[int] = []
    p12: list[int] = []

    works: list[dict] = []
    dict_names: Counter = Counter()
    dict_vers: Counter = Counter()
    unmatched: list[str] = []
    sent_no = 0

    for wi, f in enumerate(files):
        stem = f.stem
        start = len(surf)
        with open(f, encoding='utf-8-sig') as fh:
            head = fh.readline()
            if head.startswith('#'):
                # ``# dictionary=… version=… lemma_policy=… dicdir=… fugashi=…``
                for part in head.lstrip('#').strip().split('\t'):
                    if '=' in part:
                        k, v = part.split('=', 1)
                        if k.strip() == 'dictionary':
                            dict_names[v.strip()] += 1
                        elif k.strip() == 'version':
                            dict_vers[v.strip()] += 1
                fh.readline()                       # 列名の行
            else:
                pass                                # 1行目が列名だった（古い形）
            for line in fh:
                c = line.rstrip('\n').split('\t')
                if len(c) < 13:
                    continue
                if c[6] == EOS:
                    sent_no += 1                    # 文の切れ目
                    continue
                surf.append(sid(s_ids, c[1]))
                lem.append(sid(l_ids, c[2] or c[1]))
                pos.append(sid(p_ids, c[6]))
                cfm.append(sid(c_ids, c[11]))
                # 書字形基本形。活用は基本形にまとめ，表記の違いは残す
                # （言う／云う／いう）。未知語などで空なら表層形を使う
                orth.append(sid(o_ids, c[4] or c[1]))
                # 品詞の中分類。UniDic は無い段を '*' や空で埋める
                p2 = c[7] if c[7] not in ('', '*') else ''
                p12.append(sid(q_ids, f'{c[6]}-{p2}' if p2 else c[6]))
                gos.append(sid(g_ids, c[12]))
                wrk.append(wi)
                snt.append(sent_no)
        sent_no += 1                                # 作品の切れ目でも文を切る

        m = meta.get(stem)
        if m is None:
            unmatched.append(stem)
        works.append({
            'i': wi, 'stem': stem, 'start': start, 'end': len(surf),
            'tokens': len(surf) - start,
            'author': (m or {}).get('author_ja', '') or '',
            'title': (m or {}).get('title_ja', '')
                     or (m or {}).get('title_aozora', '') or '',
            'year': (m or {}).get('year_first', '') or '',
            'period': (m or {}).get('period', '') or '',
            'genre': (m or {}).get('genre_main', '') or '',
            'style': (m or {}).get('style_class', '') or '',
            'band': year_band((m or {}).get('year_first', '')),
            'meta': m is not None,
            'drop': (m or {}).get('_drop', ''),
        })
        say(f'  [idx ] {stem:<22} {len(surf) - start:>9,} 形態素'
            + ('' if m is not None else '  ⚠ メタデータ無し'))

    n = len(surf)
    if not n:
        raise SystemExit('索引に入る形態素が1つも無い。TSV の中身を確かめること。')

    # ---- 突合の結果を**必ず**報告する ------------------------------------
    if unmatched:
        say(f'\n[warn] メタデータに突合できない作品 {len(unmatched)} 点:')
        for s in unmatched[:12]:
            say(f'         {s}')
        if len(unmatched) > 12:
            say(f'         …ほか {len(unmatched) - 12} 点')
        say('       画面では「メタデータ無し」と表示される。'
            '出典が出ない用例は証拠にならないので，metadata を直すこと。')
    if len(unmatched) > len(files) * 0.1:
        say(f'[FATAL] 突合できない作品が {len(unmatched)}/{len(files)} 点'
            '（1割超）ある。**キーの作り方を疑うこと**'
            '（scripts/check_stem_keys.py）。')

    if len(dict_names) > 1:
        say(f'[warn] **辞書が混ざっている**: {dict(dict_names)}'
            '  05 を辞書ごとに --out を分けて実行し直すこと。')

    out_dir.mkdir(parents=True, exist_ok=True)
    # ---- 配列は**圧縮しない .npy で1本ずつ**書く -------------------------
    # 圧縮した .npz は読むたびに全部を展開する。1000万形態素だと
    # **サーバの起動に数十秒**かかり，その間ブラウザからは
    # 「サーバに接続できません」に見える。
    # 非圧縮の .npy なら mmap で開けるので，起動は瞬時で済む。
    # 置き場が大きくなるが data/ は git の管理外である。
    arr_dir = out_dir / 'arrays'
    arr_dir.mkdir(parents=True, exist_ok=True)
    for name, dtype, seq in (('surf', np.int32, surf), ('lem', np.int32, lem),
                             ('pos', np.int16, pos), ('cfm', np.int16, cfm),
                             ('gos', np.int8, gos), ('work', np.int16, wrk),
                             ('sent', np.int32, snt), ('orth', np.int32, orth),
                             ('pos12', np.int16, p12)):
        np.save(arr_dir / f'{name}.npy', np.asarray(seq, dtype=dtype))

    def vocab_list(d: dict) -> list[str]:
        out = [''] * len(d)
        for k, v in d.items():
            out[v] = k
        return out

    prov = {
        'index_version': INDEX_VERSION,
        'built_at': _dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M'),
        'tsv_dir': str(Path(tsv_dir).resolve()),
        'meta': str(Path(meta_path).resolve()),
        'dictionary': dict_names.most_common(1)[0][0] if dict_names else '不明',
        'dictionary_version': dict_vers.most_common(1)[0][0] if dict_vers else '不明',
        'dictionaries_seen': dict(dict_names),
        'works': len(works), 'tokens': n,
        'types_surface': len(s_ids), 'types_lemma': len(l_ids),
        'types_orth': len(o_ids),
        'sentences': sent_no,
        'unmatched_meta': unmatched,
    }
    payload = {
        'provenance': prov,
        'works': works,
        'vocab': {
            'surf': vocab_list(s_ids), 'lem': vocab_list(l_ids),
            'pos': vocab_list(p_ids), 'cfm': vocab_list(c_ids),
            'gos': vocab_list(g_ids),
            'orth': vocab_list(o_ids), 'pos12': vocab_list(q_ids),
        },
        'band_labels': BAND_LABELS,
    }
    with open(out_dir / 'kwic_index.json', 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False)
    stale = out_dir / 'kwic_tokens.npz'
    if stale.exists():
        # 古い形（圧縮）。読み込みには使われないが場所を取る
        say(f'[info ] 古い {stale.name}（{stale.stat().st_size / 1e6:.0f} MB）が'
            '残っている。もう使わないので消してよい。')
    say(f'\n[ok  ] 索引 → {out_dir}'
        f'（{len(works)} 作品・{n:,} 形態素・'
        f'表層形 {len(s_ids):,} 種・語彙素 {len(l_ids):,} 種）')
    say(f'       辞書 {prov["dictionary"]} {prov["dictionary_version"]}'
        f'／作成 {prov["built_at"]}')
    return prov


# ---------------------------------------------------------------------------
# 検索
# ---------------------------------------------------------------------------
class QueryError(ValueError):
    """検索式の書き方が違う。**黙って0件にしないための例外。**"""


class Term:
    """検索式の1項。"""

    __slots__ = ('raw', 'stream', 'forms', 'pos', 'any')

    def __init__(self, raw: str, stream: str, forms, pos: str, any_: bool):
        self.raw, self.stream, self.forms, self.pos, self.any = (
            raw, stream, forms, pos, any_)


class KwicIndex:
    """索引を読み，検索して用例を返す。

    ノートブックからはこう使う::

        from kwic_core import KwicIndex
        kw = KwicIndex(ROOT/'data'/'kwic')
        res = kw.search('汽車', stream='lemma', context=6)
        show(kw.to_frame(res), caption='汽車 の用例')
    """

    def __init__(self, index_dir: str | os.PathLike):
        d = Path(index_dir)
        jp = d / 'kwic_index.json'
        arr_dir, npz = d / 'arrays', d / 'kwic_tokens.npz'
        if not jp.exists() or not (arr_dir.is_dir() or npz.exists()):
            raise SystemExit(
                f'索引が無い: {d}\n'
                '  python3 scripts/15_kwic_index.py を先に実行すること。')
        with open(jp, encoding='utf-8') as fh:
            meta = json.load(fh)
        keys = ('surf', 'lem', 'pos', 'cfm', 'gos', 'work', 'sent')
        # 版 2 で加えた配列。版 1 の索引には無い
        opt = ('orth', 'pos12')
        if arr_dir.is_dir():
            # **mmap で開く。** 読み込みが瞬時に終わり，使ったページだけが
            # メモリに載る（1000万形態素でも起動を待たない）。
            keys = keys + tuple(k for k in opt
                                if (arr_dir / f'{k}.npy').exists()
                                and k in meta['vocab'])
            z = {k: np.load(arr_dir / f'{k}.npy', mmap_mode='r') for k in keys}
        else:
            # 古い索引（圧縮 .npz）。読めるが起動が遅い
            print('[warn] 古い形の索引（kwic_tokens.npz）である。'
                  '起動が遅いので 15_kwic_index.py で作り直すとよい。')
            z = np.load(npz)
        self.dir = d
        self.prov = meta['provenance']
        self.works = meta['works']
        self.band_labels = meta['band_labels']
        self.v = {k: meta['vocab'][k] for k in ('surf', 'lem', 'pos', 'cfm', 'gos')}
        for k in opt:
            if k in keys:
                self.v[k] = meta['vocab'][k]
        # 異綴形と品詞の中分類が使えるか（版 2 以降の索引）
        self.v2 = all(k in keys for k in opt)
        self.a = {k: z[k] for k in keys}
        self.n = len(self.a['surf'])
        # 語彙素・表層形の逆引き（語形 → 番号）
        self._rev = {k: {w: i for i, w in enumerate(self.v[k])}
                     for k in ('surf', 'lem')}
        self._pos_of = {p: i for i, p in enumerate(self.v['pos'])}
        self._punct = np.array(
            [i for p, i in self._pos_of.items() if p in PUNCT_POS], dtype=np.int16)
        self._freq: dict[str, np.ndarray] = {}
        self._rank: dict[str, np.ndarray] = {}
        self._cfreq: dict[tuple, tuple] = {}
        self._vtab: dict[tuple, dict] = {}

    # -- 補助 --------------------------------------------------------------
    def stream_key(self, stream: str) -> str:
        if stream in ('lemma', 'lem', '語彙素'):
            return 'lem'
        if stream in ('surface', 'surf', '表層形'):
            return 'surf'
        raise QueryError(f'列の名前が違う: {stream}（lemma か surface）')

    def rank(self, key: str) -> np.ndarray:
        """語形の番号 → **辞書順の順位**。並べ替えを数値で行うために使う。

        文字列の比較を Python で何十万回も行うと遅い。順位に直しておけば
        ``np.lexsort`` で同じ順序が出る（語彙の大きさぶん1回だけ作る）。
        """
        if key not in self._rank:
            vocab = self.v[key]
            order = sorted(range(len(vocab)), key=vocab.__getitem__)
            r = np.empty(len(vocab), dtype=np.int64)
            r[np.asarray(order, dtype=np.int64)] = np.arange(len(vocab))
            self._rank[key] = r
        return self._rank[key]

    def freq(self, key: str) -> np.ndarray:
        """語形ごとの延べ頻度（共起統計に使う）。"""
        if key not in self._freq:
            self._freq[key] = np.bincount(self.a[key],
                                          minlength=len(self.v[key]))
        return self._freq[key]

    # -- 検索式 ------------------------------------------------------------
    def parse(self, query: str, stream: str = 'lemma') -> list[Term]:
        """検索式を項の並びにする。**分からない書き方は例外にする。**"""
        base = self.stream_key(stream)
        q = (query or '').strip()
        if not q:
            raise QueryError('検索語が空である')
        terms: list[Term] = []
        for raw in q.split():
            t, key = raw, base
            if t[:2] in ('L:', 'l:'):
                t, key = t[2:], 'lem'
            elif t[:2] in ('S:', 's:'):
                t, key = t[2:], 'surf'
            pos = ''
            if '/' in t:
                t, pos = t.split('/', 1)
            if not t and not pos:
                raise QueryError(f'項が空である: {raw}')
            if t in ('', '*'):
                terms.append(Term(raw, key, None, pos, True))
                continue
            vocab = self.v[key]
            rev = self._rev[key]
            if t.startswith('re:'):
                try:
                    rx = re.compile(t[3:])
                except re.error as e:
                    raise QueryError(f'正規表現が誤っている（{raw}）: {e}') from e
                ids = [i for i, w in enumerate(vocab) if rx.search(w)]
            elif any(ch in t for ch in '*?[]'):
                pats = t.split('|')
                ids = [i for i, w in enumerate(vocab)
                       if any(fnmatch.fnmatchcase(w, p) for p in pats)]
            else:
                ids = [rev[w] for w in t.split('|') if w in rev]
            if not ids:
                raise QueryError(
                    f'「{t}」に当たる語形が{"語彙素" if key == "lem" else "表層形"}'
                    'の語彙に無い。'
                    '列（語彙素／表層形）の選び違い，辞書の切り方'
                    '（「非常に」→「非常」＋「に」），旧仮名の表記を疑うこと。')
            terms.append(Term(raw, key, np.asarray(sorted(ids), dtype=np.int64),
                              pos, False))
        return terms

    def _lut_mask(self, key: str, ids) -> np.ndarray:
        """語形の番号の集合に当たる位置を真にする。

        ``np.isin`` は照合する集合が大きいと遅い（``語*`` のように語彙の
        ほとんどに当たる式では**20 秒近く**かかる）。語彙の大きさの真偽表を
        作って一度引くだけにすれば，集合の大きさに依存しない。
        """
        lut = np.zeros(len(self.v[key]) + 1, dtype=bool)
        lut[np.asarray(ids, dtype=np.int64)] = True
        return lut[self.a[key]]

    def _term_mask(self, t: Term) -> np.ndarray:
        """その項に当たる位置の真偽配列。"""
        if t.any:
            m = np.ones(self.n, dtype=bool)
        else:
            m = self._lut_mask(t.stream, t.forms)
        if t.pos:
            # 品詞は大分類の前方一致（``/名`` で名詞，``/動詞`` で動詞）
            pids = [i for p, i in self._pos_of.items() if p.startswith(t.pos)]
            if not pids:
                raise QueryError(
                    f'品詞「{t.pos}」は索引に無い。'
                    f'使えるのは: {"，".join(sorted(self._pos_of))}')
            m &= self._lut_mask('pos', pids)
        return m

    # -- 本体 --------------------------------------------------------------
    def search(self, query: str, *, stream: str = 'lemma', context: int = 7,
               works: list[int] | None = None, bands: list[int] | None = None,
               authors: list[str] | None = None, styles: list[str] | None = None,
               genres: list[str] | None = None, exclude_punct: bool = False,
               sort: str = 'position', limit: int = 200, offset: int = 0,
               sample: int = 0, seed: int = 20260920,
               collocates: int = 0, coll_window: int = 4,
               coll_sort: str = 'logdice', coll_pos: list[str] | None = None,
               coll_min: int = 2, variants: bool = True) -> dict:
        """検索して用例と集計を返す。

        ``context`` は前後の語数。``sort`` は
        ``position`` / ``left1`` / ``left2`` / ``right1`` / ``right2`` /
        ``year`` / ``author`` / ``title`` / ``random``。

        共起語は ``coll_sort`` の指標で上位 ``collocates`` 語を選ぶ
        （``COLL_MEASURES`` の名前）。``coll_pos`` は共起語の品詞
        （``名詞`` のような大分類か ``名詞-普通名詞`` のような中分類の並び）。
        ``variants`` が真なら，一致した語の**異綴形**（書字形基本形）と
        その内訳を返す（版 2 の索引が要る）。
        """
        import time
        t0 = time.time()
        terms = self.parse(query, stream)
        base = self.stream_key(stream)

        # 連なりの先頭位置を求める。**文境界を越えない**（隣の文の語を
        # 「次の語」として拾うと，ありえない連接が用例として出てしまう）。
        hits = np.flatnonzero(self._term_mask(terms[0]))
        for k, t in enumerate(terms[1:], start=1):
            if hits.size == 0:
                break
            nxt = hits + k
            hits = hits[nxt < self.n]
            nxt = hits + k
            hits = hits[self.a['sent'][nxt] == self.a['sent'][hits]]
            nxt = hits + k
            hits = hits[self._term_mask(t)[nxt]]
        span = len(terms)

        # 絞り込み
        if hits.size:
            keep = np.ones(hits.size, dtype=bool)
            wid = self.a['work'][hits]
            if works:
                keep &= np.isin(wid, np.asarray(works, dtype=np.int16))
            if bands:
                bmap = np.asarray([w['band'] for w in self.works], dtype=np.int16)
                keep &= np.isin(bmap[wid], np.asarray(bands, dtype=np.int16))
            for field, want in (('author', authors), ('style', styles),
                                ('genre', genres)):
                if want:
                    vmap = {i for i, w in enumerate(self.works)
                            if w[field] in set(want)}
                    keep &= np.isin(wid, np.asarray(sorted(vmap), dtype=np.int16))
            if exclude_punct:
                keep &= ~np.isin(np.asarray(self.a['pos'][hits]), self._punct)
            hits = hits[keep]

        total = int(hits.size)
        rng = np.random.default_rng(seed)
        sampled = False
        if sample and total > sample:
            # **間引いたことを必ず返す。** 何件から何件を見ているのかが
            # 分からない用例集は，数えたことにならない。
            hits = np.sort(rng.choice(hits, sample, replace=False))
            sampled = True

        # 集計は**並べ替えと頁より先に**（表示件数に依存させない）。
        # ⚠ ここは numpy で数える。``/助動詞`` のように何十万件も当たる式で
        # Python のループを回すと十数秒かかる。
        wid = np.asarray(self.a['work'][hits], dtype=np.int64)
        wcnt = np.bincount(wid, minlength=len(self.works))
        by_work = Counter({int(i): int(c) for i, c in enumerate(wcnt) if c})
        bmap = np.asarray([w['band'] for w in self.works], dtype=np.int64)
        bcnt = np.bincount(bmap[wid] + 1, minlength=len(self.band_labels) + 1)
        by_band = Counter({int(i) - 1: int(c) for i, c in enumerate(bcnt) if c})
        types, types_capped = self._type_tally(hits, span, base)
        coll_info = {}
        if collocates:
            coll, coll_info = self._collocates(
                hits, span, base, coll_window, collocates,
                measure=coll_sort, pos_sel=coll_pos, min_co=coll_min)
        else:
            coll = []
        var = self._variants(hits, span) if variants else None

        order = self._order(hits, span, base, sort, rng)
        shown = order[offset:offset + limit] if limit else order
        rows = [self._row(int(i), span, context, base) for i in shown]

        return {
            'query': query, 'stream': base, 'span': span,
            'terms': [t.raw for t in terms],
            'total': total, 'sampled': sampled, 'sample': int(sample or 0),
            'seed': int(seed), 'shown': len(rows), 'offset': int(offset),
            'sort': sort, 'context': int(context),
            'rows': rows,
            'by_work': [{'work': k, 'stem': self.works[k]['stem'],
                         'author': self.works[k]['author'],
                         'title': self.works[k]['title'],
                         'band': self.works[k]['band'],
                         'hits': v, 'tokens': self.works[k]['tokens'],
                         'per_10k': round(v / max(1, self.works[k]['tokens']) * 1e4, 2)}
                        for k, v in by_work.most_common()],
            'by_band': self._band_table(by_band),
            'types': types, 'types_capped': types_capped,
            'collocates': coll, 'coll_info': coll_info,
            'variants': var,
            'per_million': round(total / max(1, self.n) * 1e6, 2),
            'elapsed_ms': int((time.time() - t0) * 1000),
            'provenance': self.prov,
        }

    # -- 一致した語形の集計 ------------------------------------------------
    def _type_tally(self, hits: np.ndarray, span: int, base: str,
                    topn: int = 50, cap: int = 2_000_000):
        """一致した語形を数える。**連なりは全体を1つの語形として数える。**

        何十万件も当たる式のために，Python のループを回さず番号の組を
        整数1つにまとめて ``np.unique`` で数える。桁が溢れるほど長い連なり
        （語彙が大きく span が4以上など）のときだけ，先頭 ``cap`` 件で
        打ち切り，**打ち切ったことを返す**（黙って一部だけ数えない）。
        """
        if hits.size == 0:
            return [], False
        V = len(self.v[base])
        arr = self.a[base]
        capped = False
        if span == 1:
            codes = np.asarray(arr[hits], dtype=np.int64)
        elif V ** span < (1 << 62):
            codes = np.zeros(hits.size, dtype=np.int64)
            for k in range(span):
                codes = codes * V + np.asarray(arr[hits + k], dtype=np.int64)
        else:
            h = hits[:cap]
            capped = hits.size > cap
            codes = np.zeros(h.size, dtype=np.int64)
            for k in range(span):
                codes = codes * V + np.asarray(arr[h + k], dtype=np.int64)
        vals, cnt = np.unique(codes, return_counts=True)
        top = np.argsort(-cnt)[:topn]
        out = []
        for i in top:
            c = int(vals[i])
            parts = []
            for _ in range(span):
                parts.append(self.v[base][c % V])
                c //= V
            out.append({'form': ' '.join(reversed(parts)), 'hits': int(cnt[i])})
        return out, capped

    # -- 並べ替え ----------------------------------------------------------
    def _order(self, hits: np.ndarray, span: int, base: str, sort: str,
               rng) -> np.ndarray:
        if hits.size == 0:
            return hits
        if sort in ('position', '', None):
            return hits
        if sort == 'random':
            return rng.permutation(hits)
        # ⚠ **文字列を Python で並べ替えない。** 何十万件も当たる式では
        # それだけで数秒かかる。語形を「辞書順の順位」に直しておけば，
        # 数値の並べ替え（np.lexsort）で**同じ順序**が得られる。
        sent = self.a['sent']

        def ctx_rank(off: int) -> np.ndarray:
            # 文境界を越えた先は -1（空文字と同じ扱い＝先頭）にする。
            # 並べ替えのキーに隣の文の語を使ってはいけない。
            j = hits + off
            ok = (j >= 0) & (j < self.n)
            jj = np.clip(j, 0, self.n - 1)
            ok &= self.a['sent'][jj] == sent[hits]
            r = np.full(hits.size, -1, dtype=np.int64)
            r[ok] = self.rank(base)[np.asarray(self.a[base][jj[ok]],
                                               dtype=np.int64)]
            return r

        if sort in ('left1', 'left2', 'right1', 'right2'):
            if sort.startswith('left'):
                offs = [-1] if sort == 'left1' else [-1, -2]
            else:
                offs = [span] if sort == 'right1' else [span, span + 1]
            keys = [ctx_rank(o) for o in offs]
            # lexsort は**最後のキーが主**なので逆順に積む
            idx = np.lexsort(tuple(reversed(keys + [hits.astype(np.int64)])))
        elif sort in ('year', 'author', 'title'):
            wid = np.asarray(self.a['work'][hits], dtype=np.int64)
            if sort == 'year':
                def yr(w):
                    try:
                        return int(float(w['year']))
                    except (TypeError, ValueError):
                        return 9999        # 不明は最後
                prim = np.asarray([yr(w) for w in self.works], dtype=np.int64)[wid]
                names = sorted({w['author'] for w in self.works})
                nrk = {n: i for i, n in enumerate(names)}
                sec = np.asarray([nrk[w['author']] for w in self.works],
                                 dtype=np.int64)[wid]
            else:
                vals = sorted({w[sort] or '\uffff' for w in self.works})
                vrk = {n: i for i, n in enumerate(vals)}
                prim = np.asarray([vrk[w[sort] or '\uffff'] for w in self.works],
                                  dtype=np.int64)[wid]
                sec = np.zeros(hits.size, dtype=np.int64)
            idx = np.lexsort((hits.astype(np.int64), sec, prim))
        else:
            raise QueryError(f'並べ替えの指定が違う: {sort}')
        return hits[idx]

    # -- 1行ぶん ----------------------------------------------------------
    def _row(self, i: int, span: int, context: int, base: str) -> dict:
        sent = self.a['sent']
        s = sent[i]
        lo = max(0, i - context)
        hi = min(self.n, i + span + context)
        # 文をまたいだ文脈は出すが，**どこで文が切れたかを記録する**
        def seq(a: int, b: int) -> list[dict]:
            out = []
            for j in range(a, b):
                out.append({
                    'surf': self.v['surf'][int(self.a['surf'][j])],
                    'lem': self.v['lem'][int(self.a['lem'][j])],
                    'pos': self.v['pos'][int(self.a['pos'][j])],
                    'same_sent': bool(sent[j] == s),
                })
            return out

        w = self.works[int(self.a['work'][i])]
        return {
            'pos_i': i, 'work': w['i'], 'stem': w['stem'],
            'author': w['author'] or '（メタデータ無し）',
            'title': w['title'] or w['stem'],
            'year': w['year'], 'band': w['band'],
            'period': w['period'], 'style': w['style'],
            'has_meta': w['meta'],
            'left': seq(lo, i), 'key': seq(i, i + span), 'right': seq(i + span, hi),
            'in_work': int(i - w['start']),
        }

    # -- 時代別の表 -------------------------------------------------------
    def _band_table(self, by_band: Counter) -> list[dict]:
        tok = Counter()
        for w in self.works:
            tok[w['band']] += w['tokens']
        # **不明は最後に置く。** 先頭に来ると時代の並びが読めない
        order = list(range(len(self.band_labels)))
        order += [b for b in sorted(by_band) if b not in order]
        out = []
        for b in order:
            lab = (self.band_labels[b] if 0 <= b < len(self.band_labels)
                   else '初出年不明')
            h, t = by_band.get(b, 0), tok.get(b, 0)
            out.append({'band': b, 'label': lab, 'hits': h, 'tokens': t,
                        'per_10k': round(h / t * 1e4, 3) if t else 0.0})
        return out

    # -- 品詞の選択 -------------------------------------------------------
    def pos_lut(self, sel: list[str] | None) -> tuple[str, np.ndarray] | None:
        """品詞の選択 → (配列の名前, 真偽表)。``None`` は絞り込みなし。

        選択は大分類（``名詞``）と中分類（``名詞-普通名詞``）を混ぜてよい。
        大分類を選ぶとその下の中分類すべてに当たる。
        """
        if not sel:
            return None
        sel = [s for s in sel if s]
        if not sel:
            return None
        if self.v2:
            key, vocab = 'pos12', self.v['pos12']
        else:
            if any('-' in s for s in sel):
                raise QueryError(
                    '品詞の中分類で絞るには索引を作り直すこと'
                    '（この索引は版 1。python3 scripts/15_kwic_index.py）。')
            key, vocab = 'pos', self.v['pos']
        want = set(sel)
        lut = np.zeros(len(vocab) + 1, dtype=bool)
        for i, p in enumerate(vocab):
            if p in want or p.split('-', 1)[0] in want:
                lut[i] = True
        if not lut.any():
            raise QueryError(f'品詞「{"，".join(sel)}」は索引に無い。')
        return key, lut

    def _content_mask(self, pos_sel) -> tuple[np.ndarray, str]:
        """句読点でなく，選んだ品詞に当たる位置の真偽配列（とその鍵）。"""
        punct = np.zeros(len(self.v['pos']) + 1, dtype=bool)
        if self._punct.size:
            punct[np.asarray(self._punct, dtype=np.int64)] = True
        m = ~punct[np.asarray(self.a['pos'], dtype=np.int64)]
        pl = self.pos_lut(pos_sel)
        tag = ''
        if pl:
            key, lut = pl
            m &= lut[np.asarray(self.a[key], dtype=np.int64)]
            tag = '|'.join(sorted(pos_sel))
        return m, tag

    def _coll_freq(self, base: str, pos_sel) -> tuple[np.ndarray, float]:
        """共起語の側の頻度 f(c) と母数 N。**品詞で絞るなら頻度も同じ品詞で数える。**

        名詞だけを共起語に選んだのに，頻度に動詞としての出現まで入れると
        期待値がずれる（同じ語彙素が複数の品詞をもつことがある）。
        母数 N は句読点を除いた形態素数（ウィンドウも句読点を数えない）。
        """
        ck = (base, '|'.join(sorted(pos_sel or [])))
        if ck not in self._cfreq:
            m, _ = self._content_mask(pos_sel)
            arr = np.asarray(self.a[base], dtype=np.int64)
            f = np.bincount(arr[m], minlength=len(self.v[base])).astype(np.float64)
            if pos_sel:
                allm, _ = self._content_mask(None)
                n = float(allm.sum())
            else:
                n = float(m.sum())
            self._cfreq[ck] = (f, n)
        return self._cfreq[ck]

    # -- 共起語 -----------------------------------------------------------
    def _collocates(self, hits: np.ndarray, span: int, base: str,
                    window: int, topn: int, *, measure: str = 'logdice',
                    pos_sel: list[str] | None = None,
                    min_co: int = 2) -> tuple[list[dict], dict]:
        """ウィンドウ内の共起語を数え，``measure`` の指標で上位を選ぶ。

        2×2 の分割表で数える（Evert 2008 の数え方）。

        ==========  =================  ======================
                    共起語 c           c 以外
        ==========  =================  ======================
        窓の中      O11                O12 = W − O11
        窓の外      O21 = f(c) − O11   O22
        ==========  =================  ======================

        W はウィンドウに入った形態素の延べ数（句読点・文境界の外は数えない），
        N は句読点を除いた全形態素数，期待値 E11 = W·f(c)/N。
        指標は ``COLL_MEASURES`` を見ること。
        """
        if measure not in COLL_MEASURES:
            raise QueryError(f'共起語の指標の指定が違う: {measure}'
                             f'（{"，".join(COLL_MEASURES)}）')
        info = {'measure': measure, 'window': int(window),
                'pos': list(pos_sel or []), 'min_co': int(min_co)}
        if hits.size == 0:
            return [], info
        arr = self.a[base]
        sent = self.a['sent']
        f_c_all, N = self._coll_freq(base, pos_sel)
        V = len(self.v[base])
        punct_lut = np.zeros(len(self.v['pos']) + 1, dtype=bool)
        if self._punct.size:
            punct_lut[np.asarray(self._punct, dtype=np.int64)] = True
        pl = self.pos_lut(pos_sel)
        # ⚠ **ウィンドウの中を Python の二重ループで回さない。** 何十万件も当たる式で
        # 数秒かかる。ずらし幅ごとに一括で拾って ``bincount`` で数える
        # （結果は同じ）。
        co = np.zeros(V, dtype=np.int64)
        W = 0
        s0 = np.asarray(sent[hits])
        offs = ([-k for k in range(1, window + 1)]
                + [span + k for k in range(window)])
        for o in offs:
            j = hits + o
            ok = (j >= 0) & (j < self.n)
            jj = np.clip(j, 0, self.n - 1)
            ok &= np.asarray(sent[jj]) == s0          # 文境界を越えない
            ok &= ~punct_lut[np.asarray(self.a['pos'][jj], dtype=np.int64)]
            W += int(ok.sum())                        # 窓の大きさは品詞で絞る前に数える
            if pl:
                key, lut = pl
                ok &= lut[np.asarray(self.a[key][jj], dtype=np.int64)]
            if ok.any():
                co += np.bincount(np.asarray(arr[jj[ok]], dtype=np.int64),
                                  minlength=V)
        f_node = float(hits.size)
        info.update({'W': W, 'N': int(N), 'node': int(f_node)})
        idx = np.flatnonzero((co >= max(1, min_co)) & (f_c_all >= 3))
        if idx.size == 0 or W == 0:
            return [], info
        o11 = co[idx].astype(np.float64)
        fc = f_c_all[idx]
        Wf = float(W)
        o12 = Wf - o11
        o21 = fc - o11
        o22 = N - Wf - o21
        e11 = Wf * fc / N
        e12 = Wf * (N - fc) / N
        e21 = (N - Wf) * fc / N
        e22 = (N - Wf) * (N - fc) / N

        def xlx(o, e):
            with np.errstate(divide='ignore', invalid='ignore'):
                return np.where(o > 0, o * np.log(o / e), 0.0)
        g2 = 2 * (xlx(o11, e11) + xlx(o12, e12) + xlx(o21, e21) + xlx(o22, e22))
        g2 = np.where(o11 < e11, -g2, g2)             # 反発（期待より少ない）は負
        M = {
            'co': o11,
            'logdice': 14 + np.log2(2 * o11 / (f_node + fc)),
            'mi': np.log2(o11 / e11),
            'mi3': np.log2(o11 ** 3 / e11),
            't': (o11 - e11) / np.sqrt(o11),
            'g2': g2,
            'dp_fwd': o11 / Wf - o21 / (N - Wf),
            'dp_bwd': o11 / fc - o12 / (N - fc),
        }
        key = M[measure]
        order = np.lexsort((-o11, -key))[:topn]       # 同点は共起頻度の多い順
        out = []
        for k in order:
            tid = int(idx[k])
            row = {'form': self.v[base][tid], 'co': int(o11[k]),
                   'freq': int(fc[k]), 'exp': round(float(e11[k]), 3)}
            for m, v in M.items():
                if m == 'co':
                    continue
                d = 4 if m.startswith('dp') else 2
                row[m] = round(float(v[k]), d)
            out.append(row)
        return out, info

    # -- 異綴形（書字形基本形）---------------------------------------------
    def _band_of_work(self) -> np.ndarray:
        """作品番号 → 時代区分の行番号（0..区分数-1，不明は区分数）。"""
        nb = len(self.band_labels)
        return np.asarray([w['band'] if 0 <= w['band'] < nb else nb
                           for w in self.works], dtype=np.int64)

    def _variants(self, hits: np.ndarray, span: int,
                  top: int = 12) -> dict | None:
        """一致した語の**異綴形**（書字形基本形）と，時代区分・作家・作品ごとの内訳。

        書字形基本形（orthBase）は活用を基本形にまとめ，表記の違い（漢字か仮名か・
        どの漢字か・仮名遣い）を残す。どう残るかは辞書による（歴史的仮名遣いの
        「云つた」を 云ふ とする辞書も 云う とする辞書もある）。したがって活用の
        違いは異綴形として数えない。連なりのときは各語の書字形基本形を並べて1つとする。

        ⚠ UniDic の語彙素は，意味で書き分ける別字（伯父／叔父，指す／刺す）や
        読みの違う語（縁：えん／ふち）も1つにまとめる。ここに並ぶのは綴りの
        揺れだけではない。
        上位 ``top`` 種より後は「その他」にまとめ，**まとめたことを返す**。
        """
        if not self.v2:
            return {'available': False,
                    'reason': '異綴形を出すには索引を作り直すこと（この索引は版 1。'
                              'python3 scripts/15_kwic_index.py）。'}
        if hits.size == 0:
            return {'available': True, 'total': 0, 'forms': [], 'other': None,
                    'by_band': [], 'by_author': [], 'by_work': []}
        V = len(self.v['orth'])
        arr = self.a['orth']
        codes = np.zeros(hits.size, dtype=np.int64)
        big = V ** span >= (1 << 62)
        if big:
            # 長すぎる連なり：文字列で数える（まれなので遅くてよい）
            strs = [' '.join(self.v['orth'][int(arr[h + k])] for k in range(span))
                    for h in hits]
            uniq, inv = np.unique(np.asarray(strs, dtype=object), return_inverse=True)
            names = [str(u) for u in uniq]
        else:
            for k in range(span):
                codes = codes * V + np.asarray(arr[hits + k], dtype=np.int64)
            uniq, inv = np.unique(codes, return_inverse=True)

            def name(c):
                parts = []
                c = int(c)
                for _ in range(span):
                    parts.append(self.v['orth'][c % V])
                    c //= V
                return ' '.join(reversed(parts))
            names = [name(c) for c in uniq]
        cnt = np.bincount(inv, minlength=len(names))
        order = np.argsort(-cnt, kind='stable')
        keep = order[:top]
        col = np.full(len(names), len(keep), dtype=np.int64)   # その他＝最後の列
        col[keep] = np.arange(len(keep))
        c = col[inv]
        ncol = len(keep) + (1 if len(names) > top else 0)
        total = int(hits.size)
        forms = [{'form': names[i], 'hits': int(cnt[i]),
                  'share': round(int(cnt[i]) / total, 4)} for i in keep]
        other = None
        if len(names) > top:
            oh = int(cnt[order[top:]].sum())
            other = {'types': int(len(names) - top), 'hits': oh,
                     'share': round(oh / total, 4)}

        wid = np.asarray(self.a['work'][hits], dtype=np.int64)

        def table(group: np.ndarray, labels: list[str], extra=None) -> list[dict]:
            G = len(labels)
            m = np.bincount(group * ncol + c, minlength=G * ncol).reshape(G, ncol)
            rows = []
            for g in range(G):
                tot = int(m[g].sum())
                if not tot:
                    continue
                r = {'label': labels[g], 'total': tot,
                     'counts': [int(x) for x in m[g]]}
                if extra:
                    r.update(extra(g))
                rows.append(r)
            return rows

        nb = len(self.band_labels)
        band_rows = table(self._band_of_work()[wid],
                          list(self.band_labels) + ['初出年不明'])
        authors = sorted({w['author'] or '（メタデータ無し）' for w in self.works})
        aidx = {a: i for i, a in enumerate(authors)}
        amap = np.asarray([aidx[w['author'] or '（メタデータ無し）']
                           for w in self.works], dtype=np.int64)
        au_rows = sorted(table(amap[wid], authors), key=lambda r: -r['total'])
        wlab = [f"{w['author'] or '（メタデータ無し）'}『{w['title'] or w['stem']}』"
                for w in self.works]
        wk_rows = sorted(table(wid, wlab, lambda g: {
            'year': self.works[g]['year'],
            'band': self.works[g]['band']}), key=lambda r: -r['total'])
        return {'available': True, 'total': total, 'forms': forms, 'other': other,
                'types': int(len(names)),
                'by_band': band_rows, 'by_author': au_rows, 'by_work': wk_rows,
                'n_bands': nb}

    def variant_table(self, *, min_freq: int = 20, min_var: int = 2,
                      min_types: int = 2, pos: list[str] | None = None,
                      sort: str = 'minor', limit: int = 300) -> dict:
        """**コーパス全体で表記の揺れが大きい語彙素**の一覧。

        語彙素ごとに書字形基本形を数える。``min_var`` 回未満の書字形は
        誤解析や一回きりの表記であることが多いので，異綴形として数えない
        （頻度には含める）。並べ方 ``sort`` は

          ``minor``  主要形以外の割合（揺れの大きさ）の大きい順
          ``types``  異綴形の数の多い順
          ``freq``   頻度の高い順
        """
        if not self.v2:
            raise QueryError('異綴形の一覧には索引を作り直すこと（この索引は版 1。'
                             'python3 scripts/15_kwic_index.py）。')
        if sort not in ('minor', 'types', 'freq'):
            raise QueryError(f'並べ方の指定が違う: {sort}（minor・types・freq）')
        ck = '|'.join(sorted(pos or []))
        if ck not in self._vtab:
            m, _ = self._content_mask(pos)
            lem = np.asarray(self.a['lem'], dtype=np.int64)[m]
            orth = np.asarray(self.a['orth'], dtype=np.int64)[m]
            Vo = len(self.v['orth'])
            u, cnt = np.unique(lem * Vo + orth, return_counts=True)
            L, O = u // Vo, u % Vo
            # 語彙素ごとの品詞（最も多いもの）
            P = len(self.v['pos'])
            pos_arr = np.asarray(self.a['pos'], dtype=np.int64)[m]
            up, pc = np.unique(lem * P + pos_arr, return_counts=True)
            main_pos = {}
            for code, n_ in zip(up.tolist(), pc.tolist()):
                li, pi = divmod(code, P)
                if n_ > main_pos.get(li, (0, 0))[0]:
                    main_pos[li] = (n_, pi)
            self._vtab[ck] = {'L': L, 'O': O, 'cnt': cnt, 'main_pos': main_pos}
        d = self._vtab[ck]
        L, O, cnt = d['L'], d['O'], d['cnt']
        # 語彙素ごとの区切り（u は語彙素の順に並んでいる）
        bounds = np.flatnonzero(np.diff(L)) + 1
        starts = np.concatenate(([0], bounds))
        ends = np.concatenate((bounds, [L.size]))
        tot = np.add.reduceat(cnt, starts)
        good = tot >= min_freq
        rows = []
        for s, e, T in zip(starts[good], ends[good], tot[good]):
            c = cnt[s:e]
            valid = c >= min_var
            if int(valid.sum()) < min_types:
                continue
            o = O[s:e]
            order = np.argsort(-c, kind='stable')
            main = int(c[order[0]])
            li = int(L[s])
            rows.append({
                'lemma': self.v['lem'][li],
                'pos': self.v['pos'][d['main_pos'].get(li, (0, 0))[1]],
                'freq': int(T), 'types': int(valid.sum()),
                'main': self.v['orth'][int(o[order[0]])],
                'minor_share': round(1 - main / int(T), 4),
                'forms': [{'form': self.v['orth'][int(o[i])], 'hits': int(c[i])}
                          for i in order if c[i] >= min_var][:8],
            })
        key = {'minor': lambda r: (-r['minor_share'], -r['freq']),
               'types': lambda r: (-r['types'], -r['minor_share'], -r['freq']),
               'freq': lambda r: (-r['freq'], -r['minor_share'])}[sort]
        rows.sort(key=key)
        return {'rows': rows[:limit], 'total': len(rows), 'min_freq': min_freq,
                'min_var': min_var, 'min_types': min_types,
                'pos': list(pos or []), 'sort': sort}

    # -- 広い文脈（テクストに戻る）----------------------------------------
    def passage(self, pos_i: int, *, before: int = 120, after: int = 120,
                span: int = 1) -> dict:
        """1件の用例の前後を広く返す。**文単位で切る。**"""
        i = int(pos_i)
        if not (0 <= i < self.n):
            raise QueryError(f'位置が索引の外である: {pos_i}')
        w = self.works[int(self.a['work'][i])]
        lo = max(w['start'], i - before)
        hi = min(w['end'], i + span + after)
        sent = self.a['sent']
        # 文の頭・末まで広げる（読めるようにする）
        while lo > w['start'] and sent[lo - 1] == sent[lo]:
            lo -= 1
        while hi < w['end'] and sent[hi - 1] == sent[hi]:
            hi += 1
        toks = []
        for j in range(lo, hi):
            toks.append({
                'surf': self.v['surf'][int(self.a['surf'][j])],
                'lem': self.v['lem'][int(self.a['lem'][j])],
                'pos': self.v['pos'][int(self.a['pos'][j])],
                'cfm': self.v['cfm'][int(self.a['cfm'][j])],
                'gos': self.v['gos'][int(self.a['gos'][j])],
                'key': bool(i <= j < i + span),
                'eos': bool(j + 1 < self.n and sent[j + 1] != sent[j]),
            })
        return {
            'pos_i': i, 'from': lo, 'to': hi, 'tokens': toks,
            'work': {k: w[k] for k in ('stem', 'author', 'title', 'year',
                                       'period', 'style', 'band', 'meta')},
            'band_label': (self.band_labels[w['band']]
                           if 0 <= w['band'] < len(self.band_labels)
                           else '初出年不明'),
            'in_work': int(i - w['start']), 'work_tokens': w['tokens'],
        }

    # -- 品詞の木（共起語の絞り込みの選択肢）-----------------------------
    def pos_tree(self) -> list[dict]:
        """大分類 → 中分類の木と延べ数。句読点は入れない。"""
        key = 'pos12' if self.v2 else 'pos'
        cnt = np.bincount(np.asarray(self.a[key], dtype=np.int64),
                          minlength=len(self.v[key]))
        tree: dict[str, dict] = {}
        for i, p in enumerate(self.v[key]):
            p1, _, p2 = p.partition('-')
            if not p1 or p1 in PUNCT_POS or not cnt[i]:
                continue
            node = tree.setdefault(p1, {'pos': p1, 'tokens': 0, 'children': []})
            node['tokens'] += int(cnt[i])
            if p2:
                node['children'].append({'pos': p, 'label': p2,
                                         'tokens': int(cnt[i])})
        out = sorted(tree.values(), key=lambda x: -x['tokens'])
        for x in out:
            x['children'].sort(key=lambda c: -c['tokens'])
        return out

    # -- 絞り込みの選択肢 --------------------------------------------------
    def facets(self) -> dict:
        authors = Counter(w['author'] or '（メタデータ無し）' for w in self.works)
        return {
            'authors': [{'name': k, 'works': v} for k, v in
                        sorted(authors.items(), key=lambda x: (-x[1], x[0]))],
            'bands': [{'band': i, 'label': l,
                       'works': sum(1 for w in self.works if w['band'] == i)}
                      for i, l in enumerate(self.band_labels)]
                     + [{'band': -1, 'label': '初出年不明',
                         'works': sum(1 for w in self.works if w['band'] == -1)}],
            'styles': [{'name': k, 'works': v} for k, v in
                       Counter(w['style'] for w in self.works
                               if w['style']).most_common()],
            'genres': [{'name': k, 'works': v} for k, v in
                       Counter(w['genre'] for w in self.works
                               if w['genre']).most_common()],
            'works': [{'work': w['i'], 'stem': w['stem'], 'author': w['author'],
                       'title': w['title'], 'year': w['year'],
                       'band': w['band'], 'tokens': w['tokens'],
                       'meta': w['meta']} for w in self.works],
            'pos': sorted(p for p in self.v['pos'] if p),
            'pos_tree': self.pos_tree(),
            'coll_measures': COLL_MEASURES,
            'index_v2': self.v2,
            'api': API_VERSION,
            'band_labels': self.band_labels,
            'provenance': self.prov,
        }

    # -- 表にする（ノートブック用）----------------------------------------
    def to_frame(self, res: dict, *, join: str = ''):
        """検索結果を pandas の表にする。``show()`` にそのまま渡せる。"""
        import pandas as pd
        rows = []
        for r in res['rows']:
            left = join.join(t['surf'] for t in r['left'])
            key = join.join(t['surf'] for t in r['key'])
            right = join.join(t['surf'] for t in r['right'])
            rows.append({
                '時代': (self.band_labels[r['band']]
                         if 0 <= r['band'] < len(self.band_labels) else '不明'),
                '著者': r['author'], '作品': r['title'],
                '左文脈': left, 'キーワード': key, '右文脈': right,
                '位置': r['in_work'],
            })
        return pd.DataFrame(rows)


def to_csv(kw: KwicIndex, res: dict, path: str | os.PathLike) -> Path:
    """検索結果を CSV に書く（Excel で開けるよう BOM 付き）。"""
    p = Path(path)
    with open(p, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['# query', res['query'], 'stream', res['stream'],
                    'total', res['total'], 'shown', res['shown'],
                    'dictionary', res['provenance'].get('dictionary', ''),
                    'index_built', res['provenance'].get('built_at', '')])
        w.writerow(['時代区分', '著者', '作品', '初出年', '文体',
                    '左文脈', 'キーワード', '右文脈', '語彙素', '品詞',
                    '作品内位置', '作品の語幹'])
        for r in res['rows']:
            w.writerow([
                (kw.band_labels[r['band']]
                 if 0 <= r['band'] < len(kw.band_labels) else '初出年不明'),
                r['author'], r['title'], r['year'], r['style'],
                ''.join(t['surf'] for t in r['left']),
                ''.join(t['surf'] for t in r['key']),
                ''.join(t['surf'] for t in r['right']),
                ' '.join(t['lem'] for t in r['key']),
                ' '.join(t['pos'] for t in r['key']),
                r['in_work'], r['stem']])
    return p

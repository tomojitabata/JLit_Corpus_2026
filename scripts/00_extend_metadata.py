#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
00_extend_metadata.py
=====================
``metadata/corpus_metadata_v2.csv`` を，実際に取得できた作品ぜんぶに広げる。

なぜ要るのか
------------
``00_build_metadata_v2.py`` が作る表は **v1 の 64 点**を対象にしている。
増補後のコーパスは 108 点あるので，差分の 45 点にはメタデータの行がない。
行が無いと ``06_build_datasets.py`` の突合が外れ，チャンク索引の
``period`` も ``genre`` も空になる。その状態で先へ進むと，
時代別 keyness も doc2vec のカテゴリ効果もトピックの通時変化も，
**すべて空振りしたまま最後まで通ってしまう。**

何を典拠にするか
----------------
列によって典拠が違う。混ぜないために ``source`` 系の列に記録する。

============================  ==========================================
青空文庫の索引・図書カード    author_ja, title_aozora, ndc, year_first,
（``fetch_log.csv``）          kana_orthography, first_medium, note（底本）
増補候補表                    genre_main, genre_sub, author_sex,
（``expansion_candidates``）   style_class の**当初の見込み**
本文からの実測                tokens, types, ttr_x1000, kanji_ratio,
（``--tokens`` 指定時）        bungo_per10k, kogo_per10k, p1_per10k …
編者の判断                    narration, register_level, audience, form
（``--editorial``）            genre_main/sub, author_sex, completeness
============================  ==========================================

最後の列は**機械では決められない**。``--editorial`` を渡さなければ ``TBD``
を入れ，``needs_review`` 列に列名を並べる。増補分について空欄のまま分析に
入らないこと。

判断を書く場所について
----------------------
判断は ``metadata/editorial_expansion.csv`` に書く。**生成物である
v3 を直接手で直してはいけない**。このスクリプトは v3 を毎回 v2 と
``fetch_log.csv`` から作り直すので，v3 への手入れは次の実行で消える。
判断表は person_id と work_id で行を指定し，統制語彙（``VOCAB``）から
外れた値は読み込む側で弾く。どの行にも当たらなかった判断は警告に出る。

使い方
------
    python3 00_extend_metadata.py \\
        --meta metadata/corpus_metadata_v2.csv \\
        --fetch-log data/aozora/fetch_log.csv \\
        --candidates metadata/expansion_candidates.csv \\
        --tokens data/tokens/tokens_surface --remeasure-all \\
        --out metadata/corpus_metadata_v3.csv

``--editorial`` と ``--persons`` は省いてよい。``--meta`` の隣の
``editorial_expansion.csv`` と ``--fetch-log`` の隣の
``list_person_all_extended_utf8.csv`` を自動で使う。
**オプションを1つ落としただけで編者の判断が全部消えた v3 が
黙って出来上がる**ので，既定で拾うようにしてある。

``--tokens`` を省くと実測列は空になる（あとから埋められる）。
**``data/plain/full`` を渡してはいけない**（分かち書きされていない）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_spec_dir = os.path.dirname(os.path.abspath(__file__))
try:                                    # 00_ で始まるので import できない
    import importlib.util
    _s = importlib.util.spec_from_file_location(
        '_meta_v2', os.path.join(_spec_dir, '00_build_metadata_v2.py'))
    _m = importlib.util.module_from_spec(_s)
    _s.loader.exec_module(_m)
    period_of, NDC_LABEL, measure, style_class = (
        _m.period_of, _m.NDC_LABEL, _m.measure, _m.style_class)
    period5_of = _m.period5_of
    NotTokenised = _m.NotTokenised
except Exception as e:                                          # noqa: BLE001
    sys.exit(f'00_build_metadata_v2.py を読み込めない: {e}')

TBD = 'TBD'


def is_tbd(v) -> bool:
    """セルが TBD か。**値が文字列とは限らない**ので str() を通す。

    実測列には int / float が入る。``(v or '').strip()`` と書くと
    ``--tokens`` を付けたときだけ AttributeError で落ちる。
    """
    return isinstance(v, str) and v.strip() == TBD


JUDGEMENT_COLS = ('narration', 'register_level', 'audience', 'form')

#: ``--editorial`` の表で上書きしてよい列。実測列（tokens 以下）は
#: 手で書き換えられては困るので，ここには入れない。
EDITORIAL_COLS = (
    'narration', 'register_level', 'audience', 'form',
    'genre_main', 'genre_sub', 'author_sex', 'completeness',
    'year_first', 'year_source', 'year_first_end',
    # 青空文庫の索引の「初出」欄が空の作品は，媒体が機械では決まらない。
    'first_medium',
    # ``note`` だけは**置換**である。事情が変わって嘘になった注記
    # （「未収録。要再取得」など）は，追記では消せない。
    'note',
)

#: 統制語彙。既存 64 点で使われている値に，増補で新たに要ったものを足す。
#: 綴りが一文字違うだけで集計が割れるので，書いた側ではなく読み込む側で止める。
VOCAB = {
    'narration': {'first', 'third', 'mixed', 'none', 'dialogue'},
    'register_level': {'canonical', 'middlebrow', 'popular', 'documentary'},
    'audience': {'general', 'juvenile'},
    'form': {'novel', 'novella', 'short-story', 'collection', 'cycle',
             'treatise', 'essay', 'diary', 'diary-novel', 'biography',
             'autobiography',
             # 増補で新設。戯曲と紀行は既存 64 点に無かった。
             'play', 'travelogue',
             # 円朝の速記本。筆記された口演であって著述ではないので，
             # novel と混ぜると「小説の文体」の平均が動く。別に立てる。
             'oral-transcript'},
    'genre_main': {'Fiction', 'Nonfiction'},
    'author_sex': {'M', 'F'},
    'completeness': {'complete', 'incomplete', 'part', 'partial',
                     'revised', 'DUPLICATE',
                     # 増補で同じ作品を分冊ごとに取り直したため，v1 の
                     # 合本行が要らなくなった場合に使う。
                     'superseded',
                     # 1チャンクにも満たず，チャンク単位の分析に乗らない作品。
                     # 書誌としては残すが集計からは外す。
                     'too_short',
                     # 分冊。03b_merge_volumes.py で canonical の巻に
                     # 本文を統合したので，この行はもう本文を持たない。
                     'merged'},
}


def load_persons(path: str) -> dict:
    """青空文庫の人物データから 読み・生年・没年 を引く表を作る。

    典拠は ``02_fetch_aozora.py`` が展開する
    ``data/aozora/list_person_all_extended_utf8.csv``。
    増補 45 点は作品側の索引だけで組み立てているので，このまま放っておくと
    ``author_birth`` ``author_death`` が空のままになる。**作家の世代で
    層別した分析がそこだけ黙って落ちる**ので，同じ索引から埋めておく。

    生没年は ``1867-03-10`` や ``1867`` のように書かれている。年だけ採る。
    """
    table = {}
    for r in read_csv(path):
        pid = (r.get('人物ID') or '').strip()
        if not pid:
            continue
        pid = pid.zfill(6)
        if pid in table:
            continue
        sei = (r.get('姓読み') or '').strip()
        mei = (r.get('名読み') or '').strip()
        reading = ' '.join(x for x in (sei, mei) if x)

        def year(v):
            mm = re.match(r'\s*(\d{4})', v or '')
            return mm.group(1) if mm else ''

        table[pid] = {
            'author_reading': reading,
            'author_birth': year(r.get('生年月日')),
            'author_death': year(r.get('没年月日')),
        }
    return table


#: 初出誌が新聞であることの手掛かり。誌名に入っていれば新聞と見る。
_NEWSPAPER_RE = re.compile(r'新聞|日日|日々|朝日|読売|報知|時事|萬朝報|万朝報')
#: 雑誌の号表示。「第三巻第十一号」「十二月号」など。
_ISSUE_RE = re.compile(r'[号號]|附録')


def first_medium_of(shoshutsu: str) -> str:
    """青空文庫の「初出」欄の文字列から初出媒体を分類する。

    **この列に初出の文字列をそのまま入れてはいけない。** 1件1種類の
    値になってしまい，媒体で層別できなくなる（既存 64 点は
    magazine / newspaper / book の3値である）。生の文字列は
    ``shoshutsu`` 列に別に残す。

    判定できないときは空を返す。誤った媒体を入れるより空のほうがよい。
    """
    s = (shoshutsu or '').strip()
    if not s:
        return ''
    if _NEWSPAPER_RE.search(s):
        return 'newspaper'
    if _ISSUE_RE.search(s):
        return 'magazine'
    # 「…社、1913年6月14日」のように出版社と日付だけなら単行本とみる。
    if re.search(r'(書店|書院|書房|社|館|堂|刊)[、,]?\s*\d{4}', s):
        return 'book'
    if re.search(r'^「[^」]+」\s*\d{4}', s):        # 「誌名」＋年 → 雑誌
        return 'magazine'
    # 号表示がなく刊行日が1日に特定できるものは単行本。私家版
    # （『遠野物語』柳田國男、1910年6月14日）がこれに当たる。
    if re.search(r'\d{4}[^\d]*\d{1,2}月\d{1,2}日', s):
        return 'book'
    return ''


def load_editorial(path: str) -> tuple[dict, list[str]]:
    """編者の判断表を読む。

    戻り値は ``{(person_id, work_id): {列: 値}}`` と，統制語彙から外れた
    値の一覧。判断表は生成物ではなく**典拠**なので，v3 を作り直しても
    残るように別ファイルにしてある。v3 を直接手で直すと，次に
    このスクリプトを走らせた時点で消える。
    """
    table, bad = {}, []
    for r in read_csv(path):
        pid = (r.get('person_id') or '').strip()
        wid = (r.get('work_id') or '').strip()
        if not pid or not wid:
            continue
        vals = {}
        for k in EDITORIAL_COLS:
            v = (r.get(k) or '').strip()
            if not v:
                continue
            if k in VOCAB and v not in VOCAB[k]:
                bad.append(f'{pid}_{wid} {k}={v}')
                continue
            vals[k] = v
        note = (r.get('editorial_note') or '').strip()
        if note:
            vals['_note'] = note
        table[(pid.zfill(6), wid.zfill(6))] = vals
    return table, bad


def apply_editorial(row: dict, table: dict) -> bool:
    """1 行に編者の判断を当てる。当たったら True。"""
    key = (row.get('aozora_person_id', '').strip().zfill(6),
           row.get('aozora_work_id', '').strip().zfill(6))
    vals = table.get(key)
    if not vals:
        return False
    for k, v in vals.items():
        if k == '_note':
            continue
        row[k] = v
    note = vals.get('_note')
    if note:
        row['note'] = (row.get('note', '') + ' ／ ' + note).strip(' ／')
    row['source_meta'] = (row.get('source_meta', '') + '+editorial').lstrip('+')
    return True


def stem_of(person_id: str, work_id: str) -> str:
    return f'{person_id.strip().zfill(6)}_{work_id.strip().zfill(6)}'


def read_csv(path: str) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig') as fh:
        return list(csv.DictReader(fh))


def norm(s: str) -> str:
    """作者名・作品名の突合用に記号と空白を落とす。"""
    return re.sub(r'[\s　・･「」『』（）\(\)　]', '', (s or ''))


def base_title(s: str) -> str:
    """副題・分冊表示を落とした作品名を返す。

    v1 のメタデータは分冊をまとめて『夜明け前（第一部上〜第二部下）』と
    書いているが，青空文庫の索引では分冊ごとに『夜明け前』である。
    括弧以降を落とさないと同じ作品だと分からない。
    """
    t = re.split(r'[（(]', s or '', 1)[0]
    t = re.sub(r'[上中下前後]?巻?$', '', t.strip())
    return norm(t)


def clean_ndc(raw: str) -> tuple[str, str]:
    """青空文庫の索引の NDC 表記を正規化する。

    索引は ``NDC 913`` や ``NDC 121 210``（複数分類）のように書く。
    既存メタデータは ``913`` ``K913`` の形なので，**そのまま入れると
    書式が混在し，NDC での絞り込みも genre 判定も効かなくなる**。
    先頭の ``NDC`` を落とし，最初の分類を主として返す。
    戻り値は ``(主分類, 全分類を空白区切りにしたもの)``。
    """
    t = re.sub(r'^\s*NDC\s*', '', (raw or '').strip(), flags=re.I)
    codes = [c for c in re.split(r'[\s,、/]+', t) if c]
    return (codes[0] if codes else ''), ' '.join(codes)


def genre_from_ndc(ndc: str) -> tuple[str, str]:
    """NDC から genre_main を決め，genre_sub の当たりを付ける。

    判断できるのは大分類まで。sub は増補候補表があればそちらを優先する。
    """
    n = clean_ndc(ndc)[0].lstrip('K')
    # NDC の文学は 9□3 が各国文学の小説（913 日本，933 英米，983 ロシア…）。
    # 国ごとに列挙すると必ず抜けるので，桁の形で見る。
    if re.fullmatch(r'9\d3', n[:3] or ''):
        return 'Fiction', ''
    if n.startswith('912'):
        return 'Fiction', 'Drama'
    if n.startswith('911') or n.startswith('951'):
        return 'Nonfiction', 'Verse'
    if n.startswith('914') or n.startswith('915'):
        return 'Nonfiction', 'Essay'
    if n[:1] in ('1', '2', '3', '9') and not n.startswith('9'):
        return 'Nonfiction', ''
    return 'Nonfiction', '' if n else (TBD, '')


_DICT_CACHE: dict = {}


def token_dictionary(tokdir: str) -> str:
    """このトークン列がどの辞書で作られたかを返す（分からなければ空）。

    実測列（``bungo_per10k`` など）は**辞書に依存する**。辞書を替えて
    測り直した行と，前の辞書で測った行が同じ表に並ぶと，作品の違いと
    辞書の違いが見分けられなくなる。そこで ``measure_source`` に辞書名を
    書き込み，混在を ``[warn]`` で拾えるようにする。

    05_tokenise_unidic.py が ``data/tokens/tokenise_provenance.json`` に
    書いた記録を読む。古い 05 で作った列には記録が無い。
    """
    key = os.path.normpath(tokdir)
    if key in _DICT_CACHE:
        return _DICT_CACHE[key]
    p = os.path.join(os.path.dirname(key), 'tokenise_provenance.json')
    name = ''
    try:
        with open(p, encoding='utf-8') as fh:
            d = json.load(fh)
        name = str(d.get('dictionary') or '')
        ver = str(d.get('version') or '')
        if name and ver:
            name = f'{name}-{ver}'
    except Exception:                                           # noqa: BLE001
        name = ''
    _DICT_CACHE[key] = name
    return name


def remeasure(row: dict, stem: str, tokdir: str) -> bool:
    """トークン列から実測列を埋め直す。成功したら True。

    **分かち書きされたファイルを渡すこと。** 生テクストを渡すと
    段落数を語数として数えてしまい，TTR が 1000 近くになる。
    ``measure()`` が入力を検査して例外を投げる。
    """
    p = os.path.join(tokdir, stem + '.txt')
    if not os.path.exists(p):
        return False
    try:
        vals = measure(p)
    except NotTokenised as e:
        raise SystemExit(f'[ERR ] {e}')
    except Exception as e:                                      # noqa: BLE001
        print(f'  [warn] 実測できない {stem}: {e}')
        return False
    for k, v in vals.items():
        row[k] = v          # 既存行に無い列（bungo_ratio 等）も入れる
    row['style_class'] = style_class(float(vals.get('bungo_per10k') or 0),
                                     float(vals.get('kogo_per10k') or 0))
    src = 'rebuilt:' + os.path.basename(tokdir.rstrip('/'))
    dic = token_dictionary(tokdir)
    row['measure_source'] = f'{src}@{dic}' if dic else src
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--meta', required=True, help='既存の corpus_metadata_v2.csv')
    ap.add_argument('--fetch-log', required=True, help='02 が書いた fetch_log.csv')
    ap.add_argument('--candidates', default=None,
                    help='metadata/expansion_candidates.csv（編集方針の典拠）')
    ap.add_argument('--tokens', default=None,
                    help='data/tokens/tokens_surface。指定すると実測列を計算する。'
                         '**data/plain/full を渡してはいけない**'
                         '（分かち書きされていないので語数を数え損なう）')
    ap.add_argument('--no-editorial', action='store_true',
                    help='編者の判断表を当てずに，TBD のままの v3 を作る。'
                         '判断そのものを見直すときだけ使う')
    ap.add_argument('--persons', default=None,
                    help='data/aozora/list_person_all_extended_utf8.csv。'
                         '増補行の author_reading / author_birth / author_death を'
                         '埋める。省くと空のままになり，作家の世代で'
                         '層別した分析から増補分だけが落ちる')
    ap.add_argument('--editorial', default=None,
                    help='metadata/editorial_expansion.csv。'
                         'narration / register_level / audience / form など，'
                         '機械では決められない列を person_id・work_id で指定して'
                         '埋める。v3 を手で直すと次の実行で消えるので，'
                         '判断はこの表に書くこと')
    ap.add_argument('--remeasure-all', action='store_true',
                    help='既存 64 点も同じ方法で測り直す。v1 のテクストは'
                         '外字欠落・奥付混入があるので，本来はこちらが正しい')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    base = read_csv(args.meta)
    if not base:
        sys.exit(f'既存メタデータが読めない: {args.meta}')

    # 実測列は辞書に依存する。**どの辞書で測るのかを最初に出す。**
    if args.tokens:
        dic = token_dictionary(args.tokens)
        if dic:
            print(f'[dic ] 実測に使うトークン列の辞書: {dic}')
        else:
            print('[warn] トークン列に辞書の記録が無い'
                  '（tokenise_provenance.json）。'
                  '\n       古い 05 で作った列である。'
                  '**どの辞書で測ったか表に残らない。**'
                  '\n       05_tokenise_unidic.py を今の版で回し直すこと。')

    # 判断表と人物データは，**指定を忘れても勝手に見つける**。
    # v3 は毎回作り直す生成物なので，オプションを1つ落としただけで
    # 編者の判断が全部消え，TBD が 160 セル復活した v3 が黙って出来る。
    # 既定の場所にあるなら使うのが正しく，使わないときは明示させる。
    if not args.editorial and not args.no_editorial:
        guess = os.path.join(os.path.dirname(args.meta) or '.',
                             'editorial_expansion.csv')
        if os.path.exists(guess):
            args.editorial = guess
            print(f'[auto] 編者の判断表を自動で使う: {guess}')
            print('       当てたくないときは --no-editorial を付ける')
    if args.no_editorial:
        args.editorial = None
        print('[warn] --no-editorial: 編者の判断を当てない。'
              'TBD の残った v3 で分析に進まないこと')
    if not args.persons:
        guess = os.path.join(os.path.dirname(args.fetch_log) or '.',
                             'list_person_all_extended_utf8.csv')
        if os.path.exists(guess):
            args.persons = guess
            print(f'[auto] 人物データを自動で使う: {guess}')

    editorial, bad_vocab = ({}, [])
    if args.editorial:
        if not os.path.exists(args.editorial):
            sys.exit(f'編者の判断表が読めない: {args.editorial}')
        editorial, bad_vocab = load_editorial(args.editorial)
        if not editorial:
            sys.exit(f'編者の判断表に person_id/work_id のある行が無い: '
                     f'{args.editorial}')
    ed_used = set()

    persons = {}
    if args.persons:
        persons = load_persons(args.persons)
        if not persons:
            sys.exit(f'人物データが読めない（人物ID 列が無い）: {args.persons}')
    # 既存 64 点は v1 で生没年を持っているので，人物データが無いときの
    # 次善の典拠にする。同じ作者が両方に出てくる場合が多い。
    from_core = {}
    for r in base:
        pid = (r.get('aozora_person_id') or '').strip().zfill(6)
        if pid and r.get('author_birth'):
            from_core.setdefault(pid, {
                'author_reading': r.get('author_reading', ''),
                'author_birth': r.get('author_birth', ''),
                'author_death': r.get('author_death', ''),
            })
    person_filled = person_missing = 0
    cols = list(base[0].keys())
    for extra in ('set', 'source_meta', 'measure_source', 'bungo_ratio',
                  'ndc_all', 'shoshutsu', 'needs_review', 'period5'):
        if extra not in cols:
            cols.append(extra)

    have = set()
    for r in base:
        pid, wid = r.get('aozora_person_id', ''), r.get('aozora_work_id', '')
        if pid and wid:
            have.add(stem_of(pid, wid))

    # 同一作品の別冊（『夜明け前』第一部/第二部，『家』上/下 など）は，
    # 既存行から初出年・ジャンル・語りの視点を引き継げる。同じ作品なのだから
    # 別々に判断する必要はない。底本年で代用するより確実である。
    sibling = {}
    for r in base:
        au = norm(r.get('author_ja'))
        for t in (norm(r.get('title_aozora')), base_title(r.get('title_aozora'))):
            if t:
                sibling.setdefault((au, t), r)

    cand = read_csv(args.candidates) if args.candidates else []
    cand_lut = {}
    for c in cand:
        au_c = norm(c.get('author_ja'))
        for t in (norm(c.get('title')), base_title(c.get('title'))):
            if t:
                cand_lut.setdefault((au_c, t), c)

    log = read_csv(args.fetch_log)
    if not log:
        sys.exit(f'fetch_log が読めない: {args.fetch_log}')

    added, review, no_year = [], [], []
    next_id = max((int(r['id'][4:]) for r in base
                   if r.get('id', '').startswith('JLIT')), default=0)

    for r in log:
        pid = r.get('person_id', '')
        wid = r.get('work_id', '')
        if not pid or not wid:
            continue
        stem = stem_of(pid, wid)
        if stem in have:
            continue
        have.add(stem)
        next_id += 1

        ndc, ndc_all = clean_ndc(r.get('ndc'))
        gmain, gsub = genre_from_ndc(ndc)
        au0 = norm(r.get('author_ja'))
        c = (cand_lut.get((au0, norm(r.get('title_aozora'))))
             or cand_lut.get((au0, base_title(r.get('title_aozora')))) or {})
        if c:
            gmain = c.get('genre_main') or gmain
            gsub = c.get('genre_sub') or gsub

        # 初出年。青空文庫の索引は 45 件中 22 件で「初出」欄が空である。
        # 底本年（現代の全集の刊年）で代用してはいけない——1969年刊の
        # 『夜明け前』を昭和戦後に分類してしまう。増補候補表の年次を
        # 次善の典拠とし，それも無ければ TBD にして人に回す。
        year, ysrc = 0, ''
        try:
            year = int((r.get('year_first') or '0')[:4])
            ysrc = 'aozora_index'
        except ValueError:
            year = 0
        if not year and c.get('year_target'):
            try:
                year = int(str(c['year_target'])[:4])
                ysrc = 'expansion_candidates'
            except ValueError:
                year = 0
        au = norm(r.get('author_ja'))
        sib = (sibling.get((au, norm(r.get('title_aozora'))))
               or sibling.get((au, base_title(r.get('title_aozora')))))
        if not year and sib and sib.get('year_first'):
            try:
                year = int(str(sib['year_first'])[:4])
                ysrc = 'sibling:' + sib.get('id', '')
            except ValueError:
                year = 0
        if not year:
            no_year.append((f'JLIT{next_id:03d}', r.get('author_ja', ''),
                            r.get('title_aozora', ''), r.get('card_url', '')))

        row = {k: '' for k in cols}
        row.update({
            'id': f'JLIT{next_id:03d}',
            'file_v1': '',                       # v1 に無い作品
            'author_ja': r.get('author_ja', ''),
            'author_sex': c.get('author_sex', '') or TBD,
            'title_aozora': r.get('title_aozora', ''),
            'aozora_person_id': pid.zfill(6),
            'aozora_work_id': wid.zfill(6),
            'aozora_card_url': r.get('card_url', ''),
            'year_first': str(year) if year else '',
            'year_source': ysrc or TBD,
            'period': period_of(year) if year else TBD,
            'ndc': ndc,
            'ndc_label': NDC_LABEL.get(ndc.lstrip('K'), ''),
            'ndc_all': ndc_all if ' ' in ndc_all else '',
            'genre_main': gmain,
            'genre_sub': gsub or TBD,
            # 初出の**文字列**ではなく**媒体の種別**を入れる。生の
            # 文字列は shoshutsu 列に残す（典拠として要る）。
            'first_medium': first_medium_of(r.get('shoshutsu', '')),
            'shoshutsu': (r.get('shoshutsu', '') or '').replace('<br>', ' '),
            'kana_orthography': r.get('kana_orth', ''),
            'completeness': 'complete',
            'note': (f"底本: {r.get('teihon', '')}" if r.get('teihon') else ''),
            'set': 'expansion',
            'source_meta': 'aozora_index' + ('+candidates' if c else ''),
        })
        for k in JUDGEMENT_COLS:
            if k in row:
                row[k] = TBD
        # 別冊があるなら，判断の要る列もそこから引き継ぐ
        if sib:
            for k in JUDGEMENT_COLS + ('genre_sub', 'author_sex', 'register_level',
                                       'completeness',
                                       # 初出誌と連載終了年も作品の属性であって
                                       # 分冊ごとに違うものではない。索引の
                                       # 「初出」欄が空の分冊はここで埋まる。
                                       'first_medium', 'year_first_end'):
                if k in row and sib.get(k) and row.get(k) in ('', TBD):
                    row[k] = sib[k]
            row['source_meta'] = (row['source_meta'] + '+sibling:'
                                  + sib.get('id', ''))
            row['completeness'] = 'part'      # 分冊であることを明示する

        pid6 = (row.get('aozora_person_id') or '').strip().zfill(6)
        who = persons.get(pid6) or from_core.get(pid6)
        if who:
            for k, v in who.items():
                if v and not row.get(k):
                    row[k] = v
            person_filled += 1
        else:
            person_missing += 1

        # 編者の判断は継承より後に当てる。別冊から引き継いだ値も，
        # 判断表に書いてあればそちらが勝つ。
        if editorial and apply_editorial(row, editorial):
            ed_used.add((row.get('aozora_person_id', '').zfill(6),
                         row.get('aozora_work_id', '').zfill(6)))
            if row.get('year_first') and row.get('period') in ('', TBD):
                row['period'] = period_of(row['year_first'])

        if args.tokens:
            if remeasure(row, stem, args.tokens):
                pass
        if not row.get('style_class'):
            row['style_class'] = c.get('style_expect', '') or TBD

        need = [k for k in cols if row.get(k) == TBD]
        if not row.get('measure_source'):
            need.append('実測列（--tokens を付けて測り直すこと）')
        row['needs_review'] = ' '.join(need)
        added.append(row)
        if need:
            review.append((row['id'], row['author_ja'], row['title_aozora'], need))

    remeasured = 0
    for r in base:
        r.setdefault('set', 'core')
        r.setdefault('source_meta', 'aozora_card')
        r.setdefault('needs_review', '')
        if not r.get('measure_source'):
            r['measure_source'] = 'v1_wakachi'
        # 既存 64 点にも判断表を当てられるようにしておく。completeness の
        # DUPLICATE / PARTIAL のように，増補で事情が変わった行を直すため。
        if editorial and apply_editorial(r, editorial):
            ed_used.add((r.get('aozora_person_id', '').zfill(6),
                         r.get('aozora_work_id', '').zfill(6)))
        if args.remeasure_all and args.tokens:
            pid, wid = r.get('aozora_person_id', ''), r.get('aozora_work_id', '')
            if pid and wid and remeasure(r, stem_of(pid, wid), args.tokens):
                remeasured += 1

    out_rows = base + added

    # ---- ID の綴りを揃える（**事故の根を断つ**）----------------------------
    # v1 由来の行は作品 ID が 0 埋めされていない（1743）のに，増補した行は
    # 0 埋めされている（001504）という混在状態にあった。両者が1つの表に
    # 並ぶと，`f'{pid}_{wid}'` と素朴に鍵を作った工程だけが**静かに**
    # 突合に失敗する。2026-09-22 に 07_descriptive_stats.py がこれで
    # 62/101 点を落とし，PCA の図に「初出年不明 62 件」と出た。
    #
    # 読む側で 0 埋めして照合するのが基本だが，**書く側でも揃えておく**。
    # ファイル名は6桁 0 埋めなので，表もそれに合わせる。
    padded = 0
    for r in out_rows:
        for col in ('aozora_person_id', 'aozora_work_id'):
            v = str(r.get(col) or '').strip()
            if v and v.lower() != 'nan' and v.isdigit() and len(v) < 6:
                r[col] = v.zfill(6)
                padded += 1
    if padded:
        print(f'[fix ] 作品・人物 ID を6桁に揃えた（{padded} セル）。'
              'ファイル名の綴りと一致させるため。')

    # 学習に使う5区分。**period から必ず引き直す**（手で書いた値は信用しない）。
    # 6区分と5区分が食い違った表を配ると，Step 4 の図と Step 6 のモデルが
    # 別の母集団を指すことになる。導出の規則は 00_build_metadata_v2.BAND5。
    for r in out_rows:
        r['period5'] = period5_of(str(r.get('period') or ''))

    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for r in out_rows:
            w.writerow({k: r.get(k, '') for k in cols})

    print(f'[ok  ] 既存 {len(base)} 行 ＋ 追補 {len(added)} 行 = {len(out_rows)} 行')
    if args.tokens:
        n_new = sum(1 for r in added if r.get('measure_source', '').startswith('rebuilt'))
        print(f'[ok  ] 実測 追補 {n_new} 行' +
              (f' ／ 既存 {remeasured} 行を測り直し' if args.remeasure_all else ''))
    mixed = {r.get('measure_source', '') for r in out_rows}
    if len({m for m in mixed if m}) > 1:
        stale = [r for r in out_rows
                 if r.get('measure_source', '') == 'v1_wakachi']
        print('\n[warn] 実測の典拠が混在している: '
              + '，'.join(sorted(m for m in mixed if m)))
        print('       v1 のテクストは外字欠落・奥付混入があるので，'
              '再構築後のトークン列で測った値と直接は比べられない。')
        # 辞書が混ざっている場合は，v1/v2 の混在より見つけにくい。
        # measure_source の @ の右側（辞書名）が2種類あれば名指しで出す。
        dics = {m.split('@', 1)[1] for m in mixed if '@' in m}
        if len(dics) > 1:
            print('       **さらに，辞書が混在している: '
                  + '，'.join(sorted(dics)) + '**')
            print('       bungo_per10k / kogo_per10k / ttr_x1000 / '
                  'style_class は辞書に依存する。')
            print('       同じ表に別の辞書で測った行を並べると，作品の違いと'
                  '辞書の違いが見分けられない。')
            print('       05 を本番の辞書で回し直し，--remeasure-all で'
                  '全行を測り直すこと。')
        if not args.remeasure_all:
            print('       --remeasure-all を付けて全行を同じ方法で測り直すこと。')
        else:
            # --remeasure-all を付けたのに残っているのは，本文が無い行である。
            # ここで「--remeasure-all を付けろ」と言うと，付けた人を混乱させる。
            print(f'       --remeasure-all は効いている。残る {len(stale)} 行は'
                  '**本文ファイルを持たない行**で，測り直しようがない:')
            for r in stale[:6]:
                print(f'         {r.get("id", "?")} {r.get("author_ja", "")}'
                      f'『{r.get("title_aozora", "")}』'
                      f'（completeness={r.get("completeness", "")}）')
            if len(stale) > 6:
                print(f'         …ほか {len(stale) - 6} 行')
            print('       これらは集計から外れる行なので，実測値は参考値である。')
    # 統合したはずの分冊に本文があるなら，03b を走らせる前に 04/05 を
    # 走らせている。メタデータ上は merged でも，トークン列は巻ごとに
    # 残っているので，**実測値が作品のものではなく巻のもの**になる。
    if args.tokens:
        odd = [r for r in out_rows
               if (r.get('completeness') or '') in ('merged', 'superseded')
               and str(r.get('measure_source', '')).startswith('rebuilt')]
        if odd:
            print(f'\n[warn] **本文を持たないはずの行 {len(odd)} 件に実測値がある**')
            for r in odd[:6]:
                print(f'         {r.get("id", "?")} {r.get("author_ja", "")}'
                      f'『{r.get("title_aozora", "")}』'
                      f'（completeness={r.get("completeness")}）')
            print('       03b_merge_volumes.py を走らせる前に 04/05 を'
                  '走らせた可能性が高い。')
            print('       その場合トークン列は巻ごとのままなので，'
                  '**03b → 04 → 05 の順で走らせ直すこと。**')
            print('       これらの行は集計から外れるので結果は狂わないが，'
                  '実測列は作品のものではない。')

    if person_filled or person_missing:
        print(f'[ok  ] 作者の読み・生没年 {person_filled} 行を埋めた'
              + (f'（人物データ {len(persons)} 人）' if persons else '（既存行から継承）'))
    if person_missing:
        print(f'[warn] 作者の生没年が引けない行が {person_missing} 件ある。'
              '--persons に list_person_all_extended_utf8.csv を渡すこと')

    if args.editorial:
        print(f'[ok  ] 編者の判断表 {len(editorial)} 行のうち '
              f'{len(ed_used)} 行を当てた（{args.editorial}）')
        stale = sorted(set(editorial) - ed_used)
        if stale:
            print(f'[warn] **どの行にも当たらなかった判断が {len(stale)} 件ある**')
            print('       person_id / work_id の綴りが違うか，その作品が'
                  'コーパスに入っていない。黙って無視すると，')
            print('       判断したつもりの列が TBD のまま残る。')
            for pid, wid in stale[:10]:
                print(f'         {pid}_{wid}')
            if len(stale) > 10:
                print(f'         …ほか {len(stale) - 10} 件')
        if bad_vocab:
            print(f'[warn] **統制語彙から外れた値が {len(bad_vocab)} 件ある'
                  '（無視した）**')
            for b in bad_vocab[:10]:
                print(f'         {b}')
            print('       綴りを直すか，新しい値なら VOCAB に足すこと。')

    # **値が文字列とは限らない。** remeasure() は tokens や ttr_x1000 に
    # int / float をそのまま入れるので，``(v or '').strip()`` は
    # AttributeError で落ちる（--tokens を付けたときだけ落ちるので，
    # 付けずに試していると気づかない）。str() を通してから比べる。
    left = sum(1 for r in out_rows
               for v in r.values() if is_tbd(v))
    if left:
        from collections import Counter as _C
        where = _C(k for r in out_rows for k, v in r.items() if is_tbd(v))
        judged = sum(c for k, c in where.items()
                     if k in EDITORIAL_COLS or k in JUDGEMENT_COLS)
        measured = left - judged
        print(f'[FATAL] TBD が {left} セル残っている: '
              + '，'.join(f'{k}×{c}' for k, c in where.most_common()))
        if judged:
            print(f'        うち {judged} セルは**編者の判断**の列である。'
                  '判断表に行が無いか，鍵が合っていない。')
            print('        metadata/editorial_expansion.csv に'
                  ' person_id / work_id で行を足すこと。')
        if measured:
            print(f'        うち {measured} セルは**実測から決まる**列'
                  '（style_class など）である。')
            print('        --tokens data/tokens/tokens_surface を付けて'
                  '測り直せば埋まる。')
        print('        TBD のまま keyness や doc2vec に進むと，'
              'そのカテゴリの比較が無意味になる。')
    else:
        print('[ok  ] TBD は残っていない')
    print(f'[ok  ] → {args.out}')

    if review:
        dest = os.path.splitext(args.out)[0] + '_needs_review.csv'
        with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow(['id', 'author_ja', 'title_aozora', 'needs_review'])
            for i, a, t, n in review:
                w.writerow([i, a, t, ' '.join(n)])
        print(f'\n[note] 編者の判断が要る行が {len(review)} 件ある → {dest}')
        print('       機械では決められない列なので，必ず人が埋めること:')
        from collections import Counter
        cnt = Counter(k for _, _, _, n in review for k in n)
        for k, v in cnt.most_common():
            print(f'         {k:<18} {v:>3} 行')
        print('\n       narration（語りの視点）は本文を読まないと決まらない。')
        print('       register_level（正典/中間/大衆/記録）も同様である。')
        print('       TBD のまま doc2vec や keyness に進むと，'
              'そのカテゴリの比較が無意味になる。')

    if no_year:
        dest = os.path.splitext(args.out)[0] + '_no_year.csv'
        with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow(['id', 'author_ja', 'title_aozora', 'card_url'])
            w.writerows(no_year)
        print(f'\n[note] **初出年が分からない作品が {len(no_year)} 件ある** → {dest}')
        print('       青空文庫の索引の「初出」欄が空で，増補候補表にも年次がない。')
        print('       底本年（現代の全集の刊年）で代用してはいけない。'
              '1969年刊の『夜明け前』を')
        print('       昭和戦後に分類することになり，時代区分が壊れる。')
        print('       図書カード（上の URL）か，作品の書誌で確認して'
              'year_first を埋めること。')
        for i, a_, t, u in no_year[:6]:
            print(f'         {i}  {a_} 『{t}』  {u}')
        if len(no_year) > 6:
            print(f'         …ほか {len(no_year) - 6} 件')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

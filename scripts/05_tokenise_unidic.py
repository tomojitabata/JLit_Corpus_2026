#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05_tokenise_unidic.py
=====================
UniDic 短単位による形態素解析。CoNLL 風の TSV と，用途別のトークン列を出力する。

UniDic の語形をめぐる注意（重要）
--------------------------------
UniDic の素性には似た語形が何種類もあり，どれを分析単位にするかで結果が変わる。

======================  ================================================
素性                    例：「噓を」「渋江」「云つた」
======================  ================================================
``surface``（表層形）   噓 / 渋江 / 云つ
``orth``（書字形）      噓 / 渋江 / 云つ
``orthBase``            噓 / 渋江 / 云う      ← 活用は基本形に，表記は保持
``lemma``（語彙素）     嘘 / **シブエ** / 言う ← 表記も統一されるが固有名詞は片仮名
``lForm``（語彙素読み） ウソ / シブエ / イウ
======================  ================================================

``lemma`` は異表記（噓／嘘，云う／言う）を統合するので通時比較に適する一方，
**固有名詞では片仮名の読みになる**という UniDic の仕様がある。本スクリプトは

    固有名詞 → orthBase（なければ表層形）
    それ以外 → lemma（なければ orthBase → 表層形）

という規則で ``lemma_key`` を作る。この規則は ``--lemma-policy`` で切り替えられ，
どれを使ったかは出力の先頭行に記録される。v1 コーパスは
「どの辞書のどの語形か」が記録されておらず，同一作品の2ファイルで
``Ｂ ・ Ｄ`` と ``Ｂ・Ｄ`` のように分割が食い違っていた。同じ轍は踏まない。

出力
----
``tsv/<id>.tsv``          1行1形態素。surface, lemma_key, pos1..4, cType, cForm, goshu ほか
``tokens_lemma/<id>.txt`` 語彙素キーの空白区切り列（トピックモデル・word embedding 用）
``tokens_surface/<id>.txt`` 表層形の空白区切り列（文体計量用）
``tokens_content/<id>.txt`` 内容語（名詞・動詞・形容詞・副詞）の語彙素のみ

本番の辞書（2026-09-22 決定）
----------------------------
**近現代口語小説UniDic（``unidic-novel`` v202512）** を本番の辞書とする。
4 辞書を 111 点で比べた結果（``docs/dictionary_comparison.md`` §10）:

======================  ==========  ==========  ==========
辞書                    未知語率    平均語長    助動詞率
======================  ==========  ==========  ==========
**novel**（本番）       **0.17%**   1.575       10.9%
qkana                   0.19%       1.577       10.9%
kindai（検算用）        0.25%       1.582       **11.2%**
cwj（現代書き言葉）     0.66%       1.560       10.6%
======================  ==========  ==========  ==========

novel は cwj に対して未知語率・平均語長・助動詞率の3点すべてで優る。
「細かく分割して未知語を減らした」のではないことを確かめてある。

使い方
------
    python3 05_tokenise_unidic.py --in data/plain/full --out data/tokens \\
        --dicdir /Users/Shared/jlit/unidic-novel-v202512 --lemma-policy mixed

辞書の探索順
------------
``--dicdir`` > 環境変数 ``JLIT_UNIDIC_DIR`` >
``/Users/Shared/jlit/unidic-novel-v202512``（DH Lab の共有辞書・本番）>
``…/unidic-novel`` > ``…/unidic``（旧・cwj）> ``unidic`` パッケージ >
``unidic_lite``

**本番以外の辞書に切り替わったときは ``[warn]`` を出す。** 辞書が違えば語数も
未知語率も語彙素も変わるので，気づかずに混ぜると比較が成り立たない。
``--expect-dict unidic-novel`` を付けると，本番以外なら止まる。

何を記録するか
--------------
v1 コーパスの再現性の欠如は「どの辞書のどの語形で数えたか」が
どこにも残っていなかったことに由来する。そこで本スクリプトは

* 各 TSV の先頭行に，解決した**辞書のパスと名前と版**を書く
  （``dicdir=auto`` とだけ書くのでは記録にならない）
* ``<out>/tokenise_provenance.json`` に辞書・語形方針・素性数・
  fugashi の版・ファイル数・延べ語数・未知語数を書く

``06_build_datasets.py`` はこの JSON を読み，``config/pipeline.yaml`` の
``tokenise.dictionary`` と食い違えば止まる。

依存
----
    pip install fugashi
    # 辞書（本番）: NINJAL から入手して展開する。zip で約 1.7 GB
    #   https://clrd.ninjal.ac.jp/unidic_archive/2512/unidic-novel-v202512.zip
    #   展開したら必ず検査する:
    #     python3 scripts/check_unidic_dir.py /Users/Shared/jlit/unidic-novel-v202512
    # 代替（辞書が用意できないとき。未知語率が上がるので報告に明記する）
    pip install unidic-lite
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import sys
from collections import Counter

try:
    import fugashi
except ImportError:                                            # pragma: no cover
    sys.exit("fugashi が必要である:  pip install fugashi")

try:                                    # fugashi は版により __version__ を持たない
    from importlib.metadata import version as _pkg_version
    FUGASHI_VERSION = _pkg_version('fugashi')
except Exception:                                              # noqa: BLE001
    FUGASHI_VERSION = 'unknown'

CONTENT_POS = {'名詞', '動詞', '形容詞', '副詞'}
SKIP_POS = {'補助記号', '空白'}


# ---------------------------------------------------------------------------
# 本番の辞書。2026-09-22 の比較実験で決めた（docs/dictionary_comparison.md §10）
# ---------------------------------------------------------------------------
PROD_DICT = 'unidic-novel'
PROD_VERSION = 'v202512'
PROD_DIRNAME = f'{PROD_DICT}-{PROD_VERSION}'
PROD_URL = f'https://clrd.ninjal.ac.jp/unidic_archive/2512/{PROD_DIRNAME}.zip'

# 辞書の名前を basename から推定するための表。長いものを先に照合する
# （``unidic-kindai-bungo`` が ``unidic-kindai`` と誤って照合されないように）。
DICT_NAMES = ('unidic-kindai-bungo', 'unidic-kinsei-kamigata',
              'unidic-kinsei-edo', 'unidic-kinsei-bungo',
              'unidic-chusei-kougo', 'unidic-chusei-bungo',
              'unidic-D-kansai', 'unidic-novel', 'unidic-qkana',
              'unidic-chuko', 'unidic-jodai', 'unidic-waka',
              'unidic-cwj', 'unidic-csj', 'unidic-lite', 'unidic')

VERSION_RE = __import__('re').compile(r'-(v?\d[\d.]*)$')


def shared_root() -> str:
    """このマシンの共有場所。00_bootstrap_mac.sh が作る。"""
    # 共用 iMac は /Users/Shared/jlit，自分の Mac（--personal）は ~/.jlit
    if os.environ.get('JLIT_SHARED'):
        return os.environ['JLIT_SHARED']
    if sys.platform != 'darwin':
        return ''
    for d in ('/Users/Shared/jlit', os.path.expanduser('~/.jlit')):
        if os.path.isdir(d):
            return d
    return '/Users/Shared/jlit'


def identify_dict(path: str) -> tuple[str, str]:
    """辞書のディレクトリ名から「名前」と「版」を推定する。

    パス名だけが手がかりなので確実ではない。**確実にしたいなら
    ``--dicdir`` を明示すること。** それでも，記録が何も無いより良い。
    ``/Users/Shared/jlit/unidic-novel-v202512`` → (unidic-novel, v202512)
    """
    p = os.path.normpath(path)
    base = os.path.basename(p)
    if base == 'dicdir':
        # pip の unidic / unidic_lite は <パッケージ>/dicdir に置く。
        # 「dicdir」という辞書名を記録しても何の記録にもならないので，
        # 一段上（パッケージ名）を見る。
        base = os.path.basename(os.path.dirname(p)).replace('_', '-')
    ver = ''
    m = VERSION_RE.search(base)
    if m:
        ver, base_nover = m.group(1), base[:m.start()]
    else:
        base_nover = base
    for name in DICT_NAMES:
        if base_nover == name or base_nover.startswith(name + '-'):
            return name, ver
    return base_nover or '不明', ver


def dict_fingerprint(path: str) -> str:
    """同名の別物を区別するための指紋。``sys.dic`` の大きさを使う。

    ハッシュは辞書全体を読むので遅い。大きさだけでも「別の版に替わった」
    ことは検出できる。
    """
    p = os.path.join(path, 'sys.dic')
    try:
        return f'sys.dic:{os.path.getsize(p)}'
    except OSError:
        return ''


def resolve_dicdir(dicdir: str | None) -> tuple[str, str]:
    """使う辞書の場所と，どこから見つけたかを返す。

    DH Lab の iMac はホームがマシンごとに別々（共有されない）ので，UniDic（zip で約 1.7 GB）を
    各ユーザの仮想環境に入れると，人数 × マシン数だけ複製ができる。
    そこで **マシン内で共有できる** ``/Users/Shared/jlit`` を先に見に行く。
    admin 権限は要らない。

    優先順位: 明示指定 > 環境変数 ``JLIT_UNIDIC_DIR`` >
    共有の ``unidic-novel-v202512``（本番）> 共有の ``unidic-novel`` >
    共有の ``unidic``（旧・cwj）> ``unidic`` パッケージ > ``unidic_lite``

    ``unidic`` を最後に回してあるのは意図である。2026-09-22 の判定で
    現代書き言葉辞書（cwj）は落選した（未知語率が novel の 3.9 倍）。
    自動検出で cwj に切り替わると，気づかないまま別の辞書で数えた列が
    混ざる。**切り替わったことが分かるように，呼び出し側で警告を出す。**
    """
    if dicdir:
        return os.path.expanduser(dicdir), '--dicdir'
    env = os.environ.get('JLIT_UNIDIC_DIR')
    if env and os.path.isdir(os.path.expanduser(env)):
        return os.path.expanduser(env), '環境変数 JLIT_UNIDIC_DIR'
    root = shared_root()
    if root:
        for sub, label in ((PROD_DIRNAME, '共有辞書（本番）'),
                           (PROD_DICT, '共有辞書'),
                           ('unidic', '共有辞書（旧・cwj）')):
            p = os.path.join(root, sub)
            if os.path.isdir(p):
                return p, label
    for mod in ('unidic', 'unidic_lite'):
        try:
            m = __import__(mod)
            if os.path.isdir(m.DICDIR):
                return m.DICDIR, f'{mod} パッケージ'
        except Exception:                                      # noqa: BLE001
            continue
    return '', '既定（辞書未検出）'


def build_tagger(dicdir: str | None, expect: str | None = None):
    """タガーと，辞書の素性を返す。

    返り値の 2 番目は provenance に書く辞書の情報である。
    """
    path, src = resolve_dicdir(dicdir)
    if not path:
        print(f'[warn] UniDic が見つからない。fugashi 既定の辞書で解析する。'
              f'\n       未知語率が高く出る。本番の辞書は {PROD_DIRNAME}:'
              f'\n         {PROD_URL}'
              f'\n       docs/00_setup_students.md §4.3 を見ること。')
        if expect:
            sys.exit(f'[FATAL] --expect-dict {expect} を指定したが辞書が無い。')
        return fugashi.Tagger(), {'dictionary': '不明', 'version': '',
                                  'dicdir': '', 'source': src,
                                  'fingerprint': ''}

    name, ver = identify_dict(path)
    info = {'dictionary': name, 'version': ver, 'dicdir': path,
            'source': src, 'fingerprint': dict_fingerprint(path)}
    label = f'{name} {ver}'.strip()
    print(f'[dic ] {label}  ←  {path}  （{src}）')

    if expect and name != expect:
        sys.exit(f'[FATAL] 期待した辞書と違う。\n'
                 f'        期待: {expect}\n        実際: {name}（{path}）\n'
                 f'        --dicdir で明示するか，環境変数 JLIT_UNIDIC_DIR を'
                 f'直すこと。')
    if name != PROD_DICT:
        print(f'[warn] **本番の辞書ではない。** 本番は {PROD_DICT} '
              f'{PROD_VERSION}（2026-09-22 決定）。'
              f'\n       いま使うのは {label or name} である。'
              f'\n       辞書が違えば語数・未知語率・語彙素が変わるので，'
              f'別の辞書で作った列と**混ぜてはいけない**。'
              f'\n       意図した比較なら --out を辞書ごとに分けること'
              f'（例 data/dict_runs/{name}/）。'
              f'\n       本番の辞書を入れるには docs/00_setup_students.md '
              f'§4.3，または:\n         {PROD_URL}')
    elif ver and ver != PROD_VERSION:
        print(f'[warn] 辞書の版が違う（期待 {PROD_VERSION} / 実際 {ver}）。'
              '報告に版を明記すること。')
    return fugashi.Tagger(f'-d {path}'), info


PROBE = '國語の研究をしたり。彼は笑つて云つた。'


def probe_features(tagger, policy: str, allow_mismatch: bool) -> int:
    """辞書の素性の並びを確かめる。**解析を始める前に。**

    古文・近代語の UniDic は現代語版と素性の数が違い（17 / 26 / 29），
    ``orthBase`` や ``lemma`` が無いことがある。無い素性を当てにすると
    ``lemma_key`` が**黙って表層形に切り替わる**。「語彙素で数えたつもりが
    表層形だった」という事故は，出力を見ても分からない。
    """
    f = list(tagger(PROBE))[0].feature
    d = getattr(f, '_asdict', None)
    n = len(d()) if d else -1
    need = {'mixed': ('lemma', 'orthBase', 'pos1', 'pos2'),
            'lemma': ('lemma',), 'orthBase': ('orthBase',),
            'surface': ()}[policy]
    miss = [k for k in need if not hasattr(f, k)]
    print(f'[dic ] 素性 {n if n > 0 else "不明"} 個。'
          f'--lemma-policy {policy} が必要とする素性: '
          f'{"，".join(need) if need else "なし"}')
    if miss:
        msg = (f'この辞書には {"，".join(miss)} が無い。'
               f'--lemma-policy {policy} は黙って表層形に切り替わる。\n'
               f'        --lemma-policy surface を明示して意図を記録するか，'
               f'別の辞書を使うこと。\n'
               f'        どうしても続けるなら --allow-feature-mismatch。')
        if allow_mismatch:
            print(f'[warn] {msg}')
        else:
            sys.exit(f'[FATAL] {msg}')
    return n


def feat(f, name: str) -> str:
    v = getattr(f, name, None)
    return '' if v in (None, '*') else str(v)


HOMOGRAPH_RE = __import__('re').compile(r'-[ぁ-んァ-ヶ一-龯]+$')


def lemma_key(w, policy: str = 'mixed', strip_homograph: bool = True) -> str:
    """分析単位となる語形を決める。

    UniDic の語彙素は同形異義を ``私-代名詞`` ``行く-行く`` のように接尾辞で
    区別する。トピックモデルの可読性のため既定では接尾辞を除くが，
    厳密な語彙素同定が必要なときは ``--keep-homograph-suffix`` を使う。
    """
    f = w.feature
    surface = w.surface
    lemma = feat(f, 'lemma')
    orth_base = feat(f, 'orthBase')
    pos1, pos2 = feat(f, 'pos1'), feat(f, 'pos2')
    if policy == 'surface':
        return surface
    if policy == 'orthBase':
        return orth_base or surface
    if policy == 'lemma':
        key = lemma or orth_base or surface
    elif pos1 == '名詞' and pos2 == '固有名詞':
        # mixed（既定）: 固有名詞は lemma が片仮名読みになるため orthBase を使う
        key = orth_base or surface
    else:
        key = lemma or orth_base or surface
    if strip_homograph and '-' in key:
        key = HOMOGRAPH_RE.sub('', key) or key
    return key


COLUMNS = ['i', 'surface', 'lemma_key', 'lemma', 'orthBase', 'lForm',
           'pos1', 'pos2', 'pos3', 'pos4', 'cType', 'cForm', 'goshu']


def drop_stale(outdirs, keep_stems, label='出力'):
    """入力が無くなった出力ファイルを消す。

    **これが無いと，前の実行の残骸が次の工程に混ざる。**
    03b_merge_volumes.py で『夜明け前』の4巻を1ファイルに統合すると，
    data/xml から巻別の XML は消えるが，**すでに作ってある
    data/plain と data/tokens の巻別ファイルは残る**。06 はそれを
    そのままチャンクに分割するので，統合前の巻と統合後の作品が二重にコーパスへ入る。
    しかもエラーは出ない。

    出力ディレクトリは毎回この工程が作り直すものなので，入力に対応が
    無いファイルは消してよい。消したものは必ず名前を出す。
    """
    removed = []
    for d in outdirs:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.txt') and not fn.endswith('.tsv'):
                continue
            stem = os.path.splitext(fn)[0]
            if stem not in keep_stems:
                os.remove(os.path.join(d, fn))
                removed.append(os.path.join(os.path.basename(d), fn))
    if removed:
        print(f'\n[info ] 入力の無くなった{label} {len(removed)} 件を削除した'
              '（前の実行の残骸）:')
        for r in removed[:10]:
            print(f'         {r}')
        if len(removed) > 10:
            print(f'         …ほか {len(removed) - 10} 件')
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='indir', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--dicdir', default=None)
    ap.add_argument('--lemma-policy', default='mixed',
                    choices=['mixed', 'lemma', 'orthBase', 'surface'])
    ap.add_argument('--keep-homograph-suffix', action='store_true',
                    help='UniDic の同形異義接尾辞（私-代名詞 の -代名詞）を残す')
    ap.add_argument('--keep-punct', action='store_true',
                    help='補助記号をトークン列に残す（文体計量で句読点を使う場合）')
    ap.add_argument('--expect-dict', default=None, metavar='NAME',
                    help=f'この辞書でなければ止まる（本番は {PROD_DICT}）')
    ap.add_argument('--allow-feature-mismatch', action='store_true',
                    help='--lemma-policy が必要とする素性が無くても続ける（非推奨）')
    args = ap.parse_args()

    # 辞書の用意より先に入力を確かめる。UniDic の読み込みは重いので，
    # 入力が無いときに何十秒も待たせてからエラーで止まるのは望ましくない。
    if not os.path.isdir(args.indir):
        sys.exit(f'入力のディレクトリが無い: {args.indir}\n'
                 '  04_normalise.py を先に実行すること。渡すのは '
                 'data/plain/full であって data/plain ではない。')

    tagger, dic = build_tagger(args.dicdir, args.expect_dict)
    dic['features'] = probe_features(tagger, args.lemma_policy,
                                     args.allow_feature_mismatch)
    dic['lemma_policy'] = args.lemma_policy
    dic['strip_homograph_suffix'] = not args.keep_homograph_suffix
    dic['keep_punct'] = args.keep_punct
    dic['fugashi'] = FUGASHI_VERSION
    dirs = {k: os.path.join(args.out, k)
            for k in ('tsv', 'tokens_lemma', 'tokens_surface', 'tokens_content')}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    report = []
    unk_global = Counter()
    for name in sorted(os.listdir(args.indir)):
        if not name.endswith('.txt'):
            continue
        stem = name[:-4]
        text = open(os.path.join(args.indir, name), encoding='utf-8').read()
        rows, lem, sur, con = [], [], [], []
        unk = 0
        i = 0
        for line in text.split('\n'):
            if not line.strip():
                continue
            for w in tagger(line):
                f = w.feature
                pos1 = feat(f, 'pos1')
                lk = lemma_key(w, args.lemma_policy, not args.keep_homograph_suffix)
                i += 1
                rows.append([i, w.surface, lk, feat(f, 'lemma'), feat(f, 'orthBase'),
                             feat(f, 'lForm'), pos1, feat(f, 'pos2'), feat(f, 'pos3'),
                             feat(f, 'pos4'), feat(f, 'cType'), feat(f, 'cForm'),
                             feat(f, 'goshu')])
                if not feat(f, 'lemma') and pos1 not in SKIP_POS:
                    unk += 1
                    unk_global[w.surface] += 1
                if pos1 in SKIP_POS and not args.keep_punct:
                    continue
                lem.append(lk)
                sur.append(w.surface)
                if pos1 in CONTENT_POS:
                    con.append(lk)
            rows.append(['', '', '', '', '', '', 'EOS', '', '', '', '', '', ''])

        # utf-8-sig（BOM 付き）で書く。この TSV は受講生が Excel で開いて
        # 品詞や語彙素を確かめるためのものである。BOM が無いと macOS の
        # Excel は Shift_JIS と推定し，すべて文字化けする。
        with open(os.path.join(dirs['tsv'], stem + '.tsv'), 'w',
                  newline='', encoding='utf-8-sig') as fh:
            # 「どの辞書のどの語形か」を1行目に残す。**auto と書くのでは
            # 記録にならない**（v1 コーパスの失敗はここに由来する）。
            # 解決した実際のパス・辞書名・版を書く。
            fh.write(f'# dictionary={dic["dictionary"]}\t'
                     f'version={dic["version"] or "不明"}\t'
                     f'lemma_policy={args.lemma_policy}\t'
                     f'dicdir={dic["dicdir"] or "fugashi 既定"}\t'
                     f'fugashi={FUGASHI_VERSION}\n')
            w = csv.writer(fh, delimiter='\t', lineterminator='\n')
            w.writerow(COLUMNS)
            w.writerows(rows)
        for key, seq in (('tokens_lemma', lem), ('tokens_surface', sur),
                         ('tokens_content', con)):
            with open(os.path.join(dirs[key], stem + '.txt'), 'w', encoding='utf-8') as fh:
                fh.write(' '.join(seq) + '\n')

        report.append({'file': stem, 'tokens': len(lem), 'types': len(set(lem)),
                       'content_tokens': len(con), 'content_types': len(set(con)),
                       'unknown_tokens': unk,
                       'unknown_rate': round(unk / max(1, len(lem)), 4)})
        print(f'  [ok  ] {stem:<22} 語数{len(lem):>9,} 異なり{len(set(lem)):>7,} '
              f'内容語{len(con):>9,} 未知語率{report[-1]["unknown_rate"]:>7.2%}')


    # 入力の無くなった出力を掃除する。03b で分冊を統合したのに
    # data/plain を作り直していないと，巻別のトークン列が残って
    # 06 に拾われる（統合前の巻と統合後の作品が二重に入る）。
    drop_stale(list(dirs.values()),
               {os.path.splitext(r['file'])[0] for r in report},
               label='トークン列')
    if report:
        dest = os.path.join(args.out, 'tokenise_report.csv')
        with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=list(report[0].keys()))
            w.writeheader()
            w.writerows(report)
        dest2 = os.path.join(args.out, 'unknown_words.csv')
        with open(dest2, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow(['surface', 'freq'])
            w.writerows(unk_global.most_common(3000))
        print(f'\n[ok  ] {len(report)} ファイル。リポート → {dest}')
        print(f'[ok  ] 未知語リスト（頻度順3000件）→ {dest2}')
        print('      未知語率が高いファイルは，底本の正書法（旧仮名・踊り字・外字）を疑うこと。')

        # ---- 由来を残す -----------------------------------------------------
        # 下流（06）はこれを読み，config/pipeline.yaml と食い違えば止まる。
        # 辞書を替えて --out を分け忘れると，別の辞書の列が混ざる。
        # **その事故を見つけるのはこのファイルだけである。**
        dic['files'] = len(report)
        dic['tokens'] = sum(r['tokens'] for r in report)
        dic['unknown_tokens'] = sum(r['unknown_tokens'] for r in report)
        dic['unknown_rate'] = round(
            dic['unknown_tokens'] / max(1, dic['tokens']), 5)
        dic['input'] = args.indir
        dic['finished'] = _dt.datetime.now().astimezone().isoformat(
            timespec='seconds')
        dest3 = os.path.join(args.out, 'tokenise_provenance.json')
        with open(dest3, 'w', encoding='utf-8') as fh:
            json.dump(dic, fh, ensure_ascii=False, indent=2)
            fh.write('\n')
        print(f'[ok  ] 由来の記録 → {dest3}')
        print(f'      辞書 {dic["dictionary"]} {dic["version"]}／'
              f'語形 {args.lemma_policy}／延べ {dic["tokens"]:,} 語／'
              f'未知語率 {dic["unknown_rate"]:.2%}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02_fetch_aozora.py
==================
青空文庫から (a) 全作品の書誌索引 と (b) 指定作品の XHTML 原文 を取得する。

なぜ XHTML か
-------------
プレーンテクスト版（Shift_JIS）はルビを ``《》`` で，外字を ``※［＃…］`` で表す。
XHTML 版は ``<ruby>`` 要素と ``<span class="notes">`` で構造化されており，
**本文とメタ情報の境界が機械的に決まる**。v1 コーパスの外字欠落（592箇所）・
奥付混入は，プレーンテクストを正規表現で削った副作用である。XHTML から作り直す。

なぜ索引 CSV か
---------------
``list_person_all_extended_utf8.zip`` には公開中の全作品について
作品ID・作品名・**初出**・**分類番号(NDC)**・文字遣い種別・底本・
テクスト/XHTML ファイル URL が入っている。図書カードを1件ずつ読む必要がなく，
かつ書誌の典拠が一元化される。v1 の ``year`` 列の誤りはすべて，この索引を
使っていれば起きなかった種類の誤りである。

使い方
------
    # 索引の取得（初回および更新時）
    python3 02_fetch_aozora.py index --out data/aozora

    # 解決結果だけ確認（ダウンロードしない）。未解決行は原因と候補つきで表示され，
    # data/aozora/unresolved.csv に保存される
    python3 02_fetch_aozora.py resolve --manifest config/corpus_manifest.tsv --out data/aozora

    # マニフェストに挙げた作品の取得
    python3 02_fetch_aozora.py works --manifest config/corpus_manifest.tsv --out data/aozora

    # 現行64点（core）だけ先に取得する
    python3 02_fetch_aozora.py works --manifest config/corpus_manifest.tsv \\
        --out data/aozora --set core

注意
----
青空文庫のサーバに負荷をかけないよう，既定で 1 秒あたり 1 件までに制限している。
取得済みのファイルは再取得しない（``--force`` で上書き）。
"""
from __future__ import annotations

import argparse
import csv
import difflib
import io
import os
import re
import shutil
import sys
import time
import urllib.request
import zipfile

INDEX_URL = "https://www.aozora.gr.jp/index_pages/list_person_all_extended_utf8.zip"
UA = "JLitCorpus/2026 (academic corpus construction; contact: tomoji.tabata@example.ac.jp)"
SLEEP = 1.0

# 取得済み XHTML の共有キャッシュ（マシン内。admin 権限は要らない）
# 共用 iMac は /Users/Shared/jlit，自分の Mac（--personal）は ~/.jlit
DEFAULT_CACHE = (next((os.path.join(d, 'aozora-cache')
                       for d in ('/Users/Shared/jlit', os.path.expanduser('~/.jlit'))
                       if os.path.isdir(d)), '/Users/Shared/jlit/aozora-cache')
                 if sys.platform == 'darwin' else '')


class FetchError(RuntimeError):
    """取得に失敗したことを，受講生に読める形で伝えるための例外。"""


def shared_cache(args) -> str:
    """XHTML の共有キャッシュの場所を返す。無ければ空文字。

    DH Lab の iMac は XCreds 認証でホームがマシンごとに別々（共有されない）ため，
    別のマシンに移るたびに 100 件超を取り直すことになる。青空文庫の
    サーバにも負荷をかけるので，**マシン内で共有できる場所**に
    キャッシュを置く。macOS の ``/Users/Shared`` は admin 権限なしに
    全ユーザーが読み書きできるので，そこを既定にしている。

    優先順位: ``--cache`` > 環境変数 ``JLIT_AOZORA_CACHE`` > 既定の共有場所
    """
    path = (getattr(args, 'cache', None)
            or os.environ.get('JLIT_AOZORA_CACHE')
            or DEFAULT_CACHE)
    if not path or path == '-':
        return ''
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, '.write_test')
        with open(probe, 'w') as fh:
            fh.write('')
        os.remove(probe)
        return path
    except OSError:
        return ''                      # 書けないなら黙って使わない


def fetch(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f'{url}\n  サーバが {e.code} を返した。'
                         'URL（作品ID）が正しいか確認すること。') from e
    except urllib.error.URLError as e:
        raise FetchError(
            f'{url}\n  接続できない（{e.reason}）。次を順に確かめること。\n'
            '   1. ネットワークにつながっているか\n'
            '   2. 学内プロキシの設定が要るか（環境変数 HTTPS_PROXY）\n'
            '   3. 青空文庫のサーバが一時的に止まっていないか\n'
            '   しばらくして再実行する（取得済みの分は共有キャッシュから読むので速い）。') from e
    except TimeoutError as e:
        raise FetchError(f'{url}\n  時間切れ。回線が遅い場合は時間をおいて再実行する。') from e


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------

def cmd_index(args) -> int:
    os.makedirs(args.out, exist_ok=True)
    dest = os.path.join(args.out, 'list_person_all_extended_utf8.csv')
    if os.path.exists(dest) and not args.force:
        print(f"[skip] {dest} は既に存在する（--force で再取得）")
        return 0
    print(f"[get ] {INDEX_URL}")
    try:
        blob = fetch(INDEX_URL, timeout=180)
    except FetchError as e:
        print(f'[ERR ] 索引を取得できない\n  {e}')
        return 1
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = [n for n in z.namelist() if n.lower().endswith('.csv')][0]
        data = z.read(name)
    with open(dest, 'wb') as fh:
        fh.write(data)
    rows = load_index(dest)
    print(f"[ok  ] {dest}  ({len(rows):,} 作品)")
    print("      主な列:", ', '.join(list(rows[0].keys())[:12]), '...')
    return 0


def load_index(path: str) -> list[dict]:
    with open(path, encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh))


def col(row: dict, *names: str):
    """索引 CSV の列名は版により揺れるため，候補名から最初に見つかったものを返す。"""
    for n in names:
        if n in row:
            return row[n]
    for n in names:
        for k in row:
            if n in k:
                return row[k]
    return ''


def norm_author(s: str) -> str:
    """著者名照合用の正規化。

    青空文庫の索引は姓と名を別の列に持つため連結して比較するが，
    利用者は「ツルゲーネフ イワン」「ツルゲーネフ・イワン」のように
    区切りを入れて書きがちである。区切りをすべて除いて比較する。
    """
    return re.sub(r'[\s　・･]', '', s)


def norm_title(s: str) -> str:
    """作品名照合用の正規化。副題・巻次・記号を除く。"""
    s = re.sub(r'[\s　]', '', s)
    s = re.sub(r'[「」『』（）()【】〔〕・,，、。．.]', '', s)
    s = re.sub(r'^\d+', '', s)
    return s


def build_lookup(rows: list[dict]) -> dict:
    """(姓名, 正規化作品名) → 行 の索引を作る。"""
    lut: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        sei = col(r, '姓')
        mei = col(r, '名')
        author = norm_author(f"{sei}{mei}")
        title = col(r, '作品名')
        sub = col(r, '副題')
        for t in {title, (title + sub) if sub else title}:
            lut.setdefault((author, norm_title(t)), []).append(r)
    return lut


def read_manifest(path: str) -> list[dict]:
    # utf-8-sig で開く。受講生がマニフェストを Excel で編集して保存すると
    # 先頭に BOM が付き，utf-8 で読むと最初の列名が '\ufeffauthor_ja' になって
    # **1行も読めない**。utf-8-sig は BOM が無くても害が無い。
    with open(path, encoding='utf-8-sig') as fh:
        rdr = csv.DictReader((l for l in fh if not l.startswith('#')), delimiter='\t')
        return [r for r in rdr if r.get('author_ja')]


def build_author_index(rows: list[dict]) -> dict[str, list[dict]]:
    """姓名 → その作家の全作品。未解決行の診断に使う。"""
    by: dict[str, list[dict]] = {}
    for r in rows:
        author = norm_author(f"{col(r, '姓')}{col(r, '名')}")
        by.setdefault(author, []).append(r)
    return by


def diagnose(m: dict, by_author: dict[str, list[dict]]) -> dict:
    """未解決行の原因を切り分け，取るべき対処を添える。

    原因は3つに分かれ，対処がそれぞれ異なる。
      1. title_mismatch   作家は登録あり，作品名が一致しない
                          → 表記のずれ。候補を出してマニフェストを直す。
      2. author_differs   同名の作品が別の著者名の下にある
                          → 翻訳作品は原著者の下に登録される（『即興詩人』は
                            アンデルセン，『小公子』はバーネット）。author_ja を直す。
      3. author_not_found 著者そのものが青空文庫にない
                          → 著作権保護期間中（没後70年）の可能性が高い。
                            作品名を直しても解決しないので，マニフェストから外す。
    """
    author = norm_author(m['author_ja'])
    works = by_author.get(author, [])
    rec = {'set': m.get('set', ''), 'id': m.get('id', ''),
           'author_ja': m['author_ja'], 'title': m['title'],
           'note': m.get('note', '')}
    if not works:
        # 作品名だけで全体を探す。翻訳作品は原著者の下に入っていることが多い
        # （『即興詩人』はアンデルセン，『小公子』はバーネットの作品として登録される）。
        want0 = norm_title(m['title'])
        elsewhere = [(a, r) for a, rs in by_author.items() for r in rs
                     if norm_title(col(r, '作品名')) == want0]
        if elsewhere:
            names = ' / '.join(
                f"{a}［{col(r, '作品名')} 作品ID {col(r, '作品ID')}］"
                for a, r in elsewhere[:5])
            rec.update({
                'cause': 'author_differs',
                'diagnosis': '同名の作品は登録があるが，別の著者名の下にある',
                'action': ('翻訳作品は原著者の下に登録される。'
                           'マニフェストの author_ja をその著者名に直し，'
                           '訳者は note 欄に書く'),
                'candidates': names,
            })
            return rec
        # 姓だけでも探す（「徳冨蘆花」と「徳富蘆花」のような表記ゆれ）
        near = sorted({a for a in by_author if a.startswith(author[:1])})[:8]
        rec.update({
            'cause': 'author_not_found',
            'diagnosis': '青空文庫にこの著者名の登録がない',
            'action': ('著作権保護期間中の可能性が高い（没後70年）。'
                       'マニフェストから外すか，著者名の表記を確認する'),
            'candidates': ' / '.join(near),
        })
        return rec
    want = norm_title(m['title'])
    scored = sorted(
        works,
        key=lambda r: -difflib.SequenceMatcher(
            None, want, norm_title(col(r, '作品名') + col(r, '副題'))).ratio())
    tops = []
    for r in scored[:5]:
        t = col(r, '作品名')
        s = col(r, '副題')
        tops.append(f"{t}{(' ' + s) if s else ''}"
                    f"[{col(r, '作品ID')}/{col(r, '文字遣い種別')}]")
    rec.update({
        'cause': 'title_mismatch',
        'diagnosis': f'著者は登録あり（全{len(works)}作品）。作品名が一致しない',
        'action': 'config/corpus_manifest.tsv の title を下の候補に合わせる',
        'candidates': ' / '.join(tops),
    })
    return rec


def resolve(manifest: list[dict], lut: dict) -> tuple[list[dict], list[dict]]:
    """マニフェストの各行を索引に突き合わせる。"""
    hit, miss = [], []
    for m in manifest:
        author = norm_author(m['author_ja'])
        key = (author, norm_title(m['title']))
        cands = lut.get(key, [])
        if not cands and m.get('aozora_work_id'):
            cands = [r for rows in lut.values() for r in rows
                     if col(r, '作品ID').lstrip('0') == m['aozora_work_id'].lstrip('0')]
        if not cands:
            miss.append(m)
            continue
        # 文字遣い種別の希望があれば絞り込む（新字新仮名を既定とする）
        want = m.get('kana_orthography') or '新字新仮名'
        pick = next((c for c in cands if col(c, '文字遣い種別') == want), cands[0])
        rec = dict(m)
        rec.update({
            'work_id': col(pick, '作品ID'),
            'person_id': col(pick, '人物ID'),
            'title_aozora': col(pick, '作品名'),
            'subtitle': col(pick, '副題'),
            'ndc': col(pick, '分類番号'),
            'shoshutsu': col(pick, '初出'),
            'kana_orth': col(pick, '文字遣い種別'),
            'teihon': col(pick, '底本名1'),
            'teihon_pub': col(pick, '底本出版社名1'),
            'teihon_year': col(pick, '底本初版発行年1'),
            'oyahon': col(pick, '底本の親本名1'),
            'card_url': col(pick, '図書カードURL'),
            'txt_url': col(pick, 'テキストファイルURL'),
            'html_url': col(pick, 'XHTML/HTMLファイルURL'),
            'n_candidates': len(cands),
        })
        hit.append(rec)
    return hit, miss


def year_from_shoshutsu(s: str) -> tuple[str, str]:
    """初出欄の自由記述から西暦年（開始・終了）を取り出す。"""
    ys = [int(y) for y in re.findall(r'(1[6-9]\d{2}|20\d{2})', s or '')]
    if not ys:
        return '', ''
    return str(min(ys)), str(max(ys))


def report_misses(miss: list[dict], idx: list[dict], out_dir: str) -> None:
    """未解決行を原因つきで表示し，CSV に書き出す。"""
    if not miss:
        print('[ok  ] 未解決なし')
        return
    by_author = build_author_index(idx)
    recs = [diagnose(m, by_author) for m in miss]
    from collections import Counter as _C
    n = _C(r['cause'] for r in recs)
    print(f'\n[warn] 未解決 {len(recs)} 件  '
          f"著者が未登録 {n['author_not_found']} / 別著者の下 {n['author_differs']} / "
          f"作品名の不一致 {n['title_mismatch']}")
    print('=' * 74)
    for cause, label in (
            ('title_mismatch', '■ 作品名が一致しない（マニフェストを直せば解決する）'),
            ('author_differs', '■ 別の著者名の下にある（翻訳作品に多い）'),
            ('author_not_found', '■ 著者が青空文庫に未登録（保護期間中の可能性）')):
        group = [r for r in recs if r['cause'] == cause]
        if not group:
            continue
        print(f'\n{label}  {len(group)} 件')
        for r in group:
            tag = f"[{r['set']}]" if r['set'] else ''
            print(f"  {tag} {r['author_ja']}『{r['title']}』")
            print(f"      → {r['action']}")
            if r['candidates']:
                print(f"      候補: {r['candidates']}")
    dest = os.path.join(out_dir, 'unresolved.csv')
    with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=list(recs[0].keys()))
        w.writeheader()
        w.writerows(recs)
    print(f'\n[ok  ] 未解決の一覧 → {dest}')
    print('      core の行が混じっている場合は最優先で直すこと。')
    print('      prio1〜4 は増補候補なので，当面は --set core で先へ進んでもよい。')


def cmd_resolve(args) -> int:
    idx = load_index(os.path.join(args.out, 'list_person_all_extended_utf8.csv'))
    lut = build_lookup(idx)
    man = read_manifest(args.manifest)
    hit, miss = resolve(man, lut)
    for h in hit:
        yf, yt = year_from_shoshutsu(h['shoshutsu'])
        h['year_first'], h['year_first_end'] = yf, yt
    os.makedirs(args.out, exist_ok=True)
    dest = os.path.join(args.out, 'resolved.csv')
    if hit:
        with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=list(hit[0].keys()))
            w.writeheader()
            w.writerows(hit)
    print(f"[ok  ] 解決 {len(hit)} / 未解決 {len(miss)}  → {dest}")
    amb = [h for h in hit if h['n_candidates'] > 1]
    for a in amb:
        print(f"  [多義] {a['author_ja']}『{a['title']}』 候補{a['n_candidates']}件 → "
              f"作品ID {a['work_id']} ({a['kana_orth']}) を採用")
    report_misses(miss, idx, args.out)
    return 0


# --------------------------------------------------------------------------
# 本文の取得
# --------------------------------------------------------------------------

def cmd_works(args) -> int:
    idx = load_index(os.path.join(args.out, 'list_person_all_extended_utf8.csv'))
    lut = build_lookup(idx)
    man = read_manifest(args.manifest)
    if getattr(args, 'set', None):
        want = set(args.set)
        man = [m for m in man if m.get('set') in want]
        print(f"[filt] set={'/'.join(sorted(want))} に限定: {len(man)} 行")
    hit, miss = resolve(man, lut)
    raw = os.path.join(args.out, 'xhtml')
    os.makedirs(raw, exist_ok=True)
    log = []
    cache = shared_cache(args)
    if cache:
        print(f'[cache] 共有キャッシュ: {cache}')
    for i, h in enumerate(hit, 1):
        url = h['html_url']
        if not url:
            print(f"  [skip] {h['author_ja']}『{h['title']}』 XHTML URL なし")
            continue
        # **ここで付ける名前が，以後すべての工程の語幹になる**
        # （03 は .html を .xml に replace するだけ）。青空文庫の索引は
        # ID を6桁に 0 埋めして配っているが，それに頼らず自分で揃える。
        # 索引の書式が変われば語幹が変わり，メタデータとの突合が静かに
        # 外れる。
        name = (f"{str(h['person_id']).strip().zfill(6)}"
                f"_{str(h['work_id']).strip().zfill(6)}.html")
        dest = os.path.join(raw, name)
        if os.path.exists(dest) and not args.force:
            print(f"  [have] {name}  {h['author_ja']}『{h['title_aozora']}』")
            log.append((name, h, 'cached'))
            continue
        # 共有キャッシュにあれば青空文庫には取りに行かない。
        # DH Lab の iMac はホームがマシンごとに別々（共有されない）ので，別のユーザーや
        # 前の授業回で取得済みのものを使い回せると待ち時間が大きく減る。
        cached = os.path.join(cache, name) if cache else ''
        if cached and os.path.exists(cached) and not args.force:
            shutil.copyfile(cached, dest)
            print(f"  [share] {name}  {h['author_ja']}『{h['title_aozora']}』"
                  ' （共有キャッシュから）')
            log.append((name, h, 'shared'))
            continue
        try:
            blob = fetch(url)
        except FetchError as e:
            print(f'  [ERR ] {h["author_ja"]}『{h["title"]}』\n         {e}')
            continue
        except Exception as e:                                   # noqa: BLE001
            print(f'  [ERR ] {url}  {type(e).__name__}: {e}')
            continue
        with open(dest, 'wb') as fh:
            fh.write(blob)
        if cache:
            try:                      # 次の人のために共有キャッシュにも置く
                # 仮の名前で書いてから名前を変える。別のユーザーが同時に
                # 読んでも書きかけのファイルを読み込まない。誰でも読めるようにする
                final = os.path.join(cache, name)
                part = f'{final}.part.{os.getpid()}'
                shutil.copyfile(dest, part)
                os.chmod(part, 0o644)
                os.replace(part, final)
            except OSError as e:
                print(f'  [warn] 共有キャッシュに書けない（{e}）。取得は成功している')
        yf, yt = year_from_shoshutsu(h['shoshutsu'])
        print(f"  [get ] {name}  {h['author_ja']}『{h['title_aozora']}』 "
              f"NDC={h['ndc']} 初出={yf or '?'}")
        log.append((name, h, 'fetched'))
        time.sleep(SLEEP)
    # 取得結果の台帳
    if log:
        dest = os.path.join(args.out, 'fetch_log.csv')
        with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow(['file', 'author_ja', 'title_aozora', 'work_id', 'person_id',
                        'ndc', 'shoshutsu', 'year_first', 'year_first_end',
                        'kana_orth', 'teihon', 'teihon_year', 'card_url', 'status'])
            for name, h, st in log:
                yf, yt = year_from_shoshutsu(h['shoshutsu'])
                w.writerow([name, h['author_ja'], h['title_aozora'], h['work_id'],
                            h['person_id'], h['ndc'], h['shoshutsu'], yf, yt,
                            h['kana_orth'], h['teihon'], h['teihon_year'],
                            h['card_url'], st])
        print(f"[ok  ] 台帳 → {dest}")
    report_misses(miss, idx, args.out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name, fn in (('index', cmd_index), ('resolve', cmd_resolve), ('works', cmd_works)):
        p = sub.add_parser(name)
        p.add_argument('--out', default='data/aozora')
        p.add_argument('--force', action='store_true')
        p.add_argument('--cache', default=None,
                       help='取得済み XHTML の共有キャッシュ。既定は '
                            f'{DEFAULT_CACHE or "（なし）"}。'
                            '"-" を渡すとキャッシュを使わない')
        if name != 'index':
            p.add_argument('--manifest', required=True)
            p.add_argument('--set', nargs='*', default=None,
                           help='マニフェストの set 列で絞る（例 --set core）')
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03b_merge_volumes.py
====================
分冊で公開されている長篇を，1作品1ファイルにまとめる。

なぜ必要か
------------
青空文庫は長篇を分冊ごとに別カードで公開する。『夜明け前』は4カード，
『家』は上下2カードである。**そのまま使うと分析の単位が「作品」ではなく
「冊」になる。**

* Delta や doc2vec の最近傍が，同じ作品の別の巻になる。「最近傍が同じ
  作家である割合」は自明に上がり，作家効果の指標として意味を失う
* 1作家あたりの作品数が水増しされる。島崎藤村なら，『夜明け前』4冊と
  『家』2冊を別作品として数えるだけで9点になる
* 語数・TTR・文体指標が巻ごとに分かれ，他の作品と比べられない

どの段階でまとめるか
--------------------
**XML の段階**でまとめる。TEI 的にも，分冊は1つの ``<text>`` の中の
``<div type="volume">`` であって，別の文書ではない。ここでまとめておけば
04（正規化）・05（解析）・06（チャンク分割）・99（検証）はすべて
「1作品1ファイル」を前提に書いたまま動く。トークン列を後から連結する
方法もあるが，本文・会話文・地の文の各系統を別々に連結する必要があり，
どれか1つを忘れると気づかないまま食い違う。

まとめたもとの巻別 XML は ``data/xml/_volumes/`` に退避する。捨てない。
巻ごとに比較したくなったときに必要になる。

使い方
------
    python3 03b_merge_volumes.py --xml data/xml --config config/merge_volumes.tsv
    python3 03b_merge_volumes.py --xml data/xml --dry-run     # 確認だけ

メタデータ側の後始末
--------------------
まとめた後，``members`` のうち ``canonical`` 以外の行は本文を持たなく
なる。``metadata/editorial_expansion.csv`` でそれらを
``completeness = merged`` にし，``canonical`` の行を ``complete`` に
戻すこと。``merged`` は ``superseded`` と同じく集計から外れる。
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys

try:
    from lxml import etree as ET
except ImportError:                                          # noqa: BLE001
    sys.exit('lxml が必要である: pip install lxml --break-system-packages')

VOL_DIR = '_volumes'


def read_config(path: str) -> list[dict]:
    # Excel で保存された BOM 付きでも読めるように utf-8-sig で開く
    with open(path, encoding='utf-8-sig') as fh:
        rdr = csv.DictReader((l for l in fh if not l.startswith('#')),
                             delimiter='\t')
        rows = [r for r in rdr if (r.get('canonical') or '').strip()]
    for r in rows:
        r['members'] = [m.strip() for m in r['members'].split(',') if m.strip()]
    return rows


def merge_group(xmldir: str, g: dict, dry: bool) -> tuple[bool, str]:
    """1グループをまとめる。戻り値は (成功したか, メッセージ)。"""
    paths = [os.path.join(xmldir, m + '.xml') for m in g['members']]
    missing = [m for m, p in zip(g['members'], paths) if not os.path.exists(p)]
    if missing:
        return False, f"巻が足りない: {'，'.join(missing)}"
    if len(g['members']) < 2:
        return False, 'members が1件しかない'
    if g['canonical'] not in g['members']:
        return False, f"canonical {g['canonical']} が members に無い"

    docs = [ET.parse(p) for p in paths]
    base = docs[0]
    root = base.getroot()

    # --- ヘッダ: 各巻の底本と作品IDを残す ---------------------------------
    title = root.find('.//{*}titleStmt/{*}title')
    if title is not None and g.get('title'):
        title.text = g['title']
    pub = root.find('.//{*}publicationStmt')
    src = root.find('.//{*}sourceDesc')
    for d, stem in list(zip(docs, g['members']))[1:]:
        r = d.getroot()
        if pub is not None:
            for idno in r.findall('.//{*}publicationStmt/{*}idno'):
                if idno.get('type') == 'aozora_work':
                    pub.append(ET.fromstring(
                        f'<idno type="aozora_work_part">{idno.text}</idno>'))
        if src is not None:
            for b in r.findall('.//{*}sourceDesc/{*}bibl'):
                if b.get('type') == 'base_text':
                    nb = ET.fromstring('<bibl type="base_text_part"/>')
                    nb.text = b.text
                    nb.set('n', stem)
                    src.append(nb)
    enc = root.find('.//{*}encodingDesc')
    if enc is not None:
        note = ET.SubElement(enc, 'p')
        note.text = ('分冊を 03b_merge_volumes.py が結合: '
                     + '，'.join(g['members'])
                     + '。もとの巻別 XML は data/xml/_volumes/ にある。'
                     + (g.get('note') or ''))

    # --- 本文: 各巻を <div type="volume"> で包む -------------------------
    body = root.find('.//{*}text/{*}body')
    if body is None:
        return False, 'body が無い'
    kept = list(body)
    for el in kept:
        body.remove(el)

    def wrap(children, n, stem, vol_title):
        div = ET.SubElement(body, 'div')
        div.set('type', 'volume')
        div.set('n', str(n))
        div.set('{http://www.w3.org/XML/1998/namespace}id', 'vol' + str(n))
        div.set('corresp', stem)
        if vol_title:
            h = ET.SubElement(div, 'head')
            h.set('type', 'volume')
            h.text = vol_title
        for c in children:
            div.append(c)

    t0 = docs[0].getroot().find('.//{*}titleStmt/{*}title')
    wrap(kept, 1, g['members'][0], None)
    for i, (d, stem) in enumerate(list(zip(docs, g['members']))[1:], start=2):
        b = d.getroot().find('.//{*}text/{*}body')
        if b is None:
            return False, f'{stem} に body が無い'
        wrap(list(b), i, stem, None)
    del t0

    out = os.path.join(xmldir, g['canonical'] + '.xml')
    n_div = len(body.findall('{*}div'))
    if dry:
        return True, f'（確認のみ）{len(g["members"])} 巻 → {out} に div {n_div} 個'

    # 先にもとのファイルを退避してから書く。canonical は上書きになるので，
    # 退避が後回しだと結合結果を退避してしまう。
    vdir = os.path.join(xmldir, VOL_DIR)
    os.makedirs(vdir, exist_ok=True)
    for m, p in zip(g['members'], paths):
        shutil.move(p, os.path.join(vdir, m + '.xml'))
    base.write(out, encoding='UTF-8', xml_declaration=True)
    return True, f'{len(g["members"])} 巻 → {os.path.basename(out)}（div {n_div} 個）'


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--xml', required=True, help='03 が書いた XML のディレクトリ')
    ap.add_argument('--config', default='config/merge_volumes.tsv')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not os.path.isdir(args.xml):
        sys.exit(f'XML のディレクトリが無い: {args.xml}')
    if not os.path.exists(args.config):
        sys.exit(f'設定ファイルが無い: {args.config}')

    groups = read_config(args.config)
    if not groups:
        sys.exit(f'まとめる指定が1件も無い: {args.config}')

    ok = ng = 0
    for g in groups:
        done, msg = merge_group(args.xml, g, args.dry_run)
        mark = '[ok  ]' if done else '[skip]'
        print(f"{mark} {g['group']:<10} {g.get('title', ''):<8} {msg}")
        ok += done
        ng += (not done)

    print(f'\n[ok  ] {ok} グループをまとめた' + (f'／{ng} グループは見送り' if ng else ''))
    if ok and not args.dry_run:
        print(f'       もとの巻別 XML → {os.path.join(args.xml, VOL_DIR)}')
        print('       **メタデータの後始末を忘れないこと。**')
        print('       canonical 以外の行を editorial_expansion.csv で')
        print('       completeness = merged にする。そうしないと，本文の無い行が')
        print('       作品数に数えられたままになる。')
    if ng:
        print('       見送ったグループは，03 がまだその巻を作っていないか，')
        print('       設定の語幹が間違っている。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

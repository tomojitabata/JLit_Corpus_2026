#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
find_unclosed_quotes.py
=======================
閉じ括弧のない ``「`` を突き止める。``03_aozora2xml.py`` の変換リポートで
``said_unclosed`` が 0 でなかった作品を，**底本のどこが原因か**まで追うための道具。

なぜ正規表現だけでは足りないか
------------------------------
対応の取れた括弧の列（入れ子を含む）は**正規言語ではない**ので，
正規表現だけでは原理的に判定できない。本スクリプトは

  * 素早い目視・grep 用の**近似の正規表現**（``--regex-only``）
  * 括弧の深さを数えながら段落を走査する**正確な判定**（既定）

の両方を持つ。近似で当たりをつけ，正確な判定で位置を確定する，という使い方を想定する。

検出する事象
------------
=========================  ====================================================
``unclosed_at_end``        ``「`` が開いたまま本文が終わった
``spans_too_many``         ``」`` は来たが ``--max-par`` 段落を超えた
                           （= 変換器が会話標示を見送った箇所）
``block_boundary``         閉じないまま見出し・字下げブロックの境界に達した
``stray_close``            対応する ``「`` のない ``」``
``line_imbalance``         1 行のなかで ``「`` と ``」`` の数が合わない（参考）
=========================  ====================================================

使い方
------
    # 変換後の XML から（最も確実。変換器が見送った段落がそのまま残っている）
    python3 scripts/find_unclosed_quotes.py --in data/xml

    # 正規化後のプレーンテクストから
    python3 scripts/find_unclosed_quotes.py --in data/plain/full

    # 青空文庫の原文（XHTML）から。底本の組版を確認するときはこちら
    python3 scripts/find_unclosed_quotes.py --in data/aozora/xhtml

    # 近似の正規表現だけを表示（grep に貼るため）
    python3 scripts/find_unclosed_quotes.py --regex-only
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

OPEN, CLOSE = '「', '」'

# --------------------------------------------------------------------------
# 近似の正規表現（grep / エディタの検索に貼って使う）
# --------------------------------------------------------------------------
APPROX_PATTERNS: dict[str, tuple[str, str]] = {
    'A': (r'「[^「」\n]*$',
          '行内で閉じない 「（次の行へ続く会話でも当たるので要目視）'),
    'B': (r'^[^「」\n]*」',
          '行内で開かない 」（前の行から続く会話でも当たる）'),
    'C': (r'「[^「」\n]*「',
          '同一行で 「 が連続（入れ子か閉じ忘れ）'),
    'D': (r'」[^「」\n]*」',
          '同一行で 」 が連続'),
    'E': (r'「(?=[、。）])',
          '「 の直後が句読点（ほぼ確実に閉じ忘れか組版の都合）'),
}

# 変換後の XML で「会話標示が付かなかった」段落を拾う。
# <p>…</p> の内側に 「 があるのに <said> も <quote> も無いもの。
# 断片化した会話は <said part="M"> のように属性を持つので `<said` で照合する。
UNMARKED_P_RE = re.compile(
    r'<p>(?!(?:(?!</p>).)*<(?:said|quote)\b)(?:(?!</p>).)*?「(?:(?!</p>).)*?</p>',
    re.S)
MARKED_RE = re.compile(r'<(?:said|quote)\b')

TAG_RE = re.compile(r'<[^>]+>')
NOTE_RE = re.compile(r'<note\b.*?</note>', re.S)
BR_RE = re.compile(r'<br\s*/?>', re.I)
BLOCK_RE = re.compile(r'<head|<ab\b|</ab>|<h[1-5]\b|jisage_')


def strip_markup(s: str) -> str:
    """注記を除いてからタグを外し，本文だけを残す。"""
    s = NOTE_RE.sub('', s)
    return TAG_RE.sub('', s)


def paragraphs(path: str) -> list[tuple[int, str, bool, bool]]:
    """ファイルを (段落番号, 本文, ブロック境界か, 標示済みか) の並びにする。

    第4要素は変換後 XML 用で，その段落に ``<said>`` / ``<quote>`` が
    付いているかを示す。継続引用符や埋め込みテクストとして
    ``03_aozora2xml.py`` が既に処理した箇所を，未処理と取り違えないため。
    """
    raw = open(path, encoding='utf-8', errors='replace').read()
    ext = os.path.splitext(path)[1].lower()
    out: list[tuple[int, str, bool, bool]] = []
    if ext == '.xml':
        for i, m in enumerate(re.finditer(r'<p>(.*?)</p>|<head\b[^>]*>.*?</head>',
                                          raw, re.S), 1):
            chunk = m.group(0)
            if chunk.startswith('<head'):
                out.append((i, '', True, False))
            else:
                inner = m.group(1)
                out.append((i, strip_markup(inner), False,
                            bool(MARKED_RE.search(NOTE_RE.sub('', inner)))))
    elif ext in ('.html', '.htm'):
        body = raw
        mm = re.search(r'<div class="main_text">(.*?)</div>', raw, re.S)
        if mm:
            body = mm.group(1)
        for i, seg in enumerate(BR_RE.split(body), 1):
            out.append((i, strip_markup(seg).strip(),
                        bool(BLOCK_RE.search(seg)), False))
    else:                                     # プレーンテクスト：1 行 = 1 段落
        for i, line in enumerate(raw.split('\n'), 1):
            out.append((i, line, False, False))
    return out


def scan(path: str, max_par: int) -> list[dict]:
    """括弧の深さを数えながら走査し，事象を列挙する。"""
    pars = paragraphs(path)
    marked = {no: mk for no, _t, _b, mk in pars}
    events: list[dict] = []
    depth = 0
    open_par = 0
    open_ctx = ''
    name = os.path.basename(path)

    def keep(par: int) -> bool:
        """その段落が既に標示済みなら報告しない（変換器が処理済み）。"""
        return not marked.get(par, False)

    def ctx(text: str, pos: int, w: int = 34) -> str:
        return text[max(0, pos - w):pos + w].replace('\n', '／')

    for no, text, is_block, _mk in pars:
        if depth and is_block:
            if keep(open_par):
                events.append({'file': name, 'kind': 'block_boundary',
                               'paragraph': open_par,
                               'detail': f'{no}段落目の境界で打ち切り',
                               'context': open_ctx})
            depth, open_par, open_ctx = 0, 0, ''
        for pos, ch in enumerate(text):
            if ch == OPEN:
                if depth == 0:
                    open_par, open_ctx = no, ctx(text, pos)
                depth += 1
            elif ch == CLOSE:
                if depth == 0:
                    if keep(no):
                        events.append({'file': name, 'kind': 'stray_close',
                                       'paragraph': no, 'detail': '対応する「がない」',
                                       'context': ctx(text, pos)})
                    continue
                depth -= 1
                if depth == 0:
                    span = no - open_par
                    if span >= max_par and keep(open_par):
                        events.append({'file': name, 'kind': 'spans_too_many',
                                       'paragraph': open_par,
                                       'detail': f'{span}段落かけて閉じた'
                                                 f'（上限{max_par}）',
                                       'context': open_ctx})
        o, c = text.count(OPEN), text.count(CLOSE)
        if o != c and abs(o - c) > 1 and keep(no):
            events.append({'file': name, 'kind': 'line_imbalance',
                           'paragraph': no, 'detail': f'開{o} 閉{c} 差{o - c:+d}',
                           'context': text[:70].replace('\n', '／')})
    if depth and keep(open_par):
        events.append({'file': name, 'kind': 'unclosed_at_end', 'paragraph': open_par,
                       'detail': f'本文末まで閉じない（深さ{depth}）',
                       'context': open_ctx})
    return events


def scan_xml_unmarked(path: str) -> list[dict]:
    """変換後 XML で，会話標示が付かなかった段落をそのまま拾う。

    ``<note>`` の中の ``「`` は校訂注（``［＃「○」は底本では「●」］`` など）であって
    本文の会話ではない。先に注記を取り除いてから判定する。
    """
    raw = open(path, encoding='utf-8', errors='replace').read()
    name = os.path.basename(path)
    out = []
    for i, m in enumerate(re.finditer(r'<p>(.*?)</p>', raw, re.S), 1):
        inner = NOTE_RE.sub('', m.group(1))       # 注記を除いてから判定
        if OPEN in inner and not MARKED_RE.search(inner):
            out.append({'file': name, 'kind': 'unmarked_paragraph', 'paragraph': i,
                        'detail': '「を含むが<said>が付いていない段落',
                        'context': strip_markup(inner)[:90]})
    return out


def print_regexes() -> None:
    print('近似の正規表現（grep -P / エディタの検索に貼る）\n' + '=' * 66)
    for key, (pat, desc) in APPROX_PATTERNS.items():
        print(f'  [{key}] {desc}')
        print(f'       {pat}')
    print('\n変換後 XML で「会話標示が付かなかった段落」を拾う（Python 用）:')
    print(f'       {UNMARKED_P_RE.pattern}')
    print('\n  注意: これらは近似である。対応の取れた括弧の列は正規言語でないため，')
    print('        正規表現だけでは判定できない。位置の確定は本スクリプトの')
    print('        既定モード（走査）を使うこと。')
    print('\n  シェルから素早く見るなら:')
    print("       grep -nP '「[^「」]*$' data/plain/full/*.txt | head -40")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--in', dest='indir', default=None,
                    help='data/xml / data/plain/full / data/aozora/xhtml のいずれか')
    ap.add_argument('--out', default=None, help='結果を書き出す CSV')
    ap.add_argument('--max-par', type=int, default=20,
                    help='会話が何段落まで続いたら異常とみなすか（変換器と同値）')
    ap.add_argument('--regex-only', action='store_true')
    ap.add_argument('--limit', type=int, default=60, help='画面に出す件数')
    args = ap.parse_args()

    if args.regex_only or not args.indir:
        print_regexes()
        return 0

    files = sorted(f for f in os.listdir(args.indir)
                   if os.path.splitext(f)[1].lower() in ('.xml', '.txt', '.html', '.htm'))
    if not files:
        sys.exit(f'対象ファイルがない: {args.indir}')

    events: list[dict] = []
    for f in files:
        p = os.path.join(args.indir, f)
        events.extend(scan(p, args.max_par))
        if f.lower().endswith('.xml'):
            events.extend(scan_xml_unmarked(p))

    from collections import Counter
    kinds = Counter(e['kind'] for e in events)
    print(f'{len(files)} ファイルを走査')
    print('=' * 70)
    if not events:
        print('  閉じ括弧の不整合は見つからなかった。')
        return 0
    for k, n in kinds.most_common():
        print(f'  {k:<22} {n:>5} 件')
    print()

    by_file = Counter(e['file'] for e in events)
    print('件数の多いファイル（上位15）:')
    for f, n in by_file.most_common(15):
        print(f'  {n:>4}  {f}')
    print()

    shown = 0
    for kind in ('unclosed_at_end', 'stray_close', 'spans_too_many',
                 'block_boundary', 'unmarked_paragraph', 'line_imbalance'):
        group = [e for e in events if e['kind'] == kind]
        if not group:
            continue
        print(f'■ {kind}  {len(group)} 件')
        for e in group[:max(1, args.limit // 6)]:
            print(f'   {e["file"]} 第{e["paragraph"]}段落  {e["detail"]}')
            print(f'      …{e["context"]}…')
            shown += 1
        if len(group) > args.limit // 6:
            print(f'   （ほか {len(group) - args.limit // 6} 件。CSV を見ること）')
        print()

    if args.out:
        os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
        with open(args.out, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=['file', 'kind', 'paragraph',
                                               'detail', 'context'])
            w.writeheader()
            w.writerows(events)
        print(f'[ok  ] 全 {len(events)} 件 → {args.out}')

    print('\n読み方:')
    print('  unclosed_at_end / block_boundary … 底本に閉じ括弧がない。'
          '青空文庫の原文を確認すること')
    print('  stray_close … 前の段落から続く会話の末尾なら正常。'
          '単独で現れるなら底本の誤り')
    print('  spans_too_many … 長大な会話。--max-par を上げれば標示できる')
    print('  unmarked_paragraph … 変換器が標示を見送った段落そのもの')
    return 0


if __name__ == '__main__':
    sys.exit(main())

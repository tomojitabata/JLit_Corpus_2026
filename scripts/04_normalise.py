#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04_normalise.py
===============
TEI 風 XML → 解析用プレーンテクスト（正規化済み）。

何をどう数えるかの決定をここに集約する
--------------------------------------
XML には本文以外の情報（ルビ・注記・見出し・会話標示）がすべて残っている。
本スクリプトは ``config/pipeline.yaml`` の設定にしたがい，そこから
**解析対象のみ**を取り出す。v1 の問題は，この決定がスクリプトの正規表現に
埋め込まれていて，あとから何をしたのか分からなくなっていた点にある。

出力ストリーム
--------------
``full``      地の文＋会話（既定の分析対象）
``narration`` 地の文のみ（``<said>`` を除く）。語りの人称を測るときはこちら
``speech``    会話のみ。会話文比率・文体指標に用いる
``embedded``  埋め込みテクストのみ（``<quote type="embedded">``）。
              書簡体・手記の部分を切り出して対照するときに使う

いずれも，注記 ``<note>``・ルビの読み ``@rt`` は除外し，外字 ``<g>`` は
復元した文字に置換する（未解決の外字は ``〓`` に置換して計数する）。

会話文比率の欠測
----------------
底本が会話符を使わない作品（樋口一葉，戯曲）や，``「`` で起こして
「と云う」で閉じる口述筆記（福沢諭吉『福翁自伝』）では，会話文比率 0 は
観測値ではなく**欠測**である。``03_aozora2xml.py`` が
``<catRef scheme="speech_markup">`` に ``full`` / ``partial`` / ``none`` を
記録しているので，``full`` 以外では ``speech_ratio`` を空欄（NA）にする。
0 と NA を取り違えると，文体指標も話法の通時変化も系統的に歪む。

正規化の内容
------------
1. 仮名踊り字 ``ゝゞヽヾ`` の展開（``々`` は展開しない）
2. くの字点 ``／＼ ／″＼ 〳〵 〴〵`` の展開
3. 全角ラテン文字・全角数字の半角化
4. 連続する空白・改行の整理

使い方
------
    python3 04_normalise.py --in data/xml --out data/plain --config config/pipeline.yaml
    python3 04_normalise.py --in data/xml --out data/plain --streams full narration speech
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.aozora import normalise_chars, normalise_iteration_marks  # noqa: E402

UNRESOLVED_CHAR = '〓'


def extract(elem: ET.Element, mode: str, keep_ruby_base: bool = True,
            narration_excludes_embedded: bool = False) -> str:
    """要素木から解析対象の文字列を取り出す。

    mode:
        full       地の文＋会話
        narration  <said> を除く
        speech     <said> のみ
        embedded   <quote type="embedded"> のみ

    ``<said>`` は ``<quote type="embedded">`` の内側にも現れる（書簡の中の
    対話）。これは会話として数える。埋め込みテクストの地の文を語りから
    外したいときは ``narration_excludes_embedded`` を真にする。
    """
    parts: list[str] = []

    def rec(e: ET.Element, in_said: bool, in_emb: bool) -> None:
        tag = e.tag
        if tag == 'note':
            # 注記の中身は常に除外するが，直後のテキスト（tail）は本文なので拾う
            if e.tail:
                emit(e.tail, in_said, in_emb)
            return
        if tag == 'g':
            ref = e.get('ref', '')
            emit((e.text or '') if ref != 'unresolved' else UNRESOLVED_CHAR,
                 in_said, in_emb)
            if e.tail:
                emit(e.tail, in_said, in_emb)
            return
        if tag == 'ruby':
            # 読み(@rt)は取らない。基底文字に外字 <g> が含まれることがある
            # （<ruby rt="…"><g ref="…">媆</g>相寿桂</ruby>）ので子も辿る
            if keep_ruby_base:
                emit(e.text or '', in_said, in_emb)
                for c in e:
                    rec(c, in_said, in_emb)
            if e.tail:
                emit(e.tail, in_said, in_emb)
            return
        if tag == 'said':
            emit(e.text or '', True, in_emb)
            for c in e:
                rec(c, True, in_emb)
            if e.tail:
                emit(e.tail, in_said, in_emb)
            return
        if tag == 'quote' and e.get('type') == 'embedded':
            emit(e.text or '', in_said, True)
            for c in e:
                rec(c, in_said, True)
            if e.tail:
                emit(e.tail, in_said, in_emb)
            return
        if tag == 'head':
            # 見出しは独立行として残す（段落境界の手がかりになる）
            if mode not in ('speech', 'embedded'):
                parts.append('\n' + (e.text or '').strip() + '\n')
            if e.tail:
                emit(e.tail, in_said, in_emb)
            return
        if tag == 'p':
            emit(e.text or '', in_said, in_emb)
            for c in e:
                rec(c, in_said, in_emb)
            parts.append('\n')
            if e.tail:
                emit(e.tail, in_said, in_emb)
            return
        emit(e.text or '', in_said, in_emb)
        for c in e:
            rec(c, in_said, in_emb)
        if e.tail:
            emit(e.tail, in_said, in_emb)

    def emit(text: str, in_said: bool, in_emb: bool) -> None:
        if not text:
            return
        if mode == 'narration':
            if in_said or (narration_excludes_embedded and in_emb):
                return
        elif mode == 'speech' and not in_said:
            return
        elif mode == 'embedded' and not in_emb:
            return
        parts.append(text)

    rec(elem, False, False)
    return ''.join(parts)


def tidy(text: str) -> str:
    text = re.sub(r'[ \t　]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ ]*\n[ ]*', '\n', text)
    text = text.strip()
    return (text + '\n') if text else ''     # 空のストリームは 0 字にする


def load_config(path: str | None) -> dict:
    cfg = {
        'streams': ['full', 'narration', 'speech', 'embedded'],
        'expand_iteration_marks': True,
        'normalise_latin_digits': True,
        'keep_ruby_base': True,
        'narration_excludes_embedded': False,
    }
    if path and os.path.exists(path):
        try:
            import yaml                                  # type: ignore
            # BOM 付きの YAML は yaml.safe_load が読めない。utf-8-sig で剥がす
            with open(path, encoding='utf-8-sig') as fh:
                user = (yaml.safe_load(fh) or {}).get('normalise', {}) or {}
            cfg.update(user)
        except ImportError:
            print('[warn] PyYAML がないため既定設定で実行する（pip install pyyaml）')
    return cfg


def drop_stale(outdirs, keep_stems, label='出力'):
    """入力が無くなった出力ファイルを消す。

    **これが無いと，前の実行の残骸が次の工程に混ざる。**
    03b_merge_volumes.py で『夜明け前』の4巻を1ファイルに統合すると，
    data/xml から巻別の XML は消えるが，**すでに作ってある
    data/plain と data/tokens の巻別ファイルは残る**。06 はそれを
    そのまま刻むので，統合前の巻と統合後の作品が二重にコーパスへ入る。
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
    ap.add_argument('--config', default='config/pipeline.yaml')
    ap.add_argument('--streams', nargs='*', default=None)
    args = ap.parse_args()

    # 素の FileNotFoundError を投げると，何を先に走らせればよいのかが
    # 分からない。前の工程の名前まで書く。
    if not os.path.isdir(args.indir):
        sys.exit(f'入力のディレクトリが無い: {args.indir}\n'
                 '  03_aozora2xml.py（分冊があれば 03b_merge_volumes.py も）を'
                 '先に走らせること。')

    cfg = load_config(args.config)
    streams = args.streams or cfg['streams']

    report = []
    for name in sorted(os.listdir(args.indir)):
        if not name.endswith('.xml'):
            continue
        tree = ET.parse(os.path.join(args.indir, name))
        root = tree.getroot()
        body = root.find('.//body')
        hdr = root.find('.//teiHeader')
        title = hdr.findtext('.//title', '') if hdr is not None else ''
        author = hdr.findtext('.//author', '') if hdr is not None else ''
        markup = 'full'
        if hdr is not None:
            for cr in hdr.iter('catRef'):
                if cr.get('scheme') == 'speech_markup':
                    markup = cr.get('target') or 'full'
        row = {'file': name, 'author': author, 'title': title,
               'speech_markup': markup}
        for mode in streams:
            raw = extract(body, mode, cfg['keep_ruby_base'],
                          cfg['narration_excludes_embedded'])
            stats = {}
            if cfg['expand_iteration_marks']:
                raw, stats = normalise_iteration_marks(raw)
            if cfg['normalise_latin_digits']:
                raw = normalise_chars(raw)
            raw = tidy(raw)
            d = os.path.join(args.out, mode)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, name.replace('.xml', '.txt')), 'w', encoding='utf-8') as fh:
                fh.write(raw)
            row[f'{mode}_chars'] = len(raw)
            if mode == 'full':
                row['unresolved_gaiji'] = raw.count(UNRESOLVED_CHAR)
                row['kana_odoriji_expanded'] = stats.get('kana_odoriji_expanded', 0)
                row['kunoji_expanded'] = stats.get('kunoji_expanded', 0)
                row['kanji_odoriji_kept'] = raw.count('々')
        if 'full_chars' in row and 'speech_chars' in row and row['full_chars']:
            # 会話標示が信用できない作品では，0 ではなく欠測（空欄）にする
            row['speech_ratio'] = (round(row['speech_chars'] / row['full_chars'], 4)
                                   if markup == 'full' else '')
        if 'full_chars' in row and 'embedded_chars' in row and row['full_chars']:
            row['embedded_ratio'] = round(
                row['embedded_chars'] / row['full_chars'], 4)
        report.append(row)
        sr = row.get('speech_ratio', '')
        sr_txt = f'{sr:>6.1%}' if isinstance(sr, float) else f'{"NA(" + markup + ")":>10}'
        print(f'  [ok  ] {name:<22} 全{row.get("full_chars", 0):>8,}字 '
              f'会話率{sr_txt} '
              f'踊り字展開 仮名{row.get("kana_odoriji_expanded", 0):>4}/'
              f'くの字{row.get("kunoji_expanded", 0):>3} '
              f'未解決外字{row.get("unresolved_gaiji", 0):>3}')

    # 入力の無くなった出力を掃除する。03b で分冊を統合すると，
    # data/xml から巻別 XML は消えるが data/plain の巻別テクストは残る。
    drop_stale([os.path.join(args.out, m) for m in streams],
               {os.path.splitext(r['file'])[0] for r in report},
               label='正規化テクスト')

    if report:
        # 列は全行の和集合。作品によって embedded_chars が無かったりするので，
        # 最初の行の keys だけを使うと落ちる。
        keys = []
        for r in report:
            for k in r:
                if k not in keys:
                    keys.append(k)
        dest = os.path.join(args.out, 'normalise_report.csv')
        with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction='ignore')
            w.writeheader()
            w.writerows(report)
        na = [r for r in report if r.get('speech_ratio') == '']
        emb = [r for r in report if r.get('embedded_chars')]
        print(f'\n[ok  ] {len(report)} ファイル。レポート → {dest}')
        if na:
            print(f'[note] 会話文比率を欠測にした作品 {len(na)} 件: '
                  + '，'.join(f"{r['title']}({r['speech_markup']})" for r in na))
            print('       分析では 0 で埋めず，欠測のまま扱うこと')
        if emb:
            print(f'[note] 埋め込みテクストを含む作品 {len(emb)} 件。'
                  'data/plain/embedded/ に切り出した')
    return 0


if __name__ == '__main__':
    sys.exit(main())

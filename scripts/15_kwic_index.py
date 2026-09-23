#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
15_kwic_index.py
================
KWIC コンコーダンサの索引を作る。**05 の TSV から**作る。

    python3 scripts/15_kwic_index.py \\
        --tsv data/tokens/tsv \\
        --meta metadata/corpus_metadata_v3.csv \\
        --out data/kwic

出力（``data/kwic/``）
    ``kwic_tokens.npz``  形態素の並び（表層形・語彙素・品詞・活用形・語種・
                         作品・文の番号。すべて整数の配列）
    ``kwic_index.json``  語彙と作品の一覧，**由来**（辞書名・版・作成時刻・
                         突合できなかった作品）

なぜ TSV から作るのか
---------------------
``tokens_lemma`` / ``tokens_surface``（空白区切りの列）には品詞が無く，
句読点も落ちている。**同じ解析結果から表層形と語彙素の両方を引きたい**ので，
1行1形態素の TSV が唯一の入口である。これで「語彙素で検索して表層形で読む」
ができる。

索引を作り直すのは，**05 を走らせ直したとき**（辞書を替えた・本文を直した）。
辞書が変われば切り方が変わるので，古い索引のままでは用例が本文と食い違う。
由来に辞書名と版が入っているので，画面でいつでも確かめられる。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kwic_core import build_index                      # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tsv', default=str(ROOT / 'data' / 'tokens' / 'tsv'),
                    help='05 の出力の tsv/ ディレクトリ')
    ap.add_argument('--meta', default=None,
                    help='既定: metadata/ の *_v3_local.csv > v3 > v2')
    ap.add_argument('--out', default=str(ROOT / 'data' / 'kwic'))
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    meta = args.meta
    if meta is None:
        for cand in ('corpus_metadata_v3_local.csv', 'corpus_metadata_v3.csv',
                     'corpus_metadata_v2.csv'):
            p = ROOT / 'metadata' / cand
            if p.exists():
                meta = str(p)
                break
    if not meta or not os.path.exists(meta):
        sys.exit('メタデータが無い。00_extend_metadata.py で v3 を作ること'
                 '（自分の版は metadata/corpus_metadata_v3_local.csv）。')
    if not args.quiet:
        print(f'[in  ] {args.tsv}')
        print(f'[meta] {meta}')

    prov = build_index(args.tsv, meta, args.out, quiet=args.quiet)
    if not args.quiet:
        print('\n次は画面を出す:')
        print('    python3 scripts/16_kwic_server.py --open')
    # 突合が総崩れのときは 0 で返さない（気づかせる）
    return 1 if len(prov.get('unmatched_meta', [])) > prov['works'] * 0.1 else 0


if __name__ == '__main__':
    raise SystemExit(main())

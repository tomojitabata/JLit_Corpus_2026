#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
13_pipeline_compare.py
======================
辞書ごとに Step 3–8 を最後まで通し，**外在的な**指標で比べる。

なぜ外在的な指標が要るのか
--------------------------
``12_dict_compare.py`` が出すのは内在的な指標（未知語率・境界一致率）で，
**「よく解析できたか」しか言わない。** 私たちが本当に知りたいのは
「**どちらの辞書で解析したほうが，やりたい分析がうまくいくか**」である。

そこで，同じテクストを辞書ごとに解析し直し，そのトークン列で
Step 4・7・8 を回して，分析の成績を比べる。

======================  ==================================================
指標                    何を意味するか
======================  ==================================================
Delta 最近傍（作家）    文体の指紋が取れているか。高いほど良い
Delta 最近傍（時代）    作家効果と交絡するので，作家と**並べて**見る
doc2vec 最近傍          同上を埋め込みで
カテゴリ効果（作家）    埋め込みが作家を捉えている強さ
LDA coherence           トピックの上位語が実際に共起するか。0 に近いほど良い
LDA exclusivity         トピックが固有の語を持つか。高いほど良い
語彙（ストップ語除去後）小さすぎれば刻みすぎ，大きすぎれば統合不足を疑う
======================  ==================================================

**比べてよいもの・いけないもの**

* 比べてよい … 上の**成績**。辞書が違っても「作家を当てられた率」は
  同じ土俵の数字である
* 比べてはいけない … MFW の中身，特徴語のリスト，トピックの上位語。
  語彙素の体系が違うので，**同じ語が別の見出しになる**。
  「こちらの辞書のほうが良い語が出た」という比較は成り立たない

**成績が同点なら内在指標で決める。** 逆に成績が大きく違うなら，
なぜ違うのかを ``dict_disagreements.csv`` の実例で説明できるまで
結論を出さないこと。

使い方
------
    python3 13_pipeline_compare.py \\
        --plain data/plain/full \\
        --dict cwj=/Users/Shared/jlit/unidic-cwj-202512 \\
        --dict kindai=/Users/Shared/jlit/unidic-kindai-bungo-v202512 \\
        --meta metadata/corpus_metadata_v3.csv \\
        --work data/dict_runs --out results/dict_compare \\
        --topics 40

    # 途中まで走っているときは，出来ている工程を飛ばす
    python3 13_pipeline_compare.py … --skip-existing

**時間がかかる。** 1辞書あたり，形態素解析 5–15 分，doc2vec 5–10 分，
MALLET 5–15 分を見込む。辞書2つなら1時間前後。
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def run(script: str, *args, dry: bool = False) -> float:
    cmd = [sys.executable, os.path.join(HERE, script)] + [str(a) for a in args]
    print(f'\n$ {" ".join(cmd[1:])}')
    if dry:
        return 0.0
    t0 = time.time()
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f'[FATAL] {script} が失敗した（終了コード {r.returncode}）')
    return time.time() - t0


def read_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig') as fh:
        return list(csv.DictReader(fh))


def nn_accuracy_from_matrix(path: str, meta: dict, fields=('author_ja', 'period')):
    """距離行列から「最近傍が同じ○○である率」を出す。

    07 は Delta 行列を書くが最近傍一致率は出さない。辞書比較では
    **これが本命の指標**なので，ここで計算する。
    """
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8-sig') as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 3:
        return {}
    stems = rows[0][1:]
    D = [[float(x) if x else float('inf') for x in r[1:]] for r in rows[1:]]
    out = {}
    for f in fields:
        lab = [meta.get(s, {}).get(f, '') for s in stems]
        hit = usable = 0
        for i in range(len(stems)):
            if not lab[i]:
                continue
            order = sorted(range(len(stems)), key=lambda j: D[i][j])
            nn = next((j for j in order if j != i), None)
            if nn is None:
                continue
            usable += 1
            hit += (lab[nn] == lab[i])
        if usable:
            out[f'delta_nn_{f}'] = round(hit / usable, 4)
    return out


def load_meta(path: str) -> dict:
    out = {}
    for r in read_rows(path):
        pid = str(r.get('aozora_person_id') or '').strip()
        wid = str(r.get('aozora_work_id') or '').strip()
        row = {'author_ja': r.get('author_ja', ''), 'period': r.get('period', '')}
        if pid and wid:
            out[f'{pid.zfill(6)}_{wid.zfill(6)}'] = row
            out[f'{pid}_{wid}'] = row
        fv = r.get('file_v1')
        if isinstance(fv, str) and fv.strip():
            out[os.path.splitext(fv)[0]] = row
    return out


def weighted_unknown(report_path: str) -> dict:
    rows = read_rows(report_path)
    if not rows:
        return {}
    tot = sum(int(r['tokens']) for r in rows)
    unk = sum(int(r['unknown_tokens']) for r in rows)
    typ = sum(int(r['types']) for r in rows)
    return {'tokens': tot, 'unknown_rate': round(unk / max(1, tot), 5),
            'types_sum': typ}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plain', required=True)
    ap.add_argument('--dict', action='append', required=True, metavar='名前=パス')
    ap.add_argument('--meta', required=True)
    ap.add_argument('--work', default='data/dict_runs', help='辞書ごとの中間生成物')
    ap.add_argument('--out', required=True)
    ap.add_argument('--chunk', type=int, default=2000)
    ap.add_argument('--max-chunks', type=int, default=40)
    ap.add_argument('--sample', default='stratified')
    ap.add_argument('--mfw', type=int, default=300)
    ap.add_argument('--topics', type=int, default=40)
    ap.add_argument('--iterations', type=int, default=2000)
    ap.add_argument('--dim', type=int, default=200)
    ap.add_argument('--epochs', type=int, default=40)
    ap.add_argument('--seed', type=int, default=20260920)
    ap.add_argument('--stages', nargs='*',
                    default=['tokenise', 'datasets', 'descriptive', 'doc2vec', 'lda'],
                    help='回す工程。既定は全部')
    ap.add_argument('--skip-existing', action='store_true',
                    help='出力が既にある工程を飛ばす')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    dicts = []
    for spec in args.dict:
        if '=' not in spec:
            sys.exit(f'--dict は 名前=パス の形で書くこと: {spec}')
        n, p = spec.split('=', 1)
        if not os.path.isdir(p) and not args.dry_run:
            sys.exit(f'辞書のディレクトリが無い: {p}')
        dicts.append((n, p))
    if len(dicts) < 2:
        sys.exit('--dict を2つ以上指定すること')
    os.makedirs(args.out, exist_ok=True)
    meta = load_meta(args.meta)

    def have(path):
        return args.skip_existing and os.path.exists(path)

    results = []
    for name, dicdir in dicts:
        base = os.path.join(args.work, name)
        tok = os.path.join(base, 'tokens')
        ds = os.path.join(base, 'datasets')
        desc = os.path.join(base, 'descriptive')
        d2v = os.path.join(base, 'd2v')
        lda = os.path.join(base, 'mallet')
        os.makedirs(base, exist_ok=True)
        print(f'\n{"=" * 68}\n辞書 {name}  ({dicdir})\n{"=" * 68}')
        secs = {}

        if 'tokenise' in args.stages and not have(os.path.join(tok, 'tokenise_report.csv')):
            # --allow-feature-mismatch を付ける理由。
            # 05 は単独で走るとき，--lemma-policy mixed が要る素性
            # （lemma / orthBase）が無い辞書では**止まる**。黙って表層形に
            # 落ちるほうが危ないからである。しかしここは**辞書を比べる**
            # 工程なので，素性の少ない辞書で止まると比較そのものが進まない。
            # 警告は出るので記録には残る。**その警告を読んでから結果を
            # 解釈すること**（docs/dictionary_comparison.md §8）。
            secs['tokenise'] = run('05_tokenise_unidic.py', '--in', args.plain,
                                   '--out', tok, '--dicdir', dicdir,
                                   '--lemma-policy', 'mixed',
                                   '--allow-feature-mismatch',
                                   dry=args.dry_run)
        if 'datasets' in args.stages and not have(os.path.join(ds, 'chunks_index.csv')):
            secs['datasets'] = run('06_build_datasets.py',
                                   '--tokens', os.path.join(tok, 'tokens_content'),
                                   '--meta', args.meta, '--out', ds,
                                   '--chunk', args.chunk, '--max-chunks', args.max_chunks,
                                   '--sample', args.sample, '--seed', args.seed,
                                   # ここでは**わざと**本番以外の辞書で回すので，
                                   # config との照合は切る（切らないと辞書ごとに
                                   # 警告が出て，本当の事故が埋もれる）
                                   '--no-dict-check',
                                   dry=args.dry_run)
        if 'descriptive' in args.stages and not have(os.path.join(desc, 'delta_matrix.csv')):
            secs['descriptive'] = run('07_descriptive_stats.py',
                                      '--tokens', os.path.join(tok, 'tokens_lemma'),
                                      '--tsv', os.path.join(tok, 'tsv'),
                                      '--meta', args.meta, '--out', desc,
                                      '--mfw', args.mfw, dry=args.dry_run)
        if 'doc2vec' in args.stages and not have(os.path.join(d2v, 'nn_accuracy.csv')):
            secs['doc2vec'] = run('09_doc2vec.py',
                                  '--chunks', os.path.join(ds, 'chunks'),
                                  '--index', os.path.join(ds, 'chunks_index.csv'),
                                  '--out', d2v, '--dim', args.dim,
                                  '--epochs', args.epochs, '--dm', 0,
                                  '--seed', args.seed, dry=args.dry_run)
        if 'lda' in args.stages and not have(os.path.join(lda, 'topics_summary.csv')):
            secs['lda'] = run('10_mallet.py', 'run', '--datasets', ds, '--out', lda,
                              '--topics', args.topics, '--iterations', args.iterations,
                              '--seed', args.seed, dry=args.dry_run)

        if args.dry_run:
            continue

        # ---- 成績を集める -------------------------------------------------
        row = {'dict': name, 'dicdir': dicdir}
        row.update(weighted_unknown(os.path.join(tok, 'tokenise_report.csv')))
        vs = read_rows(os.path.join(ds, 'vocab_stats.csv'))
        if vs:
            row['vocab'] = len(vs)
        ci = read_rows(os.path.join(ds, 'chunks_index.csv'))
        if ci:
            row['chunks'] = len(ci)
            row['works'] = len({r['work_stem'] for r in ci})
        row.update(nn_accuracy_from_matrix(
            os.path.join(desc, 'delta_matrix.csv'), meta))
        for r in read_rows(os.path.join(d2v, 'nn_accuracy.csv')):
            row[f'd2v_nn_{r["field"]}'] = float(r['nn_accuracy'])
        for r in read_rows(os.path.join(d2v, 'category_effects.csv')):
            if r.get('field') in ('author_ja', 'period'):
                row[f'd2v_gap_{r["field"]}'] = float(r.get('gap', 'nan'))
        diag = read_rows(os.path.join(lda, 'topic_diagnostics.csv'))
        if diag:
            coh = [float(r['coherence']) for r in diag if r.get('coherence')]
            exc = [float(r['exclusivity']) for r in diag if r.get('exclusivity')]
            if coh:
                row['lda_coherence'] = round(sum(coh) / len(coh), 4)
            if exc:
                row['lda_exclusivity'] = round(sum(exc) / len(exc), 4)
        row.update({f'sec_{k}': round(v) for k, v in secs.items()})
        results.append(row)

    if not results:
        print('\n[info ] --dry-run のため成績は集めていない。')
        return 0

    keys = []
    for r in results:
        for k in r:
            if k not in keys:
                keys.append(k)
    dest = os.path.join(args.out, 'pipeline_compare.csv')
    with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader(); w.writerows(results)
    print(f'\n[ok  ] {dest}')

    # ---- 画面に並べる -----------------------------------------------------
    show = [('unknown_rate', '未知語率', '%', False),
            ('vocab', '語彙', ',', None),
            ('delta_nn_author_ja', 'Delta最近傍(作家)', '%', True),
            ('delta_nn_period', 'Delta最近傍(時代)', '%', None),
            ('d2v_nn_author_ja', 'doc2vec最近傍(作家)', '%', True),
            ('lda_coherence', 'LDA coherence', 'f', True),
            ('lda_exclusivity', 'LDA exclusivity', 'f', True)]
    print(f'\n{"指標":<24}' + ''.join(f'{r["dict"]:>16}' for r in results))
    for key, lab, fmt, higher in show:
        if not any(key in r for r in results):
            continue
        cells = []
        vals = [r.get(key) for r in results]
        best = None
        num = [v for v in vals if isinstance(v, (int, float))]
        if higher is not None and num:
            best = max(num) if higher else min(num)
        for v in vals:
            if v is None:
                cells.append(f'{"—":>16}')
            elif fmt == '%':
                cells.append(f'{v:>15.1%}{"*" if v == best else " "}')
            elif fmt == ',':
                cells.append(f'{v:>15,}{"*" if v == best else " "}')
            else:
                cells.append(f'{v:>15.4f}{"*" if v == best else " "}')
        print(f'{lab:<24}' + ''.join(cells))
    print('\n  * は「その指標では有利」という印にすぎない。')
    print('  **指標ごとに勝者が割れるのが普通である。** 総合点は付けない。')
    print('  どの層で誰が勝ったかは dict_by_stratum.csv を，なぜ違うかは')
    print('  dict_disagreements.csv を見ること。判断の基準は')
    print('  docs/dictionary_comparison.md に書いてある。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
06_build_datasets.py
====================
解析用データセットの組み立て。作品長の極端な不均衡を扱うための分割と，
用途別の語彙フィルタを行う。

なぜ分割が要るか
----------------
本コーパスの作品長は最小 30,555 語（鴎外『大塩平八郎』）から
最大 502,937 語（藤村『夜明け前』）まで **16倍** の開きがある。
文書単位でトピックモデルを回すと，長篇 1 作が数トピックを独占し，
短篇の主題は検出されない。stylo 系の距離計算でも同じ問題が起きる。

したがって
    * 一定語数のチャンクに分割する（既定 2,000 語。JLit2000 と同じ設計）
    * チャンク数の上限を設けて，長篇が支配しないようにする（``--max-chunks``，
      既定 40）。上限を超える作品からは ``--sample`` の方法で抜き出す
の 2 段構えにする。

抜き出しは既定で **層化無作為**（``--sample stratified``）である。作品を
``max_chunks`` 個の区画に等分し，各区画から1つ無作為に引く。単純無作為だと
連続した区間がまるごと抜けることがあり，物語の位置と主題が相関する長篇では
その区間の話題が標本から落ちる。層化すれば冒頭・中盤・結末が必ず入る。

抜き出しは **``--seed`` で再現できる**。種は作品ごとに作るので，コーパスに
作品を足しても既存作品の標本は変わらない。

出力
----
``chunks/<id>__0001.txt`` …   分割されたトークン列
``chunks_index.csv``          チャンク → 作品メタデータの対応表
``mallet_input/``             MALLET の ``import-dir`` 用（時代別サブディレクトリ）
``vocab_stats.csv``           語彙の文書頻度（ストップリスト設計の根拠）
``stopwords_auto.txt``        文書頻度が高すぎる／低すぎる語の自動候補

使い方
------
    python3 06_build_datasets.py --tokens data/tokens/tokens_content \\
        --meta metadata/corpus_metadata_v2.csv --out data/datasets \\
        --chunk 2000 --max-chunks 40 --sample stratified --seed 20260920
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import shutil
from collections import Counter, defaultdict


def check_token_provenance(tokens_dir: str, config: str,
                           strict: bool = False) -> dict:
    """トークン列が**どの辞書で作られたか**を確かめる。

    なぜ要るか
    ----------
    辞書を替えて 05 を回すとき ``--out`` を分け忘れると，前の辞書で作った
    ファイルの上に新しい辞書のファイルが混ざる。入力の数は変わらないので
    件数では気づけず，エラーも出ない。**混ざった列で数えた頻度表は，もう
    何を測ったものでもない。** v1 コーパスで起きたのはこの種の事故である。

    05 は ``data/tokens/tokenise_provenance.json`` に辞書名と版を書く。
    ここでそれを ``config/pipeline.yaml`` の ``tokenise.dictionary`` と
    突き合わせる。食い違えば ``--strict-dict`` で止まる。
    """
    parent = os.path.dirname(os.path.normpath(tokens_dir))
    p = os.path.join(parent, 'tokenise_provenance.json')
    if not os.path.exists(p):
        print(f'[warn] 辞書の記録が無い（{p}）。'
              '\n       古い 05 で作ったトークン列である。'
              '**どの辞書で数えたか分からない。**'
              '\n       05_tokenise_unidic.py を今の版で回し直すこと。')
        return {}
    with open(p, encoding='utf-8') as fh:
        prov = json.load(fh)
    got, got_ver = prov.get('dictionary', '?'), prov.get('version', '')
    line = (f'[dic ] トークン列の辞書: {got} {got_ver}'
            f'／語形 {prov.get("lemma_policy", "?")}')
    rate = prov.get('unknown_rate')
    if isinstance(rate, (int, float)):
        line += f'／未知語率 {rate:.2%}'
    print(line)

    want = want_ver = ''
    if not config:
        # 辞書を比べる工程（13_pipeline_compare.py）からは照合を切る。
        # そこでは辞書ごとに --out を分けて**わざと別の辞書で回す**ので，
        # config と食い違うのが正しい。警告を出すと本当の事故が埋もれる。
        return prov
    try:
        import yaml                                            # type: ignore
        with open(config, encoding='utf-8-sig') as fh:
            tk = (yaml.safe_load(fh) or {}).get('tokenise', {}) or {}
        want = str(tk.get('dictionary') or '')
        want_ver = str(tk.get('dictionary_version') or '')
    except Exception:                                          # noqa: BLE001
        print('[warn] config/pipeline.yaml の辞書名を読めなかった'
              '（PyYAML 未導入か記載なし）。照合を飛ばす。')
    if want and got != want:
        msg = (f'トークン列は **{got}** で作られているが，'
               f'config/pipeline.yaml は **{want}** と記録している。\n'
               f'        どちらかが古い。辞書を替えたのなら 05 を回し直し，'
               f'替えていないのなら設定を直すこと。\n'
               f'        辞書ごとに比べたいのなら --out を分けること'
               f'（例 data/dict_runs/{got}/）。')
        if strict:
            raise SystemExit(f'[FATAL] {msg}')
        print(f'[warn] {msg}')
    elif want and want_ver and got_ver and got_ver != want_ver:
        print(f'[warn] 辞書の版が違う（設定 {want_ver} / 実測 {got_ver}）。'
              '報告に版を明記すること。')
    return prov


EXCLUDED = {
    'superseded': 'v1 の合本。分冊で取り直したので集計に足すと二重に数える',
    'merged':     '分冊。03b で canonical の巻に本文を統合した',
    'too_short':  '1チャンクに満たない作品',
}


def load_meta(path: str) -> tuple[dict, dict, dict]:
    """メタデータを，ファイル名の語幹から引ける辞書にする。

    戻り値は3つ。

    ``usable``    分析に使う行。これが従来の戻り値
    ``excluded``  行はあるが**意図的に外した**もの（語幹 → 理由）
    ``labels``    語幹 → ``作者『作品』``。除外した行も引ける

    ``excluded`` を分けるのが肝心である。分けずに ``usable`` から
    落とすだけだと，呼び出し側からは「メタデータに行が無い」のと
    区別がつかない。**本当は行があって，こちらが外したのだ**という
    ことが分からないと，「追補してからやり直せ」という無意味な指示が
    出続ける。

    鍵の作り方にも注意。青空文庫の作品 ID は索引では **0 埋めされていない**
    （``1743``）が，本パイプラインのファイル名は 6 桁に 0 埋めしてある
    （``000119_001743``）。素朴に連結すると ``000119_1743`` となり，
    **1件も一致しない**。一致しなくても ``meta.get(stem, {})`` は空辞書を
    返すので処理は最後まで通り，period も genre も空のまま
    チャンク索引ができあがる——気づきにくい壊れ方をする。
    そこで両方の綴りを登録し，一致率を呼び出し側で検査する。
    """
    with open(path, encoding='utf-8-sig') as fh:
        rows = list(csv.DictReader(fh))
    usable, excluded, labels = {}, {}, {}
    for r in rows:
        keys = []
        if r.get('file_v1'):
            keys.append(os.path.splitext(r['file_v1'])[0])
        pid = (r.get('aozora_person_id') or '').strip()
        wid = (r.get('aozora_work_id') or '').strip()
        if pid and wid:
            keys += [f'{pid.zfill(6)}_{wid.zfill(6)}',   # 本パイプラインの綴り
                     f'{pid}_{wid}',                     # 索引そのままの綴り
                     f'{pid.zfill(6)}_{wid}']
        lab = f"{r.get('author_ja', '?')}『{r.get('title_aozora', '?')}』"
        for k in keys:
            labels[k] = lab
        comp = (r.get('completeness') or '').strip()
        if comp in EXCLUDED:
            for k in keys:
                excluded[k] = comp
            continue
        for k in keys:
            usable[k] = r
    return usable, excluded, labels


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tokens', required=True, help='05 の tokens_* ディレクトリ')
    ap.add_argument('--meta', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--chunk', type=int, default=2000)
    ap.add_argument('--min-chunk', type=int, default=None,
                    help='この語数に満たないチャンクは捨てる。既定は --chunk の半分。'
                         '1チャンクにも満たない作品はチャンク0となり，'
                         'コーパスから落ちて名指しで報告される')
    ap.add_argument('--max-chunks', type=int, default=40,
                    help='1作品あたりのチャンク上限（既定 40）。0 で無制限。'
                         '結合後の『夜明け前』は約107チャンクあり，無制限だと'
                         '1作品でトピックを数本占有する')
    ap.add_argument('--sample', choices=['head', 'random', 'spread', 'stratified'],
                    default='stratified',
                    help='上限を超えるとき，どのチャンクを残すか（既定 stratified）。'
                         'stratified=作品を等分してから各区画で無作為，'
                         'random=作品全体から無作為，spread=等間隔，head=先頭から。'
                         'stratified と random は --seed で再現する')
    ap.add_argument('--min-df', type=int, default=3, help='自動ストップリスト：最小文書頻度')
    ap.add_argument('--max-df-ratio', type=float, default=0.80,
                    help='自動ストップリスト：文書頻度の上限比')
    ap.add_argument('--seed', type=int, default=20260920)
    ap.add_argument('--config', default='config/pipeline.yaml',
                    help='辞書名の照合に使う（tokenise.dictionary）')
    ap.add_argument('--strict-dict', action='store_true',
                    help='トークン列の辞書が config と違えば止まる')
    ap.add_argument('--no-dict-check', action='store_true',
                    help='辞書の照合をしない（辞書を比べる工程から呼ぶとき）')
    args = ap.parse_args()

    if not os.path.isdir(args.tokens):
        raise SystemExit(
            f'トークンのディレクトリが無い: {args.tokens}\n'
            '  05_tokenise_unidic.py を先に走らせること。渡すのは '
            'tokens_content である。')
    if not os.path.exists(args.meta):
        raise SystemExit(
            f'メタデータが無い: {args.meta}\n'
            '  00_extend_metadata.py で v3 を作ること'
            '（自分の版は metadata/corpus_metadata_v3_local.csv）。')

    # **どの辞書で作った列か**を先に確かめる。ここで止まるほうが，
    # 辞書の混ざった頻度表で半日考えるより安い。
    prov = check_token_provenance(
        args.tokens, '' if args.no_dict_check else args.config,
        args.strict_dict)

    meta, excluded, labels = load_meta(args.meta)
    unmatched: list[str] = []
    skipped: list[tuple] = []
    too_short: list[tuple] = []
    capped: list[tuple] = []
    chunk_dir = os.path.join(args.out, 'chunks')
    mallet_dir = os.path.join(args.out, 'mallet_input')
    for d in (chunk_dir, mallet_dir):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)

    index = []
    df = Counter()
    n_docs = 0
    for name in sorted(os.listdir(args.tokens)):
        if not name.endswith('.txt'):
            continue
        stem = name[:-4]
        # **意図して外した行のトークン列は，そもそも刻まない。**
        # ここで刻むと period も genre も空のチャンクが索引に並び，
        # 通時分析の分母を静かに汚す。統合した分冊のトークン列が
        # 消し忘れで残っている場合も，ここで止まる。
        if stem in excluded:
            skipped.append((stem, excluded[stem], labels.get(stem, stem)))
            continue
        m = meta.get(stem, {})
        if not m:
            unmatched.append(stem)
        toks = open(os.path.join(args.tokens, name), encoding='utf-8').read().split()
        n = len(toks)
        pieces = [toks[i:i + args.chunk] for i in range(0, n, args.chunk)]
        # 末尾の半端なチャンクは，下限に満たなければ捨てる（長さの揃いを優先）。
        #
        # **ここに ``len(pieces) > 1`` という例外を付けてはいけない。**
        # 1チャンクに満たない作品がその例外に救われ，87語の断片が
        # 2000語のチャンクと同じ1行としてチャンク索引に並ぶ。
        # トピックモデルでは語数の足りない文書の分布が退化し，
        # チャンク単位の keyness や doc2vec では分散の大きい外れ値になる。
        # **短すぎる作品は「チャンク0」として落とし，名指しで報告する**のが正しい。
        minlen = args.min_chunk if args.min_chunk is not None else args.chunk // 2
        while pieces and len(pieces[-1]) < minlen:
            pieces.pop()
        if not pieces:
            too_short.append((stem, n, labels.get(stem, ''),))
            continue
        total = len(pieces)
        keep = list(range(total))
        if args.max_chunks and total > args.max_chunks:
            # **乱数は作品ごとに種を作る。** `random.seed()` を最初に一度
            # 呼んで使い回すと，各作品の標本は「それまでに何回 random を
            # 呼んだか」に依存する。作品を1点足しただけで，後ろに並ぶ
            # 全作品の標本が入れ替わり，前回の結果と比べられなくなる。
            # 種に語幹を混ぜれば，コーパスの構成が変わっても**同じ作品からは
            # 同じチャンクが選ばれる**。
            rng = random.Random(f'{args.seed}:{stem}')
            if args.sample == 'head':
                keep = keep[:args.max_chunks]
            elif args.sample == 'random':
                keep = sorted(rng.sample(keep, args.max_chunks))
            elif args.sample == 'stratified':
                # 作品を max_chunks 個の区画に等分し，各区画から1つ引く。
                # 無作為でありながら，冒頭・中盤・結末が必ず入る。
                #
                # 境界は **切り捨て**で作ること。round だと区画が空になり
                # 得る書き方になり（この分岐は total > max_chunks のときしか
                # 通らないので実害は出ないが），切り捨てなら隣り合う境界の
                # 差が floor(total/max_chunks) ≧ 1 であることが保証でき，
                # 「必ず max_chunks 個そろう」と言い切れる。
                edges = [i * total // args.max_chunks
                         for i in range(args.max_chunks + 1)]
                keep = sorted(rng.randrange(a, b)
                              for a, b in zip(edges, edges[1:]) if b > a)
            else:  # spread: 作品全体から等間隔に
                step = total / args.max_chunks
                keep = sorted({int(i * step) for i in range(args.max_chunks)})
            capped.append((stem, total, len(keep),
                           m.get('author_ja', ''), m.get('title_aozora', '')))
        for k in keep:
            cid = f'{stem}__{k + 1:04d}'
            with open(os.path.join(chunk_dir, cid + '.txt'), 'w', encoding='utf-8') as fh:
                fh.write(' '.join(pieces[k]) + '\n')
            # MALLET は import-dir でサブディレクトリ名をラベルにする
            label = m.get('period', 'unknown').replace('/', '_')
            d = os.path.join(mallet_dir, label)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, cid + '.txt'), 'w', encoding='utf-8') as fh:
                fh.write(' '.join(pieces[k]) + '\n')
            index.append({
                'chunk_id': cid, 'work_stem': stem, 'chunk_no': k + 1,
                'chunk_tokens': len(pieces[k]),
                'id': m.get('id', ''), 'author_ja': m.get('author_ja', ''),
                'author_sex': m.get('author_sex', ''),
                'title': m.get('title_aozora', ''),
                'year_first': m.get('year_first', ''), 'period': m.get('period', ''),
                'ndc': m.get('ndc', ''), 'genre_main': m.get('genre_main', ''),
                'genre_sub': m.get('genre_sub', ''), 'audience': m.get('audience', ''),
                'register_level': m.get('register_level', ''),
                'narration': m.get('narration', ''),
                'style_class': m.get('style_class', ''),
            })
            df.update(set(pieces[k]))
            n_docs += 1
        print(f'  [ok  ] {stem:<24} {n:>9,}語 → チャンク {len(keep):>3}/{total:<3}')

    with open(os.path.join(args.out, 'chunks_index.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=list(index[0].keys()))
        w.writeheader()
        w.writerows(index)

    # 語彙統計とストップリスト候補
    rows = [{'term': t, 'df': c, 'df_ratio': round(c / n_docs, 5)}
            for t, c in df.most_common()]
    with open(os.path.join(args.out, 'vocab_stats.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=['term', 'df', 'df_ratio'])
        w.writeheader()
        w.writerows(rows)
    auto = [r['term'] for r in rows
            if r['df'] < args.min_df or r['df_ratio'] > args.max_df_ratio]
    with open(os.path.join(args.out, 'stopwords_auto.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(auto) + '\n')

    print(f'\n[ok  ] チャンク {n_docs:,} 個 / 語彙 {len(rows):,} 種')
    print(f'[ok  ] 索引 → {os.path.join(args.out, "chunks_index.csv")}')
    print(f'[ok  ] 自動ストップリスト候補 {len(auto):,} 語 → stopwords_auto.txt')
    print('      （df<{} または df比>{} の語。採否は必ず目視で決めること）'
          .format(args.min_df, args.max_df_ratio))

    # データセット自身に「どう作ったか」を持たせる。論文の方法節に
    # 書くべき3行（辞書・チャンク長・上限と種）がここに揃う。
    # **図や表だけが残って作り方が分からない**状態を作らないため。
    dest = os.path.join(args.out, 'dataset_provenance.json')
    with open(dest, 'w', encoding='utf-8') as fh:
        json.dump({
            'tokenise': prov or '記録なし（古い 05 で作った列）',
            'tokens_dir': args.tokens,
            'meta': args.meta,
            'chunk_tokens': args.chunk,
            'min_chunk_tokens': (args.min_chunk if args.min_chunk is not None
                                 else args.chunk // 2),
            'max_chunks_per_work': args.max_chunks,
            'sampling': args.sample,
            'seed': args.seed,
            'min_df': args.min_df,
            'max_df_ratio': args.max_df_ratio,
            'chunks': len(index),
            'works': len({r['work_stem'] for r in index}),
        }, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    print(f'[ok  ] 由来の記録 → {dest}')

    # 意図して外した行。**「メタデータに無い」と混ぜてはいけない。**
    # 行はあって，こちらが外したのだから，「追補せよ」は誤った指示である。
    if skipped:
        by_reason = defaultdict(list)
        for s_, why, lab_ in skipped:
            by_reason[why].append((s_, lab_))
        print(f'\n  [info ] メタデータで**意図的に外した**作品 '
              f'{len(skipped)} 件のトークン列を飛ばした')
        for why, items in sorted(by_reason.items()):
            print(f'         {why}: {EXCLUDED[why]}')
            for s_, lab_ in sorted(items):
                print(f'           {s_}  {lab_}')
        if 'merged' in by_reason:
            print('         **merged のトークン列が残っているのは消し忘れである。**')
            print('         03b_merge_volumes.py を走らせた後は，'
                  'data/plain と data/tokens を')
            print('         いったん空にしてから 04 → 05 を走らせ直すこと。')
            print('         古い巻別ファイルが残っていると，'
                  '統合前の巻がコーパスに混ざる。')

    # --- メタデータとの突合を検査する ------------------------------------
    # 一致しないまま進むと period も genre も空のチャンク索引ができ，
    # 以降の分析（時代別 keyness・doc2vec のカテゴリ効果・トピックの通時変化）
    # がすべて空振りする。**黙って通してはいけない工程である。**
    if unmatched:
        dest = os.path.join(args.out, 'meta_unmatched.csv')
        with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow(['work_stem', 'reason'])
            for u in sorted(unmatched):
                w.writerow([u, 'corpus_metadata_v2.csv に該当行がない'])
        print(f'\n  [warn] メタデータに無い作品が {len(unmatched)} 件ある。')
        print(f'         一覧 → {dest}')
        print('         これらのチャンクは period も genre も空になる。')
        print('         scripts/00_extend_metadata.py で追補してから，'
              'この工程をやり直すこと。')
        for u in sorted(unmatched)[:8]:
            print(f'           {u}')
        if len(unmatched) > 8:
            print(f'           …ほか {len(unmatched) - 8} 件')

    # 上限に当たった作品。**どのチャンクを捨てたかは結果に効く。**
    # chunks_index.csv の chunk_no を見れば選ばれた番号は分かるが，
    # 「何点が何チャンクから何チャンクに削られたか」は一覧で出す。
    if capped:
        print(f'\n  [info ] --max-chunks {args.max_chunks} の上限に当たった作品 '
              f'{len(capped)} 点（抽出方法: {args.sample}，seed {args.seed}）')
        for s_, tot, kept_n, a_, t_ in sorted(capped, key=lambda x: -x[1]):
            print(f'           {tot:>4} → {kept_n:<3} {a_}『{t_}』')
        print('           選ばれた番号は chunks_index.csv の chunk_no にある。')
        if args.sample in ('random', 'stratified'):
            print('           種は作品ごとに作るので，コーパスに作品を足しても'
                  '既存作品の標本は変わらない。')

    # 1チャンクにも満たない作品。**分析から落ちたことを黙っていてはいけない。**
    # 「コーパス 108 点」と書いた論文の統計が実は 105 点だった，という
    # ことになる。差し替えるか，チャンク長を下げるかは編者が決めること。
    if too_short:
        dest = os.path.join(args.out, 'works_too_short.csv')
        with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow(['work_stem', 'tokens', 'author_ja', 'title', 'reason'])
            for s, n_, a_, t_ in sorted(too_short):
                w.writerow([s, n_, a_, t_, f'--chunk {args.chunk} に満たない'])
        print(f'\n  [warn] **1チャンクに満たず，コーパスから落ちた作品が '
              f'{len(too_short)} 件ある**')
        print(f'         一覧 → {dest}')
        for s, n_, a_, t_ in sorted(too_short, key=lambda x: x[1]):
            print(f'           {s}  {n_:>6} 語  {a_}『{t_}』')
        print('         短い作品を残したいなら --chunk を下げること。ただし'
              'チャンク長を変えると')
        print('         トピックモデルも keyness も全作品で作り直しになる。'
              '差し替えるほうが普通は安い。')

    blank = sum(1 for r in index if not r.get('period'))
    if blank:
        share = blank / len(index)
        lv = '[FATAL]' if share > 0.5 else '[warn] '
        print(f'\n  {lv} period が空のチャンクが {blank:,} / {len(index):,} '
              f'（{share:.0%}）ある。')
        if share > 0.5:
            print('         メタデータとの突合がほぼ失敗している。'
                  'この状態で分析に進んではいけない。')

    # 時代別のチャンク数を表示して偏りを確認させる
    per = Counter(r['period'] or '（メタデータなし）' for r in index)
    print('\n  時代別チャンク数:')
    for k in sorted(per):
        bar = '#' * max(1, int(40 * per[k] / max(per.values())))
        print(f'    {k:<22} {per[k]:>5}  {bar}')
    if len(per) > 1:
        lo, hi = min(per.values()), max(per.values())
        print(f'\n  最小/最大 = {lo}/{hi} （比 {hi / lo:.1f}倍）')
        if hi / lo > 3:
            print('  [warn] 時代間の不均衡が大きい。--max-chunks で上限を設けるか，')
            print('         時代別モデルを学習する際に下位の時代へ合わせて抽出すること。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
07_descriptive_stats.py
=======================
記述統計と文体計量。word embedding やトピックモデルに進む前に，**素朴な頻度で
何が見えるか**を確定させておく。ここで見えないものが word embedding で見えたときは，
たいてい前処理の副作用である。

出力
----
``freq_matrix_mfw.csv``   最頻語 N 語 × 作品 の相対頻度行列（stylo と互換）
``work_profile.csv``      作品ごとの文体指標（TTR・平均文長・漢字率・会話率ほか）
``keyness_by_period.csv`` 時代ごとの特徴語（対数尤度比 G² と効果量，
                          および**散らばり（ディスパーション）**の列 df_all_prop / dp_gries /
                          top_work_share。culling と bursty 判定に使う）
``delta_matrix.csv``      Burrows's Delta 距離行列
``pca_coordinates.csv``   最頻語相対頻度の主成分得点（第1–4主成分）

列名の約束
----------
**割合は 0–1 で書き，名前の末尾を ``_prop`` / ``_ratio`` / ``_share`` にする**
（``df_all_prop`` = 0.1584 は 15.84 % の意）。0–100 の百分率を入れる列だけを
``_pct`` と綴る（このパイプラインでは ``14_unknown_profile.py`` の
``types_pct`` / ``tokens_pct`` のみ）。読み手が列を見ただけで尺度を決められる
ようにするための約束である。``scripts/check_units.py`` が機械的に検査している。

.. note::
   ``df_all_pct`` / ``df_in_pct`` は ``df_all_prop`` / ``df_in_prop`` の
   旧名である（中身は 0–1 なので per cent は誤称）。旧名の列を持つ CSV を
   読むノートブックは旧名を自動で読み替えるが，**07 を実行し直す
   のが正しい**。

使い方
------
    python3 07_descriptive_stats.py --tokens data/tokens/tokens_lemma \\
        --tsv data/tokens/tsv --meta metadata/corpus_metadata_v2.csv \\
        --out results/descriptive --mfw 300
"""
from __future__ import annotations

import argparse
import csv
import math
import os
from collections import Counter, defaultdict

import numpy as np


def stem_keys(r: dict) -> list[str]:
    """メタデータ1行から，トークンファイルの語幹になりうる綴りを**全部**返す。

    **ここを間違えると，突合が静かに失敗する。**
    青空文庫の索引の作品 ID は 0 埋めされていない（``1743``）が，
    本パイプラインのファイル名は6桁に 0 埋めしてある（``000119_001743``）。
    さらに ``corpus_metadata_v3.csv`` は，v1 由来の行では作品 ID が
    **0 埋めされていない**まま（``1743``）で，増補した行では
    **0 埋めされている**（``001504``）という混在状態にある。

    したがって ``f'{pid}_{wid}'`` と素朴に書くと，v1 由来の 62 点が
    1件も引けない。**エラーは出ない。** period も year_first も空のまま
    図が描かれ，多くの作品が「初出年不明」の灰色になる。両方を 0 埋めして
    照合し，念のため索引そのままの綴りと
    片側だけ 0 埋めした綴りもキーに登録する。
    """
    keys = []
    fv = (r.get('file_v1') or '').strip()
    if fv:
        keys.append(os.path.splitext(fv)[0])
    pid = (r.get('aozora_person_id') or '').strip()
    wid = (r.get('aozora_work_id') or '').strip()
    if pid and wid and pid.lower() != 'nan' and wid.lower() != 'nan':
        keys += [f'{pid.zfill(6)}_{wid.zfill(6)}',      # 本パイプラインの綴り
                 f'{pid}_{wid}',                        # 索引そのままの綴り
                 f'{pid.zfill(6)}_{wid}',               # 片側だけ 0 埋め
                 f'{pid}_{wid.zfill(6)}']
    return keys


def load_meta(path):
    with open(path, encoding='utf-8-sig') as fh:
        rows = list(csv.DictReader(fh))
    idx = {}
    for r in rows:
        for k in stem_keys(r):
            idx[k] = r
    return rows, idx


def log_likelihood(a: int, b: int, c: int, d: int) -> float:
    """Dunning の対数尤度比 G²。a,b=観測頻度, c,d=各コーパスの総語数。"""
    e1 = c * (a + b) / (c + d)
    e2 = d * (a + b) / (c + d)
    g = 0.0
    if a > 0:
        g += a * math.log(a / e1)
    if b > 0:
        g += b * math.log(b / e2)
    g *= 2
    return g if (a / c) > (b / d) else -g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tokens', required=True)
    ap.add_argument('--tsv', default=None, help='05 の tsv ディレクトリ（品詞情報用）')
    ap.add_argument('--meta', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--mfw', type=int, default=300)
    ap.add_argument('--min-ll', type=float, default=15.13,
                    help='特徴語の G² 下限（15.13 ≒ p<0.0001）')
    ap.add_argument('--cull-df', type=float, default=0.10, metavar='RATIO',
                    help='報告に使う culling の閾値（既定 0.10 ＝ 全作品の 10%%）。'
                         '**行を削除するのではなく，この閾値で何語が弾かれるかを'
                         '表示するだけ**。culling そのものは df_all_prop 列を'
                         '使って後の工程で行う')
    ap.add_argument('--allow-unmatched', action='store_true',
                    help='メタデータと突合できない作品が1割を超えても続ける'
                         '（既定は停止。period 別の集計が壊れるため）')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    rows_meta, meta = load_meta(args.meta)

    # ---- 頻度の収集 -------------------------------------------------------
    docs: dict[str, Counter] = {}
    lengths: dict[str, int] = {}
    for name in sorted(os.listdir(args.tokens)):
        if not name.endswith('.txt'):
            continue
        stem = name[:-4]
        toks = open(os.path.join(args.tokens, name), encoding='utf-8').read().split()
        docs[stem] = Counter(toks)
        lengths[stem] = len(toks)
    if not docs:
        raise SystemExit(f'トークンファイルが見つからない: {args.tokens}')

    total = Counter()
    for c in docs.values():
        total.update(c)
    mfw = [w for w, _ in total.most_common(args.mfw)]
    stems = sorted(docs)

    # ---- メタデータとの突合を**先に**確かめる -----------------------------
    # **これが無いと，突合が外れた作品が無言で除外される。**
    # 突合が外れた作品は period も year_first も空になる。すると
    #   * PCA の図で「初出年不明」の灰色になる
    #   * work_profile.csv の period 列が空になる
    #   * keyness_by_period.csv で「unknown」という架空の時代ができ，
    #     **その作品群が「時代の特徴語」の計算に混ざる**
    # どれもエラーにならない。数字は出るし図も描ける。だから
    # **出力を書く前に，ここで必ず件数を突き合わせる。**
    unmatched = [s for s in stems if s not in meta]
    if unmatched:
        share = len(unmatched) / len(stems)
        lv = '[FATAL]' if share > 0.10 else '[warn] '
        print(f'\n{lv} メタデータと突合できない作品が '
              f'{len(unmatched)} / {len(stems)} 点（{share:.0%}）ある。')
        for s in unmatched[:12]:
            print(f'         {s}')
        if len(unmatched) > 12:
            print(f'         …ほか {len(unmatched) - 12} 点')
        dest = os.path.join(args.out, 'meta_unmatched.csv')
        with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh)
            w.writerow(['work_stem', 'reason'])
            for s in unmatched:
                w.writerow([s, 'corpus_metadata に該当行がない（キーの綴り違いを疑う）'])
        print(f'         一覧 → {dest}')
        print('         キーの綴りを疑うこと。作品 ID の 0 埋めの有無が'
              'メタデータ内で混在している。')
        print('         stem_keys() の説明を読み，'
              '00_extend_metadata.py で v3 を作り直すのが早い。')
        if share > 0.10:
            print('         **この状態で出力を読んではいけない。**'
                  'period 別の集計がすべて壊れる。')
            print('         どうしても続けるなら --allow-unmatched を付ける'
                  '（報告にその旨を書くこと）。')
            if not args.allow_unmatched:
                return 1
    else:
        print(f'[ok  ] {len(stems)} 点すべてメタデータと突合できた')

    # ---- 最頻語相対頻度行列 ----------------------------------------------
    M = np.zeros((len(stems), len(mfw)))
    for i, s in enumerate(stems):
        n = max(1, lengths[s])
        for j, w in enumerate(mfw):
            M[i, j] = docs[s][w] / n
    with open(os.path.join(args.out, 'freq_matrix_mfw.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['work'] + mfw)
        for i, s in enumerate(stems):
            w.writerow([s] + [f'{x:.8f}' for x in M[i]])

    # ---- Burrows's Delta --------------------------------------------------
    mu, sd = M.mean(axis=0), M.std(axis=0)
    sd[sd == 0] = 1e-12
    Z = (M - mu) / sd
    D = np.abs(Z[:, None, :] - Z[None, :, :]).mean(axis=2)
    with open(os.path.join(args.out, 'delta_matrix.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow([''] + stems)
        for i, s in enumerate(stems):
            w.writerow([s] + [f'{x:.5f}' for x in D[i]])

    # ---- PCA --------------------------------------------------------------
    Zc = Z - Z.mean(axis=0)
    U, S, Vt = np.linalg.svd(Zc, full_matrices=False)
    # 主成分の数は，作品数・語数・4 のいずれか小さいほうに合わせる
    npc = int(min(4, len(S), len(stems), len(mfw)))
    scores = U[:, :npc] * S[:npc]
    var = (S ** 2) / max(1e-12, (S ** 2).sum())
    with open(os.path.join(args.out, 'pca_coordinates.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh)
        w.writerow(['work', 'id', 'author_ja', 'year_first', 'period', 'genre_sub',
                    'style_class'] + [f'PC{i + 1}' for i in range(npc)])
        for i, s in enumerate(stems):
            m = meta.get(s, {})
            w.writerow([s, m.get('id', ''), m.get('author_ja', ''),
                        m.get('year_first', ''), m.get('period', ''),
                        m.get('genre_sub', ''), m.get('style_class', '')]
                       + [f'{x:.5f}' for x in scores[i]])
    print('  PCA 寄与率: ' + ', '.join(f'PC{i + 1}={var[i]:.1%}' for i in range(npc)))

    # ---- 作品プロファイル -------------------------------------------------
    prof = []
    for s in stems:
        n = lengths[s]
        c = docs[s]
        m = meta.get(s, {})
        freqs = np.array(sorted(c.values(), reverse=True), dtype=float)
        p = freqs / n
        prof.append({
            'work': s, 'id': m.get('id', ''), 'author_ja': m.get('author_ja', ''),
            'year_first': m.get('year_first', ''), 'period': m.get('period', ''),
            'genre_sub': m.get('genre_sub', ''), 'style_class': m.get('style_class', ''),
            'tokens': n, 'types': len(c),
            'ttr': round(len(c) / n, 5),
            # 標本サイズに依存しにくい語彙多様性指標
            'guiraud_R': round(len(c) / math.sqrt(n), 3),
            'yule_K': round(1e4 * ((freqs ** 2).sum() - n) / (n ** 2), 3),
            'entropy_bits': round(float(-(p * np.log2(p)).sum()), 3),
            'hapax_ratio': round(sum(1 for v in c.values() if v == 1) / len(c), 4),
        })
    with open(os.path.join(args.out, 'work_profile.csv'), 'w',
              newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=list(prof[0].keys()))
        w.writeheader()
        w.writerows(prof)

    # ---- 時代別特徴語 -----------------------------------------------------
    # **時代が分からない作品は，時代の分析から外す。**
    # period を 'unknown' にまとめて集計に混ぜると，
    # 「unknown という時代の特徴語」という無意味な行ができるだけでなく，
    # **比較の相手（残り全部）にその作品群が入る**ので，他のすべての時代の
    # G² が静かにずれる。Step 6 の時代スライスと同じ規則で外すのが正しい。
    no_period = [s for s in stems
                 if not str(meta.get(s, {}).get('period', '') or '').strip()]
    if no_period:
        print(f'\n[warn] period が分からない作品 {len(no_period)} 点を'
              f'**時代別特徴語の集計から外す**（コーパス全体の集計には残す）:')
        for s in no_period[:8]:
            m = meta.get(s, {})
            lab = (f"{m.get('author_ja','?')}『{m.get('title_aozora','?')}』"
                   if m else '（メタデータなし）')
            print(f'         {s}  {lab}')
        if len(no_period) > 8:
            print(f'         …ほか {len(no_period) - 8} 点')
        print('       突合の失敗が原因なら，まず上の [FATAL]/[warn] を直すこと。')

    skip = set(no_period)
    incl = [s for s in stems if s not in skip]      # 時代別集計に入る作品
    by_period: dict[str, Counter] = defaultdict(Counter)
    len_period: dict[str, int] = defaultdict(int)
    period_of: dict[str, str] = {}
    for s in incl:
        p = str(meta[s]['period']).strip()
        period_of[s] = p
        by_period[p].update(docs[s])
        len_period[p] += lengths[s]
    grand_n = sum(len_period.values())
    if grand_n == 0:
        print('[FATAL] period を持つ作品が1点も無い。時代別特徴語は作れない。')
        return 1

    # **比較の相手（残り全部）も，時代別集計に入る作品だけで数える。**
    # コーパス全体の total を使って b = total[term] - a とすると，
    # 分子は外した作品の頻度を含むのに分母 n_rest は含まないという
    # ねじれが起き，すべての時代の G² が静かにずれる。
    total_incl = Counter()
    for s in incl:
        total_incl.update(docs[s])

    # ---- 語の散らばり（dispersion）----------------------------------------
    # **G² は「どこで多いか」を見ない。** 1作品に 300 回出て他の 100 作品に
    # 1回も出ない語と，101 作品に平均3回ずつ出る語は，総頻度が同じなら
    # ほぼ同じ G² を得る。前者は「その作品の語」であって「その時代の語」
    # ではない。両者を見分けるために，頻度と一緒に散らばりを測る。
    #
    #   df_all        その語を含む作品数（**時代別集計に入る作品の中で**）
    #   df_all_prop   その割合。**culling の閾値に使う**（例 df < 10% を弾く）
    #   df_in         その時代の中で，その語を含む作品数
    #   df_in_prop    その時代の作品数に対する割合
    #   top_work_share  総頻度のうち最も多い1作品が占める割合
    #   dp_gries      Gries (2008) の deviation of proportions（コーパス全体）
    #                 0 に近い＝均等に散らばる／1 に近い＝一点に固まる
    #   dp_in         **その時代の中での** DP。時代別の bursty 判定はこちら
    #                 （全体の DP は「時代に偏る」と「1作品に偏る」を
    #                  区別できない。dispersion_in の説明を読むこと）
    #
    # DP は「各作品にその語が何割あるか」と「各作品がコーパスの何割か」の
    # 差の総和の半分である。作品長の違いを織り込むので，長篇に偏った語を
    # 正しく割り引いて評価できる。df だけでは「多くの作品に少しずつ出るが，頻度の大半は1作に集中している」語を
    # 見落とす（df は 0/1 しか見ないため）。**両方を見ること。**
    # **散らばりも，時代別集計に入る作品（incl）の中で測る。**
    # G² の表と母集団を揃えるため。揃えないと「df 10% 未満」の 10% が
    # 何に対する 10% なのか分からなくなる。
    n_works = len(incl)
    len_share = {s: lengths[s] / max(1, grand_n) for s in incl}
    df_all: Counter = Counter()
    df_in_period: dict[str, Counter] = defaultdict(Counter)
    for s in incl:
        for t in docs[s]:
            df_all[t] += 1
            df_in_period[period_of[s]][t] += 1
    works_in_period = Counter(period_of[s] for s in incl)

    stems_of_period: dict[str, list] = defaultdict(list)
    for s in incl:
        stems_of_period[period_of[s]].append(s)

    _disp_cache: dict[str, tuple] = {}

    def dispersion(term: str) -> tuple:
        """``(dp_gries, top_work_share, top_work)`` を返す（incl の全作品）。"""
        if term in _disp_cache:
            return _disp_cache[term]
        tot = max(1, total_incl[term])
        dp = 0.0
        best, best_s = 0, ''
        for s in incl:
            f = docs[s][term]          # Counter なので未出現でも 0（追加しない）
            if f > best:
                best, best_s = f, s
            dp += abs(f / tot - len_share[s])
        val = (round(0.5 * dp, 4), round(best / tot, 4), best_s)
        _disp_cache[term] = val
        return val

    def dispersion_in(term: str, p: str) -> float:
        """**その時代の中での** DP。時代別特徴語の bursty 判定はこちらを見る。

        コーパス全体の DP（``dp_gries``）には落とし穴がある。ある時代だけに
        出る語は，たとえその時代の全作品に均等に出ていても，全体で見れば
        「偏っている」ので DP が 0.5 前後になる。ところが**その偏りは，
        まさに時代の特徴語として望ましい偏り**である。全体の DP は
        「時代に偏る」と「1作品に偏る」を区別できない。

        そこで分母をその時代の作品に限る。こちらが 0 に近ければ
        「その時代にまんべんなく広がる語」，1 に近ければ
        「その時代の数点（多くは1点）に固まる語」である。
        """
        ss = stems_of_period.get(p, [])
        tot_in = sum(docs[s][term] for s in ss)
        len_in = sum(lengths[s] for s in ss)
        if tot_in <= 0 or len_in <= 0:
            return 0.0
        dp = sum(abs(docs[s][term] / tot_in - lengths[s] / len_in) for s in ss)
        return round(0.5 * dp, 4)

    out_rows = []
    for p, c in by_period.items():
        n_p = len_period[p]
        n_rest = grand_n - n_p
        for term, a in c.items():
            if a < 10:
                continue
            b = total_incl[term] - a      # 分母 n_rest と母集団を揃える
            if b < 0:
                continue
            g = log_likelihood(a, b, n_p, max(1, n_rest))
            if abs(g) < args.min_ll:
                continue
            rf_p = a / n_p * 1e6
            rf_r = b / max(1, n_rest) * 1e6
            dp, top_share, top_work = dispersion(term)
            n_in = max(1, works_in_period[p])
            out_rows.append({
                'period': p, 'term': term, 'freq_in': a, 'freq_out': b,
                'pm_in': round(rf_p, 2), 'pm_out': round(rf_r, 2),
                'log_ratio': round(math.log2((rf_p + 0.01) / (rf_r + 0.01)), 3),
                'G2': round(g, 2),
                # 以下は散らばり。culling と bursty/even の判定に使う
                'df_all': df_all[term],
                'df_all_prop': round(df_all[term] / max(1, n_works), 4),
                'df_in': df_in_period[p][term],
                'df_in_prop': round(df_in_period[p][term] / n_in, 4),
                'dp_gries': dp,
                'dp_in': dispersion_in(term, p),
                'top_work_share': top_share,
                'top_work': top_work,
            })
    out_rows.sort(key=lambda r: (r['period'], -r['G2']))
    if out_rows:
        with open(os.path.join(args.out, 'keyness_by_period.csv'), 'w',
                  newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)
    else:
        print('  [warn] 閾値を超える特徴語がない。--min-ll を下げるか語数を増やすこと')

    print(f'\n[ok  ] {len(stems)} 作品 / MFW {len(mfw)} 語')
    cut = args.cull_df
    for p in sorted(by_period):
        rows_p = [r for r in out_rows if r['period'] == p and r['G2'] > 0]
        top = [r['term'] for r in rows_p[:12]]
        print(f'  {p:<22} {len_period[p]:>9,}語  特徴語: {" ".join(top)}')
        # **上位語のうち何語が「一部の作品にしか出ない語」か**を出す。
        # ここが大きい時代の特徴語リストは，時代の特徴ではなく
        # 数点の作品の語彙を映している。
        t15 = rows_p[:15]
        if t15:
            thin = [r['term'] for r in t15 if r['df_all_prop'] < cut]
            print(f'{"":24}上位15語のうち df<{cut:.0%} が {len(thin):>2} 語'
                  + (f'（{" ".join(thin[:8])}）' if thin else ''))
    print(f'\n[ok  ] 出力 → {args.out}')
    print('      注意：特徴語は作家効果を含む。1作家に偏る時代では，その作家固有の')
    print('      語（人名・地名）が上位に来る。keyness_by_period.csv を必ず目視すること。')
    print(f'      keyness_by_period.csv には散らばりの列がある'
          f'（df_all_prop / dp_gries / top_work_share）。')
    print(f'      df_all_prop < {cut:.0%} を弾いた場合との比較は Step 4 のノートブック。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

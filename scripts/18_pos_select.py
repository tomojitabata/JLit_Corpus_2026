#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
18_pos_select.py
================
**品詞と頻度帯で語を選び，トピックモデル用のトークン列を作る**（Step 8）。

05 の ``tokens_content`` は「名詞・動詞・形容詞・副詞」をすべて残す。
そこには**固有名詞**（登場人物・地名）と，意味の薄い**非自立の動詞**
（為る・居る・成る）や**副詞**が入る。トピックモデルでは

* 固有名詞は**1作品にしか出ない**ので，トピックが「作品の目印」になる
  （「三吉・お種・正太」のトピック＝『家』）。主題ではなく作品を見分けてしまう
* 為る・居る・有る は**どの作品にも大量に出る**ので，どのトピックの上位にも出る

⚠ **品詞だけでは固有名詞を除き切れない。** UniDic は辞書に無い人名を
普通名詞や形状詞と解析することがある（『青年』の「純一」，『坊っちゃん』の
「山嵐」，『黒死館殺人事件』の「法水」）。こうした語は**1作品に集中する**ので，
``--max-work-share``（1作品が度数に占める割合の上限）で除外できる。
同じ作家の複数の作品に出る人物名（宮本百合子の「素子」）は1作品に集中しないので，
``--max-author-share``（1作家が度数に占める割合の上限）で除外する。

**bursty な語**（少数の作品に固まって出る語）は Gries (2008) の DP でも除外できる。
DP は「各作品にその語が何割あるか」と「各作品がコーパスの何割か」の差の
総和の半分で，0＝均等に散らばる，1＝一点に固まる。Step 4 の 07 と同じ定義で

    dp      コーパス全体の DP
    dp_in   **その語がいちばん濃い時代の中での** DP（07 の dp_in と同じ考え方）

の2つを出す。全体の dp は「ある時代に偏る」語まで不利に扱ってしまう。時代の主題を
見たいトピックモデルでは，**時代への偏りは残し，作品への偏りだけを除きたい**
ので，絞り込みには ``--max-dp-in`` を使う。

ここでは 05 が書いた ``data/tokens/tsv/`` の品詞情報（UniDic の pos1–pos3）を
読み，**指定した品詞だけ**を残した語彙素の列を作る。頻度帯（全体の度数・
作品の何割に出るか）でも絞れる。出力は 05 の ``tokens_*`` と同じ形式なので，
そのまま 06_build_datasets.py → 10_mallet.py に渡せる。

品詞の指定
----------
``pos1-pos2-pos3`` の**前方一致**で書く（UniDic の品詞体系）。

    名詞-普通名詞          普通名詞すべて（一般・サ変可能・副詞可能…）
    名詞-普通名詞-一般     普通名詞のうち「一般」だけ
    動詞-一般              自立動詞（言う・思う・歩く）
    動詞-非自立可能        為る・居る・有る・成る・来る・見る
    名詞-固有名詞          人名・地名・組織名

プリセット（``--profile``）
    nva      名詞-普通名詞，動詞-一般，形容詞-一般     ← 既定
             （固有名詞・数詞・代名詞・非自立の動詞と形容詞・副詞・形状詞を除く）
    nva+     nva に 形状詞-一般（静か・立派），動詞-非自立可能，形容詞-非自立可能 を足す
    content  名詞，動詞，形容詞，副詞（05 の tokens_content と同じ。固有名詞を含む）

使い方
------
    python3 scripts/18_pos_select.py --profile nva
    #  → data/tokens/tokens_nva/  と  data/tokens/lexicon.tsv

    # 頻度帯でも絞る：全体で 5 回未満の語と，作品の 60% 超に出る語を除く
    python3 scripts/18_pos_select.py --profile nva --min-count 5 --max-doc-ratio 0.6

    # 1作品に度数の 8 割以上が集中する語も除く（品詞解析が普通名詞と誤った
    # 登場人物名＝「純一」「山嵐」「法水」などを除く）
    python3 scripts/18_pos_select.py --profile nva --max-work-share 0.8

    # bursty な語を除く：いちばん濃い時代の中で DP が 0.8 を超える語
    python3 scripts/18_pos_select.py --profile nva --max-dp-in 0.8 --name nva_dp80

    # 品詞を直接指定する（--exclude は --pos の中からさらに除く）
    python3 scripts/18_pos_select.py --name nva_noadv \\
        --pos 名詞-普通名詞 動詞-一般 形容詞-一般 --exclude 名詞-普通名詞-副詞可能

出力
----
``<tokens の親>/tokens_<name>/<作品>.txt``  選んだ語彙素の列（空白区切り）
``<tokens の親>/tokens_<name>/selection.json``  何をどう選んだかの記録
``<tokens の親>/lexicon.tsv``  語彙素ごとの代表品詞・度数・出現作品数・1作品への集中度・
                               DP（全体／いちばん濃い時代の中）（全語。
                               トピックビューア 19 が品詞と頻度帯の絞り込みに使う）
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_meta() -> str:
    base = os.path.join(ROOT, 'metadata')
    for name in ('corpus_metadata_v3_local.csv', 'corpus_metadata_v3.csv',
                 'corpus_metadata_v2.csv'):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return ''


def load_periods(path: str) -> tuple[dict[str, str], dict[str, str]]:
    """作品の語幹 → 時代，作品の語幹 → 作家。0 埋めの揺れを両方登録する。"""
    per_of, au_of = {}, {}
    if not path or not os.path.exists(path):
        return per_of, au_of
    with open(path, encoding='utf-8-sig') as fh:
        for r in csv.DictReader(fh):
            pid = str(r.get('aozora_person_id') or '').strip()
            wid = str(r.get('aozora_work_id') or '').strip()
            if not (pid.isdigit() and wid.isdigit()):
                continue
            for k in (f'{pid.zfill(6)}_{wid.zfill(6)}', f'{pid}_{wid}'):
                if r.get('period'):
                    per_of.setdefault(k, r['period'])
                au_of.setdefault(k, r.get('author_ja') or pid)
    return per_of, au_of

PROFILES = {
    'nva': {'pos': ['名詞-普通名詞', '動詞-一般', '形容詞-一般'], 'exclude': []},
    'nva+': {'pos': ['名詞-普通名詞', '動詞-一般', '形容詞-一般', '形状詞-一般',
                     '動詞-非自立可能', '形容詞-非自立可能'], 'exclude': []},
    'content': {'pos': ['名詞', '動詞', '形容詞', '副詞'], 'exclude': []},
}

# TSV の列（05_tokenise_unidic.py の COLUMNS と同じ）
C_LEMMA_KEY, C_POS1, C_POS2, C_POS3 = 2, 6, 7, 8


def pos_label(row: list[str]) -> str:
    return '-'.join(p for p in (row[C_POS1], row[C_POS2], row[C_POS3]) if p)


def matches(label: str, pats: list[str]) -> bool:
    """前方一致。'名詞-普通名詞' は '名詞-普通名詞-一般' に当たるが '名詞-普通' には当てない。"""
    for p in pats:
        if label == p or label.startswith(p + '-'):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--tsv', default=os.path.join(ROOT, 'data', 'tokens', 'tsv'),
                    help='05 の出力の tsv/ ディレクトリ')
    ap.add_argument('--profile', choices=sorted(PROFILES), default=None,
                    help='品詞のプリセット（既定 nva。--pos を与えたときは無視）')
    ap.add_argument('--pos', nargs='+', default=None, help='残す品詞（前方一致）')
    ap.add_argument('--exclude', nargs='+', default=[], help='--pos からさらに除く品詞')
    ap.add_argument('--min-count', type=int, default=1,
                    help='全体の度数がこれ未満の語を除く（既定 1＝除かない）')
    ap.add_argument('--max-doc-ratio', type=float, default=1.0,
                    help='これを超える割合の作品に出る語を除く（既定 1.0＝除かない）')
    ap.add_argument('--max-work-share', type=float, default=1.0,
                    help='度数のうち1作品が占める割合がこれを超える語を除く'
                         '（登場人物名の取りこぼし対策。例 0.8。既定 1.0＝除かない）')
    ap.add_argument('--max-author-share', type=float, default=1.0,
                    help='度数のうち1作家が占める割合がこれを超える語を除く'
                         '（複数作品にまたがる人物名。例 0.9。既定 1.0＝除かない）')
    ap.add_argument('--max-dp-in', type=float, default=1.0,
                    help='いちばん濃い時代の中での Gries の DP がこれを超える語を除く'
                         '（bursty な語。例 0.8。既定 1.0＝除かない）')
    ap.add_argument('--meta', default=None,
                    help='時代を引くメタデータ（dp_in に使う。既定 *_v3_local.csv > v3 > v2）')
    ap.add_argument('--name', default=None,
                    help='出力名（tokens_<name>）。既定はプロファイル名')
    ap.add_argument('--out', default=None,
                    help='出力の親ディレクトリ（既定は --tsv の親＝data/tokens）')
    args = ap.parse_args()

    if not os.path.isdir(args.tsv):
        sys.exit(f'TSV が無い: {args.tsv}\n  05_tokenise_unidic.py を先に実行すること。')
    if args.pos:
        pos, exclude, name = args.pos, list(args.exclude), args.name or 'custom'
        profile = None
    else:
        profile = args.profile or 'nva'
        pos = PROFILES[profile]['pos']
        exclude = PROFILES[profile]['exclude'] + list(args.exclude)
        name = args.name or profile.replace('+', 'plus')
    parent = args.out or os.path.dirname(os.path.normpath(args.tsv))
    dest = os.path.join(parent, f'tokens_{name}')
    os.makedirs(dest, exist_ok=True)

    files = sorted(f for f in os.listdir(args.tsv) if f.endswith('.tsv'))
    if not files:
        sys.exit(f'TSV が1つも無い: {args.tsv}')

    # ---- 1回目：全語の品詞・度数・出現作品数（lexicon）と，選んだ列 --------
    count = Counter()                       # 全語の度数
    docs = Counter()                        # 全語の出現作品数
    pos_of = defaultdict(Counter)           # 語彙素 → 品詞ラベルの度数
    top_in_work = Counter()                 # 語彙素 → 1作品での最大度数
    top_work = {}                           # 語彙素 → その作品
    selected: dict[str, list[str]] = {}
    per_work: dict[str, Counter] = {}       # 作品 → 語の度数（DP 用）
    lengths: dict[str, int] = {}
    header = ''
    for f in files:
        seen = set()
        here = Counter()
        seq = []
        with open(os.path.join(args.tsv, f), encoding='utf-8-sig') as fh:
            first = fh.readline()
            if first.startswith('#') and not header:
                header = first.strip('# \n')
            rd = csv.reader(fh, delimiter='\t')
            for row in rd:
                if len(row) <= C_POS3 or row[0] in ('', 'i') or row[C_POS1] == 'EOS':
                    continue
                lk = row[C_LEMMA_KEY]
                if not lk:
                    continue
                lab = pos_label(row)
                count[lk] += 1
                here[lk] += 1
                pos_of[lk][lab] += 1
                if lk not in seen:
                    seen.add(lk)
                    docs[lk] += 1
                if matches(lab, pos) and not matches(lab, exclude):
                    seq.append(lk)
        selected[f[:-4]] = seq
        per_work[f[:-4]] = here
        lengths[f[:-4]] = sum(here.values())
        for lk, c in here.items():
            if c > top_in_work[lk]:
                top_in_work[lk] = c
                top_work[lk] = f[:-4]
    n_docs = len(files)

    # ---- 散らばり（Gries の DP）--------------------------------------------
    period_of, author_of = load_periods(args.meta or default_meta())
    stems = list(per_work)
    L = sum(lengths.values())
    share = {s: lengths[s] / L for s in stems}
    by_period: dict[str, list[str]] = defaultdict(list)
    for s in stems:
        by_period[period_of.get(s, '不明')].append(s)
    plen = {p: sum(lengths[s] for s in ss) for p, ss in by_period.items()}
    pset = {p: set(ss) for p, ss in by_period.items()}
    where = defaultdict(list)                # 語 → [(作品, 度数)]
    for s, c in per_work.items():
        for w, n in c.items():
            where[w].append((s, n))
    dp, dp_in, peak, au_share = {}, {}, {}, {}
    for w, occ in where.items():
        tot = count[w]
        ac = Counter()
        for s, n in occ:
            ac[author_of.get(s, s)] += n
        au_share[w] = max(ac.values()) / tot
        seen_share = sum(share[s] for s, _ in occ)
        # 出ない作品は |0 − share| を足すので，その分をまとめて足す
        dp[w] = 0.5 * (sum(abs(n / tot - share[s]) for s, n in occ) + (1 - seen_share))
        pc = Counter()
        for s, n in occ:
            pc[period_of.get(s, '不明')] += n
        p = max(pc, key=lambda q: pc[q] / plen[q])
        peak[w] = p
        occ_p = [(s, n) for s, n in occ if s in pset[p]]
        tot_p = pc[p]
        in_share = sum(lengths[s] for s, _ in occ_p) / plen[p]
        dp_in[w] = 0.5 * (sum(abs(n / tot_p - lengths[s] / plen[p]) for s, n in occ_p)
                          + (1 - in_share))
    if not period_of:
        print('[warn] メタデータから時代が引けない。dp_in はコーパス全体の DP と同じになる')

    # ---- 頻度帯 -------------------------------------------------------------
    drop_low = {w for w, c in count.items() if c < args.min_count}
    drop_high = {w for w, d in docs.items() if d / n_docs > args.max_doc_ratio}
    drop_work = {w for w, c in count.items() if top_in_work[w] / c > args.max_work_share}
    drop_dp = {w for w, v in dp_in.items() if v > args.max_dp_in}
    drop_au = {w for w, v in au_share.items() if v > args.max_author_share}
    drop = drop_low | drop_high | drop_work | drop_dp | drop_au

    total_in = total_out = 0
    kept_types = set()
    for stem, seq in selected.items():
        out = [w for w in seq if w not in drop]
        total_in += len(seq)
        total_out += len(out)
        kept_types.update(out)
        with open(os.path.join(dest, stem + '.txt'), 'w', encoding='utf-8') as fh:
            fh.write(' '.join(out) + '\n')

    # ---- lexicon.tsv（全語。ビューアが使う）----------------------------------
    lex = os.path.join(parent, 'lexicon.tsv')
    with open(lex, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.writer(fh, delimiter='\t', lineterminator='\n')
        w.writerow(['lemma', 'pos', 'pos_share', 'count', 'docs', 'doc_ratio',
                    'top_work', 'top_work_share', 'dp', 'dp_in', 'peak_period',
                    'top_author_share'])
        for lk, c in count.most_common():
            lab, lc = pos_of[lk].most_common(1)[0]
            w.writerow([lk, lab, f'{lc / c:.3f}', c, docs[lk], f'{docs[lk] / n_docs:.3f}',
                        top_work[lk], f'{top_in_work[lk] / c:.3f}',
                        f'{dp[lk]:.3f}', f'{dp_in[lk]:.3f}', peak[lk],
                        f'{au_share[lk]:.3f}'])

    # ---- 記録 --------------------------------------------------------------
    by_pos = Counter()
    for stem, seq in selected.items():
        for wd in seq:
            if wd not in drop:
                by_pos[pos_of[wd].most_common(1)[0][0]] += 1
    rec = {
        'name': name, 'profile': profile, 'pos': pos, 'exclude': exclude,
        'min_count': args.min_count, 'max_doc_ratio': args.max_doc_ratio,
        'max_work_share': args.max_work_share, 'dropped_work_concentrated': len(drop_work),
        'max_dp_in': args.max_dp_in, 'dropped_bursty_dp_in': len(drop_dp),
        'max_author_share': args.max_author_share, 'dropped_author_concentrated': len(drop_au),
        'source_tsv': os.path.abspath(args.tsv), 'tokenise': header,
        'works': n_docs, 'tokens_selected': total_in, 'tokens_kept': total_out,
        'types_kept': len(kept_types),
        'dropped_low_freq': len(drop_low), 'dropped_high_doc_ratio': sorted(drop_high)[:500],
        'kept_by_pos': dict(by_pos.most_common()),
    }
    with open(os.path.join(dest, 'selection.json'), 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    # 06 の辞書照合が tokens_* の親の tokenise_provenance.json を見るので，
    # --out で別の場所に書いたときはそれも写しておく
    src_prov = os.path.join(os.path.dirname(os.path.normpath(args.tsv)),
                            'tokenise_provenance.json')
    dst_prov = os.path.join(parent, 'tokenise_provenance.json')
    if os.path.exists(src_prov) and not os.path.exists(dst_prov):
        with open(src_prov, encoding='utf-8') as a, open(dst_prov, 'w', encoding='utf-8') as b:
            b.write(a.read())

    print(f'[ok  ] {dest}')
    print(f'       品詞: {", ".join(pos)}' + (f'（除く: {", ".join(exclude)}）' if exclude else ''))
    print(f'       {n_docs} 作品・{total_out:,} 語（品詞で選んだ {total_in:,} 語から'
          f'頻度帯で {total_in - total_out:,} 語を除いた）・異なり {len(kept_types):,} 語')
    if drop_work:
        print(f'       1作品に {args.max_work_share:.0%} 超が集中するので除いた語: {len(drop_work):,} 語（例: '
              + '，'.join(sorted(drop_work, key=lambda x: -count[x])[:15]) + '）')
    if drop_au:
        print(f'       1作家に {args.max_author_share:.0%} 超が集中するので除いた語: {len(drop_au):,} 語（例: '
              + '，'.join(sorted(drop_au, key=lambda x: -count[x])[:15]) + '）')
    if drop_dp:
        print(f'       bursty（dp_in > {args.max_dp_in}）なので除いた語: {len(drop_dp):,} 語（例: '
              + '，'.join(sorted(drop_dp, key=lambda x: -count[x])[:15]) + '）')
    if drop_high:
        print(f'       作品の {args.max_doc_ratio:.0%} 超に出るので除いた語: '
              + '，'.join(sorted(drop_high, key=lambda x: -count[x])[:20])
              + (' …' if len(drop_high) > 20 else ''))
    print('       品詞の内訳: ' + '，'.join(f'{k} {v / max(1, total_out):.1%}'
                                     for k, v in by_pos.most_common(6)))
    print(f'[ok  ] {lex}（全 {len(count):,} 語の品詞・度数・出現作品数）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

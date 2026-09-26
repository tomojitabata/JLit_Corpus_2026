#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_balance.py
================
**コーパスの構成が設計目標に達しているか**を見る。

なぜ要るのか
------------
「バランスの良いコーパス」は，数値にしない限り検証できない。どの作家が
何割まで許されるのかを決めずに増補すると，**増やしたことで却って偏る**。

2026-09-26 の点検では，分析対象 101 点・621 万語のうち
宮本百合子 12.1%・森鴎外 11.8% に対して夏目漱石は 4.7% であった。
また昭和戦後は語数の 50% が宮本百合子1人，59% が女性で，
**性別と時代が交絡していた**。数えなければどれも見えない。

目標は ``config/design_targets.yaml`` に書く。ここはその表と現状を
突き合わせるだけで，判断は持たない。

⚠ **達していない項目は「誤り」ではない。** 青空文庫に無いものは入らない
（保護期間中の作家，未登録の作品）。埋まらない穴を**穴として報告し続ける**
のがこの検査の役目である。論文や授業で制約として明示するために使う。

数える行
--------
``completeness`` が ``superseded`` / ``merged`` / ``too_short`` の行は
落とす（合本と分冊を両方数えると同じ本文を二重に数える）。
ノートブックの ``load_meta()`` と同じ規則である。

使い方
------
    python3 scripts/check_balance.py
    python3 scripts/check_balance.py --quiet       # 達していない項目だけ
    python3 scripts/check_balance.py --meta metadata/corpus_metadata_v3_local.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

OK, WARN, NG, INFO = '[ok  ]', '[warn]', '[NG  ]', '[info ]'
ROOT = Path(__file__).resolve().parent.parent
DROP = {'superseded', 'merged', 'too_short'}


def load_targets(path: Path) -> dict:
    try:
        import yaml                                        # type: ignore
    except ImportError:
        raise SystemExit('pyyaml が要る: uv add pyyaml（または pip install pyyaml）')
    with open(path, encoding='utf-8-sig') as fh:           # BOM 付きでも読む
        return yaml.safe_load(fh) or {}


def load_rows(meta: Path) -> list[dict]:
    with open(meta, encoding='utf-8-sig') as fh:
        rows = [r for r in csv.DictReader(fh)
                if (r.get('completeness') or '') not in DROP]
    if not rows:
        raise SystemExit(f'{meta} に数えられる行が無い')
    return rows


def tok(r: dict) -> int:
    try:
        return int(float(r.get('tokens') or 0))
    except ValueError:
        return 0


def band_of(r: dict, targets: dict) -> str:
    """学習に使う区分。列が無ければ目標表の対応表で畳む。"""
    col = targets.get('bands', {}).get('slice_column', 'period5')
    v = (r.get(col) or '').strip()
    if v:
        return v
    m = targets.get('bands', {}).get('bands5', {}) or {}
    p = (r.get('period') or '').strip()
    return m.get(p, p)


def bar(x: float, limit: float, width: int = 18) -> str:
    """目標に対する現在値を棒で示す。``|`` が上限の位置。"""
    n = min(width, max(0, round(width * x / max(limit, 1e-9))))
    return ('█' * n).ljust(width, '·')


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--meta', default=None,
                    help='既定: metadata/ の *_v3_local.csv > v3')
    ap.add_argument('--targets', default=str(ROOT / 'config' / 'design_targets.yaml'))
    ap.add_argument('--quiet', action='store_true', help='達していない項目だけ')
    args = ap.parse_args()

    meta = args.meta
    if meta is None:
        for cand in ('corpus_metadata_v3_local.csv', 'corpus_metadata_v3.csv'):
            p = ROOT / 'metadata' / cand
            if p.exists():
                meta = str(p)
                break
    if not meta or not os.path.exists(meta):
        raise SystemExit('メタデータが無い。00_extend_metadata.py で v3 を作ること。')

    T = load_targets(Path(args.targets))
    rows = load_rows(Path(meta))
    total = sum(tok(r) for r in rows)
    miss: list[str] = []

    def say(s: str = '') -> None:
        if not args.quiet:
            print(s)

    say(f'メタデータ : {meta}')
    say(f'分析対象   : {len(rows)} 点 / {total:,} 語'
        f'（目標表 {T.get("meta", {}).get("decided", "?")} 版）\n')

    # ---- 1. 作家と作品の集中 --------------------------------------------
    con = T.get('concentration', {})
    lim_a = float(con.get('max_author_share', 1))
    lim_w = float(con.get('max_work_share', 1))
    au = defaultdict(int)
    for r in rows:
        au[r.get('author_ja', '')] += tok(r)
    say(f'■ 作家の集中（上限 {lim_a:.0%}）')
    for a, n in sorted(au.items(), key=lambda x: -x[1])[:8]:
        s = n / total
        mark = '←上限超過' if s > lim_a else ''
        say(f'   {a:<10}{bar(s, lim_a)} {s:6.1%} {mark}')
        if s > lim_a:
            miss.append(f'{a} が語数の {s:.1%}（上限 {lim_a:.0%}）')
    say()
    say(f'■ 作品の集中（上限 {lim_w:.0%}）')
    for r in sorted(rows, key=lambda r: -tok(r))[:5]:
        s = tok(r) / total
        mark = '←上限超過' if s > lim_w else ''
        say(f'   {r.get("author_ja", ""):<8}『{r.get("title_aozora", "")}』'
            f' {s:5.1%} {mark}')
        if s > lim_w:
            miss.append(f'『{r.get("title_aozora", "")}』が語数の {s:.1%}'
                        f'（上限 {lim_w:.0%}）')
    say()

    # ---- 2. 時代区分ごとの量と偏り ---------------------------------------
    bands = T.get('bands', {})
    lim_min = int(bands.get('min_tokens_per_band', 0))
    lim_ratio = float(bands.get('max_min_ratio', 99))
    lim_in = float(con.get('max_author_share_in_band', 1))
    b_tok: Counter = Counter()
    b_cnt: Counter = Counter()
    b_au: dict[str, Counter] = defaultdict(Counter)
    b_f: dict[str, set] = defaultdict(set)
    for r in rows:
        k = band_of(r, T)
        b_tok[k] += tok(r)
        b_cnt[k] += 1
        b_au[k][r.get('author_ja', '')] += tok(r)
        if (r.get('author_sex') or '') == 'F':
            b_f[k].add(r.get('author_ja', ''))
    say(f'■ 時代区分（{bands.get("slice_column", "period5")}。'
        f'下限 {lim_min:,} 語／最大最小 {lim_ratio:.1f} 倍以内／'
        f'区分内の最大作家 {lim_in:.0%} 以内／女性作家 '
        f'{T.get("sex", {}).get("min_female_authors_per_band", 0)} 人以上）')
    min_fa = int(T.get('sex', {}).get('min_female_authors_per_band', 0))
    for k in sorted(b_tok):
        n = b_tok[k]
        top, tn = b_au[k].most_common(1)[0]
        fs = len(b_f[k])
        flags = []
        if n < lim_min:
            flags.append('語数不足')
            miss.append(f'{k} が {n:,} 語（下限 {lim_min:,}）')
        if tn / n > lim_in:
            flags.append(f'{top} 偏重')
            miss.append(f'{k} の {tn/n:.0%} が {top}（上限 {lim_in:.0%}）')
        if fs < min_fa:
            flags.append('女性作家不足')
            miss.append(f'{k} の女性作家が {fs} 人（下限 {min_fa} 人）')
        say(f'   {k:<26}{b_cnt[k]:>3}点 {n:>9,}語  '
            f'最大 {top} {tn/n:4.0%}  女性作家{fs}人  '
            + ('／'.join(flags) if flags else 'ok'))
    if b_tok:
        ratio = max(b_tok.values()) / max(1, min(b_tok.values()))
        ok = ratio <= lim_ratio
        say(f'   最大/最小 = {ratio:.1f} 倍' + ('' if ok else f'（上限 {lim_ratio:.1f}）'))
        if not ok:
            miss.append(f'区分の語数比が {ratio:.1f} 倍（上限 {lim_ratio:.1f}）')
    say()

    # ---- 2b. 時代区分と初出年の整合 ---------------------------------------
    # period は year_first の関数である。**食い違っていても例外は出ない。**
    # 2026-09-26 に，編者が初出年を直したのに区分が古いままだった行が
    # 2 件見つかった（有島武郎『或る女』・水野仙子『四十余日』）。
    # 00_extend_metadata.py が毎回引き直すが，表を手で直したときのために
    # ここでも見る。
    def _band(y: str) -> str:
        m = re.match(r'\s*(\d{4})', str(y or ''))
        if not m:
            return ''
        n = int(m.group(1))
        for lim, name in ((1887, '1_明治前期(〜1886)'), (1900, '2_明治中期(1887-1899)'),
                          (1912, '3_明治後期(1900-1911)'), (1926, '4_大正(1912-1925)'),
                          (1945, '5_昭和戦前(1926-1944)')):
            if n < lim:
                return name
        return '6_昭和戦後(1945-)'
    odd = [r for r in rows
           if _band(r.get('year_first')) and r.get('period') != _band(r.get('year_first'))]
    if odd:
        say('■ 時代区分と初出年の食い違い')
        for r in odd[:10]:
            say(f"   {r.get('author_ja','')}『{r.get('title_aozora','')}』"
                f"{r.get('year_first','')}年  表の区分 {r.get('period','')}"
                f"  → 年から引くと {_band(r.get('year_first'))}")
            miss.append(f"{r.get('title_aozora','')} の period が初出年と食い違う")
        say()

    # ---- 3. 作者の性別 ----------------------------------------------------
    sex = T.get('sex', {})
    f_tok = sum(tok(r) for r in rows if (r.get('author_sex') or '') == 'F')
    f_share = f_tok / total if total else 0
    lim_f = float(sex.get('min_female_share', 0))
    say(f'■ 作者の性別（女性の語数比 下限 {lim_f:.0%}）')
    say(f'   女性 {f_share:.1%}（{sum(1 for r in rows if r.get("author_sex") == "F")} 点，'
        f'{len({r["author_ja"] for r in rows if r.get("author_sex") == "F"})} 人）')
    if f_share < lim_f:
        miss.append(f'女性の語数比が {f_share:.1%}（下限 {lim_f:.0%}）')
    say()

    # ---- 4. 主要作家の点数 ------------------------------------------------
    want = (T.get('authors', {}) or {}).get('canonical_min_works', {}) or {}
    if want:
        say('■ 主要作家の点数')
        have = Counter(r.get('author_ja', '') for r in rows)
        for a, n in want.items():
            got = have.get(a, 0)
            say(f'   {a:<10}{got:>3} 点 / 目標 {n} 点'
                + ('' if got >= n else f'  ←あと {n - got} 点'))
            if got < n:
                miss.append(f'{a} が {got} 点（目標 {n} 点）')
        say()

    # ---- まとめ -----------------------------------------------------------
    if miss:
        print(f'{WARN} 目標に達していない項目が {len(miss)} 件:')
        for m in miss:
            print(f'       ・{m}')
        print('       青空文庫に無いものは入らない。埋まらない項目は'
              '**制約として明示すること**（docs/corpus_design.md）。')
        print('       収録で下げきれない集中は，分析時に均す: '
              '06 --balance --max-per-author ／ 08 --balance')
    else:
        print(f'{OK} 設計目標をすべて満たしている')
    # 目標未達は「誤り」ではないので 0 を返す。CI で止めたいときは --strict を足すこと
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

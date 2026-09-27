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

    # **リバランスがどの程度効いたか**を棒グラフ（SVG）で出す
    python3 scripts/check_balance.py --fig results/figures
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


def draw_rebalance(rows: list[dict], targets: dict, out_dir: str,
                   meta_path: str) -> str:
    """**リバランスがどの程度効いたか**を棒グラフで示す。

    比べるのは「増補前」＝ ``set=core``（v1 由来の行）と「増補後」＝全体。
    **どちらも再構築後のトークン列で測った値**なので，見えるのは構成の
    違いだけである。v1 のメタデータ（``corpus_metadata_v2.csv``）と
    比べると，外字欠落・奥付混入による測定の違いが混ざって読めなくなる。

    語数そのものではなく**シェア（％）**で描く。総語数が 4.9M と 8.1M で
    違うのだから，絶対量を並べると「増えた」しか分からない。**問いは
    「偏りが均んだか」**である。

    時代区分は目標表の ``slice_column``（既定 ``period5``）に合わせる。
    設計目標がその区分で書かれているので，図と文字の出力が食い違わない。
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('[skip] matplotlib が無いので図は描かない')
        return ''
    # 図の作法（SVG・日本語フォント・パレット）は 11_visualise.py に一本化
    # してある。数字を出す側で別の色を使い始めると，同じ授業の図が
    # 揃わなくなる。
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        '_vis', os.path.join(str(ROOT), 'scripts', '11_visualise.py'))
    vis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vis)
    vis.setup_japanese_font()
    vis.setup_svg()
    C_BEFORE, C_AFTER = vis.PALETTE[0], vis.PALETTE[1]
    INK, MUTED, GRID = '#0b0b0b', '#52514e', '#e6e6e3'

    core = [r for r in rows if (r.get('set') or '').strip() == 'core']
    if not core:
        print('[skip] set 列に core が無いので増補前と比べられない')
        return ''

    def shares(g: list[dict], key) -> dict:
        t = sum(tok(r) for r in g) or 1
        d: dict = defaultdict(int)
        for r in g:
            d[key(r)] += tok(r)
        return {k: 100 * v / t for k, v in d.items()}

    band = lambda r: band_of(r, targets)                      # noqa: E731
    s_b, s_a = shares(core, band), shares(rows, band)
    bands = sorted(set(s_b) | set(s_a))
    au_b = shares(core, lambda r: r.get('author_ja', ''))
    au_a = shares(rows, lambda r: r.get('author_ja', ''))
    tops = [k for k, _ in sorted(au_a.items(), key=lambda kv: -kv[1])[:8]]

    fig, axes = plt.subplots(
        2, 1, figsize=(9, 4 + 0.42 * (len(bands) + len(tops))),
        gridspec_kw={'height_ratios': [len(bands), len(tops)]})
    h = 0.36                       # 隣の棒とのあいだに地を残す

    def panel(ax, labels, before, after, title, limit=None, limit_label=''):
        y = range(len(labels))
        # y 軸は反転させるので，**上に増補前・下に増補後**が来るよう
        # オフセットの符号をこの向きにする。読む順（上→下）を
        # 「前→後」に揃えるため。
        b1 = ax.barh([v - h / 2 - 0.02 for v in y], [before.get(k, 0) for k in labels],
                     height=h, color=C_BEFORE, label='増補前（v1 由来）', zorder=3)
        b2 = ax.barh([v + h / 2 + 0.02 for v in y], [after.get(k, 0) for k in labels],
                     height=h, color=C_AFTER, label='増補後', zorder=3)
        # 橙は地に対する明度差が小さい。**値を直接書いて補う。**
        for bars in (b1, b2):
            ax.bar_label(bars, fmt='%.1f%%', padding=3, fontsize=8, color=MUTED)
        if limit is not None:
            ax.axvline(limit, color=MUTED, lw=1.0, ls=(0, (4, 3)), zorder=4)
            ax.text(limit, len(labels) - 0.35, ' ' + limit_label,
                    fontsize=8, color=MUTED, va='top')
        ax.set_yticks(list(y))
        ax.set_yticklabels(labels, fontsize=9)
        ax.invert_yaxis()
        ax.set_xlabel('語数シェア（％）', fontsize=9, color=MUTED)
        ax.set_title(title, fontsize=11, color=INK, loc='left', pad=8)
        ax.grid(axis='x', color=GRID, lw=0.6, zorder=0)
        ax.set_axisbelow(True)
        for sp in ('top', 'right', 'bottom'):
            ax.spines[sp].set_visible(False)
        ax.spines['left'].set_color(GRID)
        ax.tick_params(length=0, colors=MUTED)
        ax.set_xlim(0, max(max(before.values(), default=0),
                           max(after.values(), default=0)) * 1.18)

    lim_a = 100 * float(targets.get('concentration', {}).get('max_author_share', 1))
    panel(axes[0], bands, s_b, s_a, '時代区分ごとの語数シェア')
    panel(axes[1], tops, au_b, au_a, '作家ごとの語数シェア（増補後の上位8名）',
          limit=lim_a, limit_label=f'目標 1作家 ≦ {lim_a:.0f}%')
    n_b, n_a = len(core), len(rows)
    t_b, t_a = sum(tok(r) for r in core), sum(tok(r) for r in rows)
    fig.suptitle(f'リバランスの効き  増補前 {n_b} 点 {t_b / 1e6:.2f}M 語'
                 f' → 増補後 {n_a} 点 {t_a / 1e6:.2f}M 語',
                 fontsize=12, color=INK, x=0.01, ha='left')
    # 凡例は図の見出しの行に置く。軸の中に置くと棒が伸びたとき重なり，
    # 軸のすぐ上に置くと小見出しとぶつかる。
    fig.legend(*axes[0].get_legend_handles_labels(), loc='upper right',
               bbox_to_anchor=(0.99, 0.995), ncol=2, frameon=False,
               fontsize=9, labelcolor=MUTED)
    fig.text(0.01, 0.005,
             f'どちらも再構築後のトークン列で測った値（{os.path.basename(meta_path)}）。'
             '見えているのは構成の違いだけで，測定法の違いは入っていない。',
             fontsize=8, color=MUTED, ha='left')
    fig.tight_layout(rect=(0, 0.02, 1, 0.96))
    path = vis.save_fig(fig, out_dir, 'Step1_rebalance')
    print(f'[ok  ] リバランスの図 → {path}')
    return path


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--meta', default=None,
                    help='既定: metadata/ の *_v3_local.csv > v3')
    ap.add_argument('--targets', default=str(ROOT / 'config' / 'design_targets.yaml'))
    ap.add_argument('--quiet', action='store_true', help='達していない項目だけ')
    ap.add_argument('--fig', default=None, metavar='出力先',
                    help='増補前と増補後の構成を並べた棒グラフを SVG で書く'
                         '（例 --fig results/figures）')
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

    if args.fig:
        say()
        draw_rebalance(rows, T, args.fig, meta)
    # 目標未達は「誤り」ではないので 0 を返す。CI で止めたいときは --strict を足すこと
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

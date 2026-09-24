#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_units.py
==============
**列名と中身の尺度が合っているか**を見張る。

なぜ要るのか
------------
``df_all_pct`` という列に ``0.1584`` が入っていた。名前は per cent（百分率）
だが，中身は 0–1 の割合である。**1 が最大値になる量を百分率とは呼べない。**
2026-09-24 に ``df_all_prop`` / ``df_in_prop`` へ改名した。

この種の誤りは**例外を出さない**。``df < 10%`` の culling を
``df_all_prop >= 10`` と書けば，閾値が 100 倍ずれたまま一語も落ちず，
図も表も何事もなく出る。気づけない誤りは機械に見張らせる
（``check_stem_keys.py`` ``check_script_calls.py`` と同じ考え方）。

列名の約束
----------
====================================  ==========  ==========================
末尾                                  値の範囲    例
====================================  ==========  ==========================
``_prop`` / ``_ratio`` / ``_share``   0–1         ``df_all_prop`` ``bungo_ratio``
``_rate``                             0–1         ``unknown_rate`` ``aux_rate``
``_pct`` / ``_percent``               0–100       ``types_pct`` ``tokens_pct``
``_per10k`` / ``pm_``                 1万語/100万語  ``bungo_per10k`` ``pm_in``
====================================  ==========  ==========================

検査すること
------------
``[NG  ]`` ``_pct`` なのに ``100 *`` が無い（中身は 0–1 の割合）
``[NG  ]`` ``_prop`` / ``_ratio`` / ``_share`` / ``_rate`` なのに ``100 *`` がある
``[NG  ]`` 百分率書式 ``{x:.1%}``（100 倍して表示する）を ``_pct`` の値に当てて
          いる — **二重に 100 倍**される
``[NG  ]`` 0–1 の量を 1 より大きい閾値と比べている（閾値が 100 倍ずれている）
``[warn]`` 0–1 の値の後ろに文字の ``%`` を置いている — 100 倍し忘れの疑い
``[warn]`` ``results/`` に旧名（``df_all_pct``）の CSV が残っている

静的に読むだけで，スクリプトは実行しない。右辺が変数1つだけのときは
尺度を追えないので，**100 倍が書かれていない＝割合**とみなす（名前が
``_prop`` 系なら合格，``_pct`` なら報告する）。

使い方
------
    python3 scripts/check_units.py
    python3 scripts/check_units.py --quiet          # 問題だけ
    python3 scripts/check_units.py --no-notebooks   # 生成物は見ない
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

OK, WARN, NG, INFO = '[ok  ]', '[warn]', '[NG  ]', '[info ]'
ROOT = Path(__file__).resolve().parent.parent

PCT = ('_pct', '_percent')                       # 0–100 のはず
PROP = ('_prop', '_ratio', '_share', '_rate')    # 0–1 のはず
SUFFIX = PCT + PROP

# 旧名。出力に残っていたら 07 を走らせ直す合図
LEGACY = {'df_all_pct': 'df_all_prop', 'df_in_pct': 'df_in_prop'}

# ``'name': 式`` と ``name = 式`` と ``name=式``（キーワード引数・assign）
DEF_RE = re.compile(
    r"""(?:'(?P<q>[A-Za-z_][\w]*)'\s*:\s*|(?<![=!<>])\b(?P<b>[A-Za-z_][\w]*)\s*=(?!=))"""
    r'(?P<rhs>[^\n]*)')
# 100 倍しているか（100 * x / x * 100 / 100. * x）
HUNDRED_RE = re.compile(r'\b100\.?\s*\*|\*\s*100\.?\b')
# 書式 {式:…%}
FMT_RE = re.compile(r'\{(?P<expr>[^{}:]{0,80}?):(?P<spec>[^{}]{0,12})%\}')
# {…} の直後の文字としての %
LIT_RE = re.compile(r'\{(?P<expr>[^{}]{1,80})\}%')
# 割合（0–1）を 1 より大きい数と比べていないか／百分率を 1 以下と比べていないか
CMP_RE = re.compile(r'(?P<name>[A-Za-z_][\w.]*)\s*(?P<op>>=|<=|>|<)\s*'
                    r'(?P<num>\d+(?:\.\d+)?)\b')


def suffixed(name: str) -> str | None:
    """その名前が尺度を名乗っているか。名乗っていれば末尾を返す。"""
    for s in SUFFIX:
        if name.endswith(s):
            return s
    return None


def scan_text(src: str, where: str, out: list[tuple[str, str]]) -> None:
    """1つのソースを見る。``out`` に ``(印, 文言)`` を積む。"""
    lines = src.split('\n')

    # ---- 1. 定義の尺度 ---------------------------------------------------
    for i, line in enumerate(lines, start=1):
        s = line.strip()
        if s.startswith('#') or not s:
            continue
        for m in DEF_RE.finditer(line):
            name = m.group('q') or m.group('b')
            suf = suffixed(name)
            if not suf:
                continue
            rhs = m.group('rhs')
            # 右辺に括弧の続きがあるときは次行も見る（round( … , 4) など）
            if rhs.count('(') > rhs.count(')') and i < len(lines):
                rhs += lines[i]
            has100 = bool(HUNDRED_RE.search(rhs))
            if suf in PCT and not has100:
                # 代入先が関数の引数や比較でないことを軽く確かめる
                out.append((NG, f'{where}:{i} {name} は百分率を名乗るが '
                                '100 倍していない（中身は 0–1 の割合か）'
                                f'\n        {s[:96]}'))
            elif suf in PROP and has100:
                out.append((NG, f'{where}:{i} {name} は割合を名乗るが '
                                '100 倍している（中身は百分率か）'
                                f'\n        {s[:96]}'))

    # ---- 2. 閾値の桁 -----------------------------------------------------
    # 0–1 の量を 10 と比べていれば，閾値が 100 倍ずれている（一語も落ちない）。
    for i, line in enumerate(lines, start=1):
        s = line.strip()
        if s.startswith('#') or not s:
            continue
        for m in CMP_RE.finditer(line):
            name, num = m.group('name'), float(m.group('num'))
            base = name.rsplit('.', 1)[-1]
            suf = suffixed(base)
            if suf in PROP and num > 1:
                out.append((NG, f'{where}:{i} 0–1 の {base} を {m.group("num")} '
                                'と比べている（閾値が 100 倍ずれている）'
                                f'\n        {s[:96]}'))
            elif suf in PCT and 0 < num <= 1:
                out.append((NG, f'{where}:{i} 0–100 の {base} を '
                                f'{m.group("num")} と比べている'
                                '（閾値が 1/100 になっている）'
                                f'\n        {s[:96]}'))

    # ---- 3. 書式の二重換算 ----------------------------------------------
    for i, line in enumerate(lines, start=1):
        for m in FMT_RE.finditer(line):
            expr = m.group('expr')
            if any(re.search(rf'\b\w*{re.escape(p)}\b', expr) for p in PCT):
                out.append((NG, f'{where}:{i} 百分率の値に % 書式を当てている'
                                '（100 倍が二重になる）'
                                f'\n        {line.strip()[:96]}'))
        for m in LIT_RE.finditer(line):
            expr = m.group('expr')
            if '%' in expr or HUNDRED_RE.search(expr):
                continue                      # 既に % 書式／100 倍済み
            if any(re.search(rf'\b\w*{re.escape(p)}\b', expr) for p in PROP):
                out.append((WARN, f'{where}:{i} 0–1 の値に文字の % を付けて'
                                  'いる（100 倍し忘れか）'
                                  f'\n        {line.strip()[:96]}'))


def scan_notebook(path: Path, out: list[tuple[str, str]]) -> None:
    nb = json.loads(path.read_text(encoding='utf-8'))
    src = '\n'.join(''.join(c['source']) for c in nb['cells']
                    if c['cell_type'] == 'code')
    scan_text(src, path.name, out)


def scan_outputs(res: Path, out: list[tuple[str, str]]) -> None:
    """``results/`` に旧名の列が残っていないか。**中身は直さない。**"""
    if not res.is_dir():
        return
    for p in sorted(res.rglob('*.csv')):
        try:
            with open(p, encoding='utf-8-sig') as fh:
                head = fh.readline()
        except OSError:
            continue
        hit = [k for k in LEGACY if k in head]
        if hit:
            try:
                rel: Path | str = p.relative_to(ROOT)
            except ValueError:
                rel = p
            out.append((WARN, f'{rel} に旧名の列が残っている: '
                              + '，'.join(f'{k} → {LEGACY[k]}' for k in hit)
                              + '\n        07_descriptive_stats.py を'
                                '走らせ直すこと（ノートブックは読み替える）。'))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default=str(ROOT / 'scripts'))
    ap.add_argument('--notebooks', default=str(ROOT / 'notebooks'))
    ap.add_argument('--results', default=str(ROOT / 'results'))
    ap.add_argument('--no-notebooks', action='store_true',
                    help='生成されたノートブックは見ない（生成器だけ見る）')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    out: list[tuple[str, str]] = []
    n_files = 0
    for p in sorted(Path(args.dir).glob('*.py')):
        if p.name == Path(__file__).name:
            continue                       # 自分の説明文で自分を咎めない
        scan_text(p.read_text(encoding='utf-8'), p.name, out)
        n_files += 1
    if not args.no_notebooks:
        for p in sorted(Path(args.notebooks).glob('*.ipynb')):
            scan_notebook(p, out)
            n_files += 1
    scan_outputs(Path(args.results), out)

    ng = sum(1 for k, _ in out if k == NG)
    for k, msg in out:
        if k == NG or not args.quiet:
            print(f'{k} {msg}')
    if not out and not args.quiet:
        print(f'{OK} {n_files} ファイルを検査：列名と尺度の食い違いは無い')
    print()
    if ng:
        print(f'{NG} 尺度の食い違いが {ng} 件ある。'
              '**数値がずれても例外は出ない。必ず直すこと。**')
    else:
        print(f'{OK} 列名の約束は守られている（{n_files} ファイル，'
              f'点検 {len(out)} 件）')
    return 1 if ng else 0


if __name__ == '__main__':
    raise SystemExit(main())

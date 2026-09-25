#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_script_calls.py
=====================
**ノートブックがスクリプトを正しく呼んでいるか**を突き合わせる。

なぜ要るのか
------------
ノートブックとスクリプトは別々に育つ。片方だけ直すと，次のどれかが起きる。

* ノートブックが**存在しないスクリプト**を呼ぶ（`run_script('20_x.py')`）
* **スクリプトに無い引数**を渡す（`--max-share` → 実は `--max-work-share`）
* スクリプトを**誰も呼んでいない**（作ったが，講義に組み込み忘れた）
* ノートブックを**古い版から作り直して**しまい，呼び出しのセルごと消える

どれも**何のエラーも出ない**。とくに最後のものは，ノートブックを古い
生成器（``make_notebooks.py``）から作り直すと，あとから足したスクリプトを
呼ぶセルが黙って消える。だから機械に見張らせる。

検査すること
------------
``[NG  ]`` 呼んでいるスクリプトが ``scripts/`` に無い
``[NG  ]`` 渡している選択肢が，そのスクリプトの ``argparse`` に無い
``[warn]`` ``scripts/`` にあるのに，どのノートブックからも呼ばれていない
``[ok  ]`` 呼び出しと選択肢が揃っている

引数は**静的に**読む（スクリプトは実行しない）。``argparse`` の
``add_argument`` を AST で拾うので，辞書や変数で組み立てた選択肢までは
追えない。そこは ``[warn]`` に留める。

使い方
------
    python3 scripts/check_script_calls.py
    python3 scripts/check_script_calls.py --quiet     # 問題だけ
"""
from __future__ import annotations

import argparse
import ast
import glob
import json
import os
import re
from pathlib import Path

OK, WARN, NG, INFO = '[ok  ]', '[warn]', '[NG  ]', '[info ]'
ROOT = Path(__file__).resolve().parent.parent

# run_script('05_tokenise_unidic.py', '--in', …) の呼び出し
CALL_RE = re.compile(r"run_script\s*\(\s*'([^']+\.py)'")
# ノートブックの外（docs や README）で触れられているだけの言及は数えない


def script_options(path: Path) -> tuple[set[str], bool]:
    """スクリプトの argparse の選択肢を集める。

    返り値は ``(選択肢の集合, 静的に読み切れたか)``。
    ``add_argument`` の第1引数が文字列でない（変数・ループ）ときは
    読み切れないので，突き合わせを ``[warn]`` に落とす。
    """
    opts: set[str] = set()
    complete = True
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError):
        return opts, False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == 'add_argument'):
            continue
        if not node.args:
            complete = False
            continue
        for a in node.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                if a.value.startswith('-'):
                    opts.add(a.value)
            else:
                complete = False
    return opts, complete


def calls_in_notebooks(nb_dir: Path, mentioned: set[str] | None = None
                       ) -> dict[str, list[tuple[str, list[str]]]]:
    """ノートブックごとの ``run_script`` 呼び出しと，渡している選択肢。

    ``mentioned`` を渡すと，``run_script`` 以外の呼び方（``subprocess.Popen``
    でサーバを起動するなど）で**名前が出てくるスクリプト**も集める。
    「誰も呼んでいない」の判定を間違えないため。
    """
    out: dict[str, list[tuple[str, list[str]]]] = {}
    for f in sorted(nb_dir.glob('*.ipynb')):
        nb = json.loads(f.read_text(encoding='utf-8'))
        src = '\n'.join(''.join(c['source']) for c in nb['cells']
                        if c['cell_type'] == 'code')
        if mentioned is not None:
            mentioned.update(re.findall(r"'([0-9A-Za-z_]+\.py)'", src))
        found = []
        for m in CALL_RE.finditer(src):
            # 呼び出し1つぶんの括弧の中を取り出す（入れ子の括弧に耐える）
            i = src.index('(', m.start())
            depth, j = 0, i
            while j < len(src):
                if src[j] == '(':
                    depth += 1
                elif src[j] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            body = src[i + 1:j]
            found.append((m.group(1),
                          re.findall(r"'(--[A-Za-z0-9][\w-]*)'", body)))
        if found:
            out[f.name] = found
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', default=str(ROOT / 'scripts'))
    ap.add_argument('--notebooks', default=str(ROOT / 'notebooks'))
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    sdir, ndir = Path(args.dir), Path(args.notebooks)
    scripts = {p.name: p for p in sorted(sdir.glob('*.py'))}
    # 番号で始まるものが「工程のスクリプト」。check_* や lib は対象外
    pipeline = {n for n in scripts if re.match(r'^\d\d[a-z]?_', n)}
    mentioned: set[str] = set()
    calls = calls_in_notebooks(ndir, mentioned)

    ng = warn = ok = 0
    called: set[str] = set()
    for nb, items in calls.items():
        for name, opts in items:
            called.add(name)
            if name not in scripts:
                ng += 1
                print(f'{NG} {nb}: {name} が scripts/ に無い')
                continue
            have, complete = script_options(scripts[name])
            bad = [o for o in opts if o not in have]
            if bad and complete:
                ng += 1
                print(f'{NG} {nb}: {name} に無い選択肢 {"，".join(bad)}')
                print(f'       使えるのは: {"，".join(sorted(have))}')
            elif bad:
                warn += 1
                print(f'{WARN} {nb}: {name} の選択肢 {"，".join(bad)} を'
                      '静的には確かめられない（argparse を動的に組んでいる）')
            else:
                ok += 1
                if not args.quiet:
                    print(f'{OK} {nb}: {name}'
                          + (f'（{len(opts)} 個の選択肢）' if opts else ''))

    # run_script 以外の呼び方（subprocess など）で名前が出るものは除く
    other = sorted((pipeline & mentioned) - called)
    orphan = sorted(pipeline - called - mentioned)
    if other and not args.quiet:
        print()
        for n in other:
            print(f'{INFO} {n} は run_script 以外の呼び方で使われている'
                  '（subprocess など）')
    if orphan:
        warn += len(orphan)
        print()
        for n in orphan:
            print(f'{WARN} {n} をどのノートブックも呼んでいない')
        print('       教員側の道具（一括生成・辞書比較など）なら正しい。'
              'そうでなければ**呼び出しのセルが消えた**疑いがある。'
              '\n       ノートブックを古い版から作り直すとこれが起きる'
              '（git の履歴から復旧できる）。')

    print()
    if ng:
        print(f'{NG} 呼び出しの食い違いが {ng} 件ある。'
              '**ノートブックを配る前に直すこと。**')
    else:
        print(f'{OK} ノートブックからの呼び出し {ok} 件は'
              f'すべてスクリプトと合っている'
              + (f'（点検 {warn} 件）' if warn else ''))
    return 1 if ng else 0


if __name__ == '__main__':
    raise SystemExit(main())

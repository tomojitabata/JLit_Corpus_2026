#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_notebooks.py
==================
ノートブックの静的検査。**授業で配る前に必ず通すこと。**

セルを上から順に実行したと仮定して名前空間を積み上げ，
その時点で束縛されていない名前が使われていないかを調べる。
実行せずに ``NameError`` を先回りして捕まえるための道具である。

（初出の不具合：共通プリアンブルに ``subprocess`` の import が無く，
  第2回の取得セルで ``NameError: name 'subprocess' is not defined`` が出た）

使い方
------
    python3 scripts/check_notebooks.py [--dir notebooks]

終了コード: 問題があれば 1。
"""
from __future__ import annotations

import argparse
import ast
import builtins
import glob
import json
import os
import sys

# Jupyter が暗黙に提供する名前
IPYTHON_BUILTINS = {'display', 'get_ipython', 'In', 'Out', 'exit', 'quit'}
BUILTIN = set(dir(builtins)) | IPYTHON_BUILTINS | {'__name__', '__file__', '__doc__'}


class Binder(ast.NodeVisitor):
    """そのセルで束縛される名前を集める。"""

    def __init__(self) -> None:
        self.bound: set[str] = set()

    def add(self, n) -> None:
        if isinstance(n, str):
            self.bound.add(n)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.add(node.id)
        self.generic_visit(node)

    def visit_Import(self, node):
        for a in node.names:
            self.add((a.asname or a.name).split('.')[0])

    def visit_ImportFrom(self, node):
        for a in node.names:
            self.add(a.asname or a.name)

    def visit_FunctionDef(self, node):
        self.add(node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.add(node.name)
        self.generic_visit(node)

    def visit_arg(self, node):
        self.add(node.arg)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node):
        for n in node.names:
            self.add(n)

    def visit_Nonlocal(self, node):
        for n in node.names:
            self.add(n)


def loaded_names(tree: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def check(path: str) -> list[str]:
    nb = json.load(open(path, encoding='utf-8'))
    env = set(BUILTIN)
    problems: list[str] = []
    for i, c in enumerate(nb.get('cells', [])):
        if c.get('cell_type') != 'code':
            continue
        src = ''.join(c.get('source', []))
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            problems.append(f'セル{i}: 構文エラー {e}')
            continue
        b = Binder()
        b.visit(tree)
        undef = sorted(loaded_names(tree) - env - b.bound)
        if undef:
            problems.append(f'セル{i}: 未定義の名前 {undef}')
        env |= b.bound
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default='notebooks')
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, '*.ipynb')))
    if not files:
        print(f'ノートブックが見つからない: {args.dir}')
        return 1
    total = 0
    for f in files:
        problems = check(f)
        total += len(problems)
        print(f'{os.path.basename(f):<36} {"OK" if not problems else "要修正"}')
        for p in problems:
            print('   ', p)
    print()
    if total:
        print(f'{total} 件の問題。scripts/make_notebooks.py を直して再生成すること。')
        return 1
    print('問題なし。')
    return 0


if __name__ == '__main__':
    sys.exit(main())

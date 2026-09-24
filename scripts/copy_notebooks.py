#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
copy_notebooks.py
=================
配布版のノートブック（notebooks/）を，自分の作業フォルダ my_work/notebooks/ に
コピーする。**受講生は my_work/notebooks/ のコピーを開いて実行すること。**

notebooks/ の配布版を直接実行すると，出力が .ipynb に書き込まれて
「手元で変更した」扱いになり，次の `git pull` が止まる。my_work/ はコースの
リポジトリの .gitignore で除外してあるので，いくら実行・書き込みをしても
pull は止まらない。my_work/ は自分の GitHub にバックアップを取る（setup_my_work.sh）。

    python scripts/copy_notebooks.py            # まだ無いものをコピー
    python scripts/copy_notebooks.py --dry-run  # 何をするかだけ表示

配布版が更新されたとき（教員が直したとき）の扱い
  - 自分のコピーに手を付けていない → 新しい版で置き換える
  - 自分のコピーを実行・編集した   → 自分のコピーはそのまま残し，
    新しい版を「01_corpus_design__新版_0924.ipynb」のような別名で置く

課題のテンプレート（templates/StepN_report.md・final_report.md）も同じ規則で
my_work/results/ にコピーする。

scripts/update.sh（教材の更新）の最後にも自動で呼ばれる。
標準ライブラリだけで動く（仮想環境の外の python3 でもよい）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'notebooks'
DST = ROOT / 'my_work' / 'notebooks'
TPL = ROOT / 'templates'                 # 課題のテンプレート（StepN_report.md・final_report.md）
TPL_DST = ROOT / 'my_work' / 'results'
STATE_NAME = '.copied_from.json'         # コピーした時点の配布版・コピーのハッシュ


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sync(srcs: list[Path], dst: Path, dry: bool, stamp: str) -> tuple[int, int, int, int]:
    """配布版 srcs を dst に揃える。自分で手を入れたコピーは上書きしない。"""
    state_path = dst / STATE_NAME
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    if not dry:
        dst.mkdir(parents=True, exist_ok=True)

    n_new = n_upd = n_side = n_same = 0
    for s in srcs:
        d = dst / s.name
        h_src = sha(s)
        rec = state.get(s.name, {})

        if not d.exists():
            action, n_new = 'コピー', n_new + 1
            target = d
        elif rec.get('src') == h_src:
            n_same += 1
            continue                          # 配布版は変わっていない
        elif not rec and sha(d) == h_src:
            n_same += 1                       # 記録は無いが中身は配布版と同一
            if not dry:
                state[s.name] = {'src': h_src, 'dst': h_src}
            continue
        elif rec and rec.get('dst') == sha(d):
            action, n_upd = '更新（未編集のコピーを新しい版で置換）', n_upd + 1
            target = d
        else:
            # 自分のコピーを実行・編集している（または記録が無い）→ 残す
            target = dst / f'{s.stem}__新版_{stamp}{s.suffix}'
            k = 2
            while target.exists():
                target = dst / f'{s.stem}__新版_{stamp}_{k}{s.suffix}'
                k += 1
            action, n_side = f'新版を別名で置く → {target.name}', n_side + 1

        print(f'  {s.name:34s} {action}')
        if dry:
            continue
        shutil.copy2(s, target)
        if target == d:
            state[s.name] = {'src': h_src, 'dst': sha(d)}
        else:
            # 新版は別名で置いた。自分のコピーの記録は「最新の配布版を受け取り済み」にする
            state[s.name] = {'src': h_src, 'dst': rec.get('dst', '')}

    if not dry:
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                              encoding='utf-8')
    rel = dst.relative_to(ROOT)
    print(f'[ OK ] {rel}/ : 新規 {n_new} ・更新 {n_upd} ・別名で新版 {n_side} ・変化なし {n_same}'
          + ('（--dry-run）' if dry else ''))
    return n_new, n_upd, n_side, n_same


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--dry-run', action='store_true', help='コピーせず予定だけ表示')
    a = ap.parse_args()

    if not SRC.is_dir():
        print(f'[ERR ] {SRC} が見つからない（リポジトリの中で実行すること）')
        return 1
    srcs = sorted(SRC.glob('*.ipynb'))
    if not srcs:
        print(f'[ERR ] {SRC} にノートブックが無い')
        return 1
    stamp = dt.date.today().strftime('%m%d')

    side = sync(srcs, DST, a.dry_run, stamp)[2]
    tpls = sorted(TPL.glob('*.md')) if TPL.is_dir() else []
    if tpls:
        side += sync(tpls, TPL_DST, a.dry_run, stamp)[2]

    if side:
        print('       「__新版_」の付いたファイルが教員の直した版。自分の版と見比べ，'
              '以後は新版で続けるとよい（自分の版は消さずに残してある）')
    print(f'       Jupyter では {DST.relative_to(ROOT)}/ のノートブックを開くこと（notebooks/ は配布版）')
    if tpls:
        print(f'       課題は {TPL_DST.relative_to(ROOT)}/StepN_report.md（テンプレート）に書く（手順書 §5.3）')
    if not a.dry_run and not (ROOT / 'my_work' / '.git').exists():
        print('[NOTE] my_work/ のバックアップがまだ無い。bash scripts/setup_my_work.sh <GitHubのユーザ名> で'
              '自分の GitHub にバックアップを作ること（手順書 §5）')
    return 0


if __name__ == '__main__':
    sys.exit(main())

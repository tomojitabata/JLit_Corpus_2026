#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_umap.py
=============
**UMAP が使えるかどうかを切り分ける。** 使えないときは，原因ごとに何をすれば
よいかを出す。

なぜ要るのか
------------
Step 5 §4 の語彙のギャラクシーは UMAP で射影し，入っていなければ t-SNE に
落ちる。この「落ちる」仕掛けが曲者で，**入れたはずなのに t-SNE の図が出る**
ことがある。図は出るので壊れているようには見えず，しかも t-SNE と UMAP では
塊の見え方が違うので，**設定の問題を分析結果と読み違える。**

原因はたいてい次の4つで，どれも見た目では区別がつかない。

1. **入れた先とカーネルの環境が違う**（いちばん多い）
   ``uv add`` は**プロジェクト**（``pyproject.toml`` のあるディレクトリ）の
   ``.venv`` に入れる。リポジトリ自体には ``pyproject.toml`` が無いので，
   uv は**親へ遡って**プロジェクトを探す。本授業の置き方
   （``~/Documents/dh_project/`` に ``pyproject.toml`` と ``.venv``，
   その中に ``JLit_Corpus_2026/``）なら，見つかるのは ``dh_project`` で，
   **カーネルと同じ ``.venv`` に入る。** 置き方が違う（``dh_project`` の外に
   clone した，``pyproject.toml`` が無い，別の親プロジェクトがある）と，
   **カーネルが使っている ``.venv`` とは別の場所に入る。**
   JupyterLab を anaconda3 など別の Python で起動している場合も同じ。
   どちらも ``sys.executable`` と下の「この環境に入っているか」で分かる。

   確実なのは**カーネルの Python を名指しして入れる**こと::

       uv pip install --python "<sys.executable の値>" umap-learn
2. **``umap`` という別のパッケージが入っている**
   PyPI には ``umap``（別物・古い）と ``umap-learn``（本物）がある。
   ``pip install umap`` をしてしまうと ``import umap`` はそちらを拾い，
   ``umap.UMAP`` が無いので実行時に落ちる。
3. **numba / llvmlite が numpy の版と合わない**
   UMAP は numba で JIT する。numpy 2.x に対して numba が古いと
   ``import umap`` 自体が例外になる。
4. **numba のキャッシュを書けない**
   共用機で ``site-packages`` に書き込めないと，初回の JIT で落ちることが
   ある。``NUMBA_CACHE_DIR`` を自分の書ける場所に向ければ通る。
5. **Intel Mac で新しい llvmlite を入れようとしている**
   llvmlite の macOS **x86_64** wheel は **0.45.1 が最後**で，0.46 以降は
   arm64 だけである。版を固定せずに入れると，uv/pip は llvmlite を
   ソースからビルドしに行き，Homebrew の LLVM と版が合わずに失敗する
   （llvmlite 0.49 は LLVM 22 を要求。``llvm@20`` では通らない）。
   Intel Mac で使える最新の組み合わせは
   **numba 0.62.1 ＋ llvmlite 0.45.1**（numpy<2.4）である。

使い方
------
    python3 scripts/check_umap.py
    python3 scripts/check_umap.py --fit 2000     # 2000点で実際に射影してみる
    python3 scripts/check_umap.py --quiet        # 結論だけ

**ノートブックと同じカーネルで走らせること。** 端末の python3 で通っても，
ノートブックが別の Python なら意味がない。ノートブックのセルから

    !python3 scripts/check_umap.py

ではなく

    import sys; print(sys.executable)

を先に見て，この診断を**その Python で**走らせる（下に出るコマンドを使う）。
"""
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import site
import sys
import time
import traceback
from pathlib import Path

OK, WARN, NG, INFO = '[ok  ]', '[warn]', '[NG  ]', '[info ]'

ROOT = Path(__file__).resolve().parent.parent


def ver(name: str) -> str:
    """版を返す。入っていなければ理由を返す。"""
    try:
        m = importlib.import_module(name)
    except Exception as e:                                   # noqa: BLE001
        return f'× ({type(e).__name__})'
    return str(getattr(m, '__version__', '版不明'))


def where(name: str) -> str:
    try:
        m = importlib.import_module(name)
    except Exception:                                        # noqa: BLE001
        return '—'
    return str(getattr(m, '__file__', '—'))


def has_pkg(python_exe: Path, name: str) -> bool:
    """その環境の site-packages に入っているかを**ファイルで**見る。

    別の環境のことは import では確かめられない（いま動いている
    インタプリタのことしか分からない）ので，ディレクトリを見る。
    """
    base = python_exe.parent.parent / 'lib'
    if not base.is_dir():
        return False
    for lib in base.glob('python3.*'):
        sp = lib / 'site-packages'
        if (sp / name).is_dir() or any(sp.glob(f'{name}-*.dist-info')):
            return True
    return False


def find_projects(root: Path, up: int = 3) -> list[Path]:
    """``uv add`` が拾いうる pyproject.toml を，上へ遡って探す。"""
    out, d = [], root
    for _ in range(up + 1):
        if (d / 'pyproject.toml').is_file():
            out.append(d)
        if d == d.parent:
            break
        d = d.parent
    return out


def project_dir() -> Path:
    """作業フォルダ（pyproject.toml と .venv の置き場）。

    00_bootstrap_mac.sh と同じ規則：JLIT_PROJECT_DIR があればそれ，
    リポジトリが同期フォルダ（Dropbox など）の中なら ~/Documents/dh_project，
    それ以外はリポジトリの親。
    """
    env = os.environ.get('JLIT_PROJECT_DIR')
    if env:
        return Path(env).expanduser()
    s = str(ROOT)
    if any(k in s for k in ('/Dropbox/', '/CloudStorage/', '/Google Drive/',
                            'OneDrive', '/Mobile Documents/')):
        return Path.home() / 'Documents' / 'dh_project'
    return ROOT.parent


def in_venv(exe: str) -> tuple[bool, str]:
    """この Python が作業フォルダの .venv かどうか。"""
    venv = project_dir() / '.venv'
    if not venv.exists() and (ROOT / '.venv').exists():    # 旧い置き方
        venv = ROOT / '.venv'
    if venv.exists():
        try:
            # .venv/bin/python は元の Python へのシンボリックリンクなので，
            # resolve() すると .venv の外を指す。sys.prefix と見比べる。
            same = (Path(sys.prefix).resolve() == venv.resolve()
                    or Path(exe).absolute().is_relative_to(venv.absolute()))
        except Exception:                                    # noqa: BLE001
            same = str(venv) in exe
        return same, str(venv)
    return False, ''


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fit', type=int, default=800,
                    help='実際に射影してみる点の数（0 で試さない）')
    ap.add_argument('--quiet', action='store_true', help='結論だけ出す')
    args = ap.parse_args()

    remedies: list[str] = []

    # ---- 1. どの Python で動いているか ------------------------------------
    if not args.quiet:
        print('■ この診断を走らせている Python')
        print(f'  sys.executable = {sys.executable}')
        print(f'  版             = {sys.version.split()[0]}')
        print(f'  site-packages  = {"; ".join(site.getsitepackages()[:2])}')
    venv_like = sys.prefix != getattr(sys, 'base_prefix', sys.prefix)
    if not args.quiet:
        print(f'  仮想環境か     = {"はい" if venv_like else "いいえ（素の Python）"}'
              f'（sys.prefix = {sys.prefix}）')
    same_venv, venv = in_venv(sys.executable)
    if venv:
        if same_venv:
            print(f'{OK} 作業フォルダの .venv で動いている')
        else:
            print(f'{NG} **作業フォルダの .venv ではない Python で動いている**')
            print(f'       .venv = {venv}')
            print(f'       いま  = {sys.executable}')
            remedies.append(
                'カーネルを作業フォルダの環境に切り替える:\n'
                '    bash scripts/00_bootstrap_mac.sh（--personal）をもう一度実行し，\n'
                '  JupyterLab の右上のカーネル名をクリックして '
                '「Python (JLit)」に切り替える。\n'
                f'  ノートブックの最初のセルで sys.executable が '
                f'{venv}/bin/python になっていることを確かめる。')
    elif not args.quiet:
        print(f'{INFO} 作業フォルダ {project_dir()} に .venv が無い'
              '（bash scripts/00_bootstrap_mac.sh で作る）')

    # ---- 1b. uv add はどのプロジェクトに入れるのか ------------------------
    # **ここが落とし穴。** uv は親へ遡って最初に見つけた pyproject.toml の
    # プロジェクトに入れる。それが作業フォルダ（dh_project）でなければ，
    # カーネルの .venv には入らないのに，出力は成功したように見える。
    projs = find_projects(ROOT)
    proj_note = ''
    if not args.quiet:
        print('\n■ uv add が入れる先')
    if not projs:
        if not args.quiet:
            print(f'{WARN} {ROOT} とその親に pyproject.toml が無い')
            print('       この状態で uv add を走らせると，uv は**さらに親を'
                  '探すか新しく作る**。カーネルの .venv には入らない。')
            print(f'       bash scripts/00_bootstrap_mac.sh が {project_dir()} に'
                  ' pyproject.toml を作る。')
        proj_note = ('作業フォルダに pyproject.toml が無いので，'
                     '**uv add は別の場所の .venv に入れた疑いが濃い**。')
    else:
        here = projs[0].resolve() == project_dir().resolve()
        if not args.quiet:
            print(f'{OK if here else WARN} 最初に見つかる pyproject.toml = '
                  f'{projs[0] / "pyproject.toml"}')
        if not here:
            if not args.quiet:
                print(f'       **これは作業フォルダ {project_dir()} のものではない。**'
                      'uv add はそちらの .venv に入れる。')
            proj_note = (f'uv add は {projs[0]} のプロジェクトに入れる。'
                         'カーネルの環境とは別である。')
        # 見つかったプロジェクトの .venv に umap が入っていないか見る
        for d in projs:
            cand = d / '.venv' / 'bin' / 'python'
            if cand.exists() and Path(sys.executable).resolve() != cand.resolve():
                got = has_pkg(cand, 'umap')
                if not args.quiet:
                    print(f'{INFO} 別の環境 {d.name}/.venv に umap は'
                          f'{"ある" if got else "ない"}')
                if got:
                    proj_note += (f' 実際 {d / ".venv"} には入っている——'
                                  '**入れた先を間違えている。**')

    # ---- 1c. 機種と wheel の在り方 ----------------------------------------
    # **Intel Mac（x86_64）は罠がある。** llvmlite の macOS x86_64 wheel は
    # 0.45.1 が最後で，0.46 以降は arm64 のみ。新しい numba を入れようとすると
    # llvmlite をソースからビルドしに行き，Homebrew の LLVM と版が合わずに
    # 失敗する（llvmlite 0.49 は LLVM 22 を要求。llvm@20 では通らない）。
    import platform
    intel_mac = (sys.platform == 'darwin' and platform.machine() == 'x86_64')
    intel_cmd = (
        f'uv pip install --python "{sys.executable}" \\\n'
        '        --only-binary :all: \\\n'
        '        "numba==0.62.1" "llvmlite==0.45.1" "numpy<2.4" umap-learn')
    if not args.quiet:
        print(f'\n■ 機種  {platform.platform()}（{platform.machine()}）')
        if intel_mac:
            print(f'{WARN} **Intel Mac である。版を固定して入れること。**')
            print('       llvmlite の macOS x86_64 wheel は 0.45.1 が最後'
                  '（0.46 以降は arm64 のみ）。')
            print('       固定しないと llvmlite をソースからビルドしに行き，'
                  'LLVM の版違いで失敗する。')
            print('       使える最新の組み合わせ: '
                  'numba 0.62.1 ＋ llvmlite 0.45.1（numpy<2.4）')
            print('         ' + intel_cmd.replace('\n        ', '\n           '))
            print('       numpy が 2.3 系に下がるが，pandas 3 / scipy 1.18 /'
                  ' scikit-learn 1.9 / matplotlib 3.11 はいずれも numpy 2.3 で動く。')

    # ---- 2. umap の中身 ----------------------------------------------------
    print('\n■ umap')
    mod = None
    try:
        mod = importlib.import_module('umap')
    except Exception as e:                                   # noqa: BLE001
        print(f'{NG} import umap が失敗した: {type(e).__name__}: {e}')
        if not args.quiet:
            traceback.print_exc()
        low = f'{type(e).__name__} {e}'.lower()
        if isinstance(e, ModuleNotFoundError):
            if intel_mac:
                remedies.append(
                    '**Intel Mac なので版を固定して**この環境に入れる:\n'
                    f'    {intel_cmd}\n'
                    '  固定しないと llvmlite をソースからビルドしに行き，'
                    'LLVM の版違いで失敗する\n'
                    '  （llvmlite の x86_64 wheel は 0.45.1 が最後）。'
                    'そのあと**カーネルを再起動**する。'
                    + (f'\n  ※ {proj_note}' if proj_note else ''))
            else:
                remedies.append(
                    'この環境に入っていない。**この環境を名指しして**入れる'
                    '（プロジェクトの有無に依存しないので，これがいちばん確実）:\n'
                    f'    uv pip install --python "{sys.executable}" umap-learn\n'
                    '  （uv を使っていなければ '
                    f'{sys.executable} -m pip install umap-learn）\n'
                    '  そのあと**カーネルを再起動**する。'
                    + (f'\n  ※ {proj_note}' if proj_note else ''))
        if 'numba' in low or 'llvmlite' in low or 'dtype' in low:
            remedies.append(
                'numba / llvmlite が numpy の版と合っていない。どちらかを揃える:\n'
                '    uv add "numba>=0.60" "llvmlite>=0.43"\n'
                '  それでも駄目なら numpy を下げる:\n'
                '    uv add "numpy<2.1"')
    else:
        print(f'{OK} import できた')
        print(f'       場所 = {where("umap")}')
        print(f'       版   = {getattr(mod, "__version__", "版不明")}')
        if not hasattr(mod, 'UMAP'):
            print(f'{NG} **UMAP クラスが無い。別パッケージの umap である。**')
            print('       PyPI の `umap`（別物）と `umap-learn`（本物）は'
                  '同じ `import umap` を使う。')
            remedies.append(
                '別パッケージの umap を外して umap-learn を入れる:\n'
                '    uv remove umap ; uv add umap-learn\n'
                '    （pip なら pip uninstall -y umap && '
                'pip install -U umap-learn）\n'
                '  `import umap; print(umap.__file__)` が '
                'site-packages/umap/umap_.py を指すこと。')
        else:
            print(f'{OK} umap.UMAP がある（umap-learn 本体）')

    # ---- 3. 周辺の版 -------------------------------------------------------
    if not args.quiet:
        print('\n■ 周辺の版')
        for name in ['numpy', 'scipy', 'sklearn', 'numba', 'llvmlite',
                     'pynndescent', 'gensim']:
            print(f'  {name:<12} {ver(name)}')
        print(f'  NUMBA_CACHE_DIR = {os.environ.get("NUMBA_CACHE_DIR", "（未設定）")}')

    # ---- 4. 実際に射影してみる --------------------------------------------
    if args.fit and mod is not None and hasattr(mod, 'UMAP'):
        print(f'\n■ 実際に射影してみる（{args.fit} 点・50次元）')
        try:
            import numpy as np
            rng = np.random.default_rng(11)
            X = rng.normal(size=(args.fit, 50)).astype('float32')
            X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-12
            t0 = time.time()
            P = mod.UMAP(n_neighbors=15, min_dist=0.12, metric='cosine',
                         random_state=20260920).fit_transform(X)
            print(f'{OK} 射影できた: {P.shape}（{time.time() - t0:.1f} 秒）')
            print('       初回は numba の JIT に十数秒かかる。2回目は速い。')
        except Exception as e:                               # noqa: BLE001
            print(f'{NG} 射影で落ちた: {type(e).__name__}: {e}')
            if not args.quiet:
                traceback.print_exc()
            low = f'{type(e).__name__} {e}'.lower()
            if 'cache' in low or 'permission' in low or 'read-only' in low:
                remedies.append(
                    'numba がキャッシュを書けない。書ける場所を指定する:\n'
                    '    export NUMBA_CACHE_DIR="$HOME/.cache/numba"\n'
                    '  ノートブックからなら，**最初のセルで**\n'
                    "    import os; os.environ['NUMBA_CACHE_DIR'] = "
                    "os.path.expanduser('~/.cache/numba')")
            else:
                remedies.append(
                    'import は通るが射影で落ちる。版の組み合わせを疑う:\n'
                    '    uv add "umap-learn>=0.5.6" "numba>=0.60" '
                    '"pynndescent>=0.5.11"')

    # ---- 結論 --------------------------------------------------------------
    print()
    if not remedies:
        print(f'{OK} UMAP は使える。ノートブックで t-SNE になるなら，'
              '**セルが別のカーネルで動いている**か，'
              'GAL_PROJ が "tsne" になっていないか確かめること。')
        print('       Step 5 §4 で GAL_PROJ = "umap" にすると，'
              '落ちずに**止まって理由を出す**（黙って t-SNE にしない）。')
        return 0

    print(f'{NG} UMAP は使えない。対処（上から順に試す）:')
    for i, r in enumerate(remedies, 1):
        print(f'\n  {i}. {r}')
    print('\n  直したら，**ノートブックのカーネルを再起動して**'
          'この診断をもう一度走らせること。')
    exe = shutil.which('uv')
    if exe:
        print(f'\n  ※ uv の環境で確かめるには:\n'
              f'      uv run python scripts/check_umap.py')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
00_env_check.py
===============
受講生の環境を点検する。第1回講義の冒頭で全員が実行する。

    python3 scripts/00_env_check.py          # macOS / Linux
    py scripts\\00_env_check.py               # Windows

必須項目がすべて揃えば ``ALL OK`` を表示し，終了コード 0 を返す。
足りないものは，その場で実行すべきコマンドを表示する。
"""
from __future__ import annotations

import importlib
import os
import platform
import shutil
import subprocess
import sys

OK, NG, WARN = '  OK ', ' MISS', ' WARN'
IS_WIN = platform.system() == 'Windows'
BREW_PREFIX = '/opt/homebrew' if platform.machine() == 'arm64' else '/usr/local'

results: list[tuple[str, str, str, str]] = []   # (level, name, detail, fix)


def add(level, name, detail='', fix=''):
    results.append((level, name, detail, fix))


def check_python():
    v = sys.version_info
    ok = (v.major, v.minor) >= (3, 10)
    add(OK if ok else NG, 'Python', f'{sys.version.split()[0]} @ {sys.executable}',
        'python.org から 3.12 を導入し，仮想環境を作り直すこと')
    in_venv = (hasattr(sys, 'real_prefix')
               or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix))
    add(OK if in_venv else WARN, '仮想環境',
        '有効' if in_venv else 'システム Python を使っている',
        'source .venv/bin/activate （Windows: .\\.venv\\Scripts\\Activate.ps1）')


def check_pkg(name, import_name=None, required=True, fix=''):
    mod = import_name or name
    try:
        m = importlib.import_module(mod)
        ver = getattr(m, '__version__', '')
        if not ver:
            try:
                from importlib.metadata import version
                ver = version(name)
            except Exception:                                   # noqa: BLE001
                ver = '?'
        add(OK, name, str(ver))
        return m
    except ImportError:
        add(NG if required else WARN, name, '未導入', fix or f'pip install {name}')
        return None


SHARED_DEFAULT = '/Users/Shared/jlit' if sys.platform == 'darwin' else ''


def shared_root() -> str:
    return os.environ.get('JLIT_SHARED') or SHARED_DEFAULT


# 本番の辞書（2026-09-22 決定。docs/dictionary_comparison.md §10）。
# **05_tokenise_unidic.py の PROD_* と揃えてある。片方だけ直さないこと。**
PROD_DICT = 'unidic-novel'
PROD_VERSION = 'v202512'
PROD_DIRNAME = f'{PROD_DICT}-{PROD_VERSION}'
PROD_URL = f'https://clrd.ninjal.ac.jp/unidic_archive/2512/{PROD_DIRNAME}.zip'


def check_unidic():
    """辞書の探索順は 05_tokenise_unidic.py と揃えてある。

    本番は近現代口語小説UniDic（``unidic-novel`` v202512）である。
    それ以外の辞書しか無い機体では，**使えるが本番ではない**ことを
    はっきり出す。cwj（現代書き言葉）は 2026-09-22 の判定で落選した
    （未知語率が novel の 3.9 倍）。気づかずに混ぜると比較が壊れる。
    """
    root = shared_root()
    cands = []
    env = os.environ.get('JLIT_UNIDIC_DIR')
    if env:
        cands.append((os.path.expanduser(env), '環境変数 JLIT_UNIDIC_DIR'))
    if root:
        cands += [(os.path.join(root, PROD_DIRNAME), '共有辞書（本番）'),
                  (os.path.join(root, PROD_DICT), '共有辞書'),
                  (os.path.join(root, 'unidic'), '共有辞書（旧・cwj）')]
    for path, src in cands:
        if path and os.path.exists(os.path.join(path, 'sys.dic')):
            name = os.path.basename(os.path.normpath(path))
            if name.startswith(PROD_DICT):
                add(OK, 'UniDic 辞書', f'{name}（{src}）')
            else:
                add(WARN, 'UniDic 辞書', f'{name}（{src}）— **本番ではない**',
                    f'本番は {PROD_DIRNAME}。{root}/{PROD_DIRNAME} に展開し，'
                    f'JLIT_UNIDIC_DIR をそこへ向けること（{PROD_URL}）')
            return path
    for mod in ('unidic', 'unidic_lite'):
        try:
            m = importlib.import_module(mod)
            d = getattr(m, 'DICDIR', '')
            if d and os.path.exists(os.path.join(d, 'sys.dic')):
                note = '（現代書き言葉。本番ではない）' if mod == 'unidic' else \
                    '（軽量版。未知語率が上がる。レポートに明記すること）'
                add(WARN, 'UniDic 辞書', f'{mod}{note}',
                    f'本番の {PROD_DIRNAME} を入れるには '
                    'bash scripts/00_bootstrap_mac.sh を実行する'
                    '（このマシンの全員が使えるようになる）')
                return d
        except ImportError:
            continue
    add(NG, 'UniDic 辞書', '未導入',
        'bash scripts/00_bootstrap_mac.sh を実行する'
        '（失敗する場合は uv pip install unidic-lite。'
        'ただし本番の辞書ではない）')
    return None


def check_mecab_rc():
    """fugashi が探す mecabrc の有無。macOS でのつまずきの最頻原因。"""
    if IS_WIN:
        return
    for p in (f'{BREW_PREFIX}/etc/mecabrc', '/usr/local/etc/mecabrc', '/etc/mecabrc'):
        if os.path.exists(p):
            add(OK, 'mecabrc', p)
            return
    add(WARN, 'mecabrc', '見つからない',
        f'mkdir -p {BREW_PREFIX}/etc && touch {BREW_PREFIX}/etc/mecabrc')


def check_tagger(dicdir):
    try:
        import fugashi
    except ImportError:
        return
    try:
        t = fugashi.Tagger(f'-d {dicdir}' if dicdir else '')
        ws = t('吾輩は猫である。')
        sample = ' / '.join(f'{w.surface}:{w.feature.pos1}' for w in ws[:4])
        add(OK, '形態素解析の動作', sample)
    except Exception as e:                                      # noqa: BLE001
        add(NG, '形態素解析の動作', str(e).splitlines()[-1][:80],
            '上の mecabrc と UniDic の項目を先に解決すること')


def check_java():
    j = shutil.which('java')
    if not j:
        jh = os.environ.get('JAVA_HOME') or os.path.join(
            shared_root(), 'jdk', 'Contents', 'Home')
        cand = os.path.join(jh, 'bin', 'java')
        j = cand if os.path.exists(cand) else None
    if not j:
        add(WARN, 'Java (MALLET 用)', '未導入（Step 8 まで無くてよい）',
            'bash scripts/00_bootstrap_mac.sh が admin 権限なしで導入する。'
            'それでも入らなければ uv pip install tomotopy で代替できる')
        return
    try:
        out = subprocess.run([j, '-version'], capture_output=True, text=True, timeout=20)
        lines = [l for l in (out.stderr or out.stdout).splitlines()
                 if l.strip() and 'Picked up' not in l]
        ver = lines[0] if lines else '不明'
    except Exception:                                           # noqa: BLE001
        ver = '不明'
    add(OK, 'Java (MALLET 用)', ver)


def check_mallet():
    p = os.environ.get('MALLET') or shutil.which('mallet')
    if not p and shared_root():
        cand = os.path.join(shared_root(), 'mallet', 'bin', 'mallet')
        p = cand if os.path.exists(cand) else p
    if p and os.path.exists(p):
        add(OK, 'MALLET', p)
        if not IS_WIN and (' ' in p):
            add(WARN, 'MALLET のパス', '空白を含む', 'ASCII のみの短いパスに移すこと')
        return
    add(WARN, 'MALLET', '環境変数 MALLET が未設定（Step 8 まで無くてよい）',
        'source /Users/Shared/jlit/env.sh を実行するか，'
        'bash scripts/00_bootstrap_mac.sh で導入する')


def check_font():
    try:
        from matplotlib import font_manager
    except ImportError:
        return
    avail = {f.name for f in font_manager.fontManager.ttflist}
    for c in ('Hiragino Sans', 'Yu Gothic', 'Meiryo', 'Noto Sans CJK JP',
              'IPAexGothic', 'MS Gothic'):
        if c in avail:
            add(OK, '日本語フォント', c)
            return
    add(WARN, '日本語フォント', '見つからない（図のラベルが □ になる）',
        'macOS は Hiragino Sans が既定。Linux は fonts-noto-cjk を導入')


def check_encoding():
    enc = sys.stdout.encoding or ''
    lvl = OK if 'utf' in enc.lower() else WARN
    add(lvl, '標準出力の文字コード', enc,
        'Windows: [Environment]::SetEnvironmentVariable("PYTHONUTF8","1","User")')


def check_machine():
    """どのマシンで作業しているかを記録させる。

    DH Lab の iMac は XCreds 認証で，**ホームはログインしたマシンにしか
    残らない**。別の iMac に移ると仮想環境も成果物も無い状態から始まる。
    受講生がこれを知らないまま2台目に移ると，作業が消えたように見える。
    毎回ここで機体名を表示し，レポートに書かせる。
    """
    host = platform.node().split('.')[0]
    try:
        import subprocess as _sp
        name = _sp.run(['scutil', '--get', 'ComputerName'], capture_output=True,
                       text=True, timeout=5).stdout.strip() or host
    except Exception:                                           # noqa: BLE001
        name = host
    add(OK, '作業中のマシン', f'{name}（{host}）',
        'ホームはこのマシンにしか残らない。成果物は Git で持ち運ぶこと')

    try:
        st = os.statvfs(os.path.expanduser('~'))
        free = st.f_bavail * st.f_frsize / 1e9
        lvl = OK if free > 20 else WARN
        add(lvl, 'ホームの空き容量', f'{free:,.0f} GB',
            '20 GB を切ったら data/ と results/ の古い生成物を消すこと')
    except Exception:                                           # noqa: BLE001
        pass

    root = shared_root()
    if not root:
        return
    if os.path.isdir(root):
        # 辞書は名前が増えるので決め打ちにしない。unidic で始まるものは
        # すべて挙げる（どの辞書が置いてある機体かが一目で分かる）。
        have = [n for n in ('jdk', 'mallet', 'aozora-cache')
                if os.path.isdir(os.path.join(root, n))]
        have += sorted(n for n in os.listdir(root)
                       if n.startswith('unidic')
                       and os.path.isdir(os.path.join(root, n)))
        add(OK if have else WARN, 'このマシンの共有物', 
            f'{root}  [{", ".join(have) if have else "空"}]',
            'bash scripts/00_bootstrap_mac.sh で用意する')
    else:
        add(WARN, 'このマシンの共有物', f'{root} が無い',
            'bash scripts/00_bootstrap_mac.sh を実行する（admin 権限は要らない）')


def check_workspace():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    need = ['config/pipeline.yaml', 'config/corpus_manifest.tsv',
            'metadata/corpus_metadata_v2.csv', 'scripts', 'notebooks']
    miss = [n for n in need if not os.path.exists(os.path.join(here, n))]
    add(OK if not miss else WARN, 'リポジトリ構成',
        here if not miss else '不足: ' + ', '.join(miss),
        'リポジトリのルートから実行しているか確認すること')
    w = os.path.join(here, 'results')
    try:
        os.makedirs(w, exist_ok=True)
        t = os.path.join(w, '.writetest')
        open(t, 'w').close()
        os.remove(t)
        add(OK, 'results/ への書き込み', w)
    except OSError as e:
        add(NG, 'results/ への書き込み', str(e),
            '共用機では自分のホーム以下にリポジトリを複製して作業すること')


def main() -> int:
    print('=' * 72)
    print(' JLit 環境チェック')
    print(f' {platform.platform()} / {platform.machine()}')
    print('=' * 72)

    check_machine()
    check_python()
    check_encoding()
    for name, imp, req in [('jupyterlab', 'jupyterlab', True),
                           ('numpy', 'numpy', True),
                           ('pandas', 'pandas', True),
                           ('scipy', 'scipy', True),
                           ('scikit-learn', 'sklearn', True),
                           ('matplotlib', 'matplotlib', True),
                           ('gensim', 'gensim', True),
                           ('fugashi', 'fugashi', True),
                           ('lxml', 'lxml', True),
                           ('pyyaml', 'yaml', True),
                           ('openpyxl', 'openpyxl', True),
                           ('tqdm', 'tqdm', False)]:
        check_pkg(name, imp, req)
    check_mecab_rc()
    dicdir = check_unidic()
    check_tagger(dicdir)
    check_java()
    check_mallet()
    check_font()
    check_workspace()

    print()
    width = max(len(n) for _, n, _, _ in results) + 2
    for lvl, name, detail, _ in results:
        print(f'[{lvl}] {name:<{width}} {detail}')

    miss = [r for r in results if r[0] == NG]
    warn = [r for r in results if r[0] == WARN]
    print()
    if miss:
        print('― 対処が必要な項目 ―')
        for _, name, _, fix in miss:
            print(f'  ■ {name}')
            print(f'      {fix}')
    if warn:
        print('― 確認しておくとよい項目 ―')
        for _, name, detail, fix in warn:
            print(f'  ・ {name}: {detail}')
            if fix:
                print(f'      {fix}')
    print()
    if not miss:
        print('  ALL OK — Step 1 のノートブックに進んでよい。')
        return 0
    print(f'  必須項目に {len(miss)} 件の不足がある。上の指示に従って解決すること。')
    print('  まず bash scripts/00_bootstrap_mac.sh を試すこと。')
    print('  それでも解決しない場合は docs/00_setup_students.md の「つまずき」表を参照。')
    return 1


if __name__ == '__main__':
    sys.exit(main())

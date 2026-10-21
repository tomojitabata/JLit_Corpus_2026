#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
20_build_site.py
================
JLit KWIC コンコーダンサとトピックビューアを，**サーバの要らない Web ページ**
にまとめる（大学の個人ページなど，静的なファイルしか置けない場所で公開するため）。

作るもの（``--out``，既定 ``site_build/``。版管理しない）::

    site_build/
      jlit/                         コーパスと道具の紹介ページ
        index.html
        kwic/                       KWIC コンコーダンサ（Web 版）
          index.html  manual.html  engine.js  shim.js
          data/  manifest.json  meta.json.bin  *.bin
        topics/                     トピックビューア
          index.html  topic_viewer_manual.html
      teaching/dh-text-analytics/   授業のページ
        index.html

2つの紹介ページの体裁は，ホームページ（~tabata/）の ``css/style.css`` を使う
（ディケンズ方言プロジェクトのページと同じ作り）。ホームページの側に置かれて
いるので，ここでは作らない。手元で見た目を確かめるときだけ ``--home-css`` に
ホームページの css フォルダを渡すと，``site_build/css/`` に写す（公開はしない）。

なぜ要るのか
------------
KWIC コンコーダンサは手元で ``16_kwic_server.py`` を動かして使う作りなので，
そのままでは公開できない。そこで検索の中身（``kwic_core.py``）を JavaScript に
移した ``kwic_web/engine.js`` をブラウザの中で動かし，索引を圧縮して一緒に置く。
画面（``kwic_app.html``）は同じものを使う。

⚠ ``kwic_core.py`` を直したら ``kwic_web/engine.js`` も直し，
  ``python3 scripts/check_kwic_web.py`` で両者の結果が一致することを確かめること。

⚠ 公開するページに**手元の絶対パスを残さない**。索引とビューアに埋め込まれた
  由来（どのファイルから作ったか）は，リポジトリからの相対パスに書き換える。

使い方
------
    python3 scripts/20_build_site.py
    python3 scripts/20_build_site.py --viewer my_work/results/topic_viewer.html
    python3 -m http.server 8000 --directory site_build   # 手元で確かめる
    # → http://127.0.0.1:8000/jlit/

公開は Cyberduck で ``site_build/`` の ``jlit`` と ``teaching`` をサーバの
``public_html`` に置く（README の「5. Web で公開する」）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import html
import json
import os
import re
import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO_URL = 'https://github.com/tomojitabata/JLit_Corpus_2026'


# ---------------------------------------------------------------------------
# 索引を詰める
# ---------------------------------------------------------------------------
def varint(a: np.ndarray) -> bytes:
    """0 以上の整数の列を可変長整数（7 ビットずつ，下位から）にする。"""
    a = np.asarray(a, dtype=np.int64)
    if a.size and a.min() < 0:
        raise ValueError('負の値は詰められない')
    n1 = a < (1 << 7)
    n2 = (a >= (1 << 7)) & (a < (1 << 14))
    n3 = (a >= (1 << 14)) & (a < (1 << 21))
    n4 = a >= (1 << 21)
    if (a >= (1 << 28)).any():
        raise ValueError('値が大きすぎる')
    L = n1 * 1 + n2 * 2 + n3 * 3 + n4 * 4
    pos = np.concatenate([[0], np.cumsum(L)[:-1]]).astype(np.int64)
    buf = np.zeros(int(L.sum()), dtype=np.uint8)
    for k in range(4):
        m = L > k
        last = L == k + 1
        byte = (a[m] >> (7 * k)) & 0x7F
        cont = np.where(last[m], 0, 0x80)
        buf[pos[m] + k] = (byte | cont).astype(np.uint8)
    return buf.tobytes()


def gz(b: bytes) -> bytes:
    return gzip.compress(b, compresslevel=9, mtime=0)


def strip_paths(obj, roots: list[str]):
    """由来の中の絶対パスを，リポジトリからの相対パスにする。"""
    if isinstance(obj, dict):
        return {k: strip_paths(v, roots) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_paths(v, roots) for v in obj]
    if isinstance(obj, str):
        s = obj
        for root in roots:
            s = s.replace(root + os.sep, '').replace(root, '.')
        # リポジトリの外の絶対パス（ホームの下など）は名前だけにする
        if re.match(r'^(/Users/|/home/|[A-Za-z]:\\)', s):
            s = os.path.basename(s.rstrip('/\\'))
        return s
    return obj


def pack_kwic(kwic_dir: Path, out: Path, roots: list[str]) -> dict:
    meta = json.loads((kwic_dir / 'kwic_index.json').read_text(encoding='utf-8'))
    arr_dir = kwic_dir / 'arrays'
    need = ['surf', 'lem', 'orth', 'pos12', 'cfm', 'gos', 'sent', 'work', 'pos']
    miss = [k for k in need if not (arr_dir / f'{k}.npy').exists()]
    if miss:
        sys.exit(f'索引の配列が無い: {", ".join(miss)}\n'
                 '  python3 scripts/15_kwic_index.py で索引を作り直すこと（版 2 以降が要る）。')
    A = {k: np.load(arr_dir / f'{k}.npy', mmap_mode='r') for k in need}
    n = int(len(A['surf']))
    V = meta['vocab']

    # Web 版では品詞の大分類を中分類から引く。引けることを確かめる
    p1 = {p: i for i, p in enumerate(V['pos'])}
    pmap = np.array([p1[p.split('-')[0]] for p in V['pos12']], dtype=np.int64)
    if not np.array_equal(pmap[np.asarray(A['pos12'], dtype=np.int64)],
                          np.asarray(A['pos'], dtype=np.int64)):
        sys.exit('品詞の大分類が中分類から引けない（索引が想定と違う）')
    # 作品の番号は作品の始まりと終わりから作れることを確かめる
    w = np.asarray(A['work'], dtype=np.int64)
    for x in meta['works']:
        if not (w[x['start']:x['end']] == x['i']).all():
            sys.exit(f'作品 {x["stem"]} の範囲が配列と合わない')
    sent = np.asarray(A['sent'], dtype=np.int64)
    if (np.diff(sent) < 0).any():
        sys.exit('文番号が単調に増えていない')

    data = out / 'data'
    data.mkdir(parents=True, exist_ok=True)
    files = []

    def put(name: str, payload: bytes, **info):
        b = gz(payload)
        (data / name).write_bytes(b)
        files.append({'file': name, 'bytes': len(b), **info})

    # 語の列は「頻度の順位」に直してから詰める（よく出る語ほど短くなる）。
    # 番号は読み込み時に元に戻す（perm）ので，語彙の並びは索引と同じ
    perms = {}
    for k in ('surf', 'lem', 'orth'):
        a = np.asarray(A[k], dtype=np.int64)
        f = np.bincount(a, minlength=len(V[k]))
        order = np.argsort(-f, kind='stable')          # 順位 → 元の番号
        rank = np.empty_like(order)
        rank[order] = np.arange(order.size)
        put(f'{k}.bin', varint(rank[a]), array=k, kind='vb',
            stage='later' if k == 'orth' else 'core', perm=f'{k}.perm.bin')
        perms[k] = order
        b = gz(varint(order))
        (data / f'{k}.perm.bin').write_bytes(b)
    for k, stage in (('pos12', 'core'), ('cfm', 'later'), ('gos', 'later')):
        put(f'{k}.bin', np.asarray(A[k], dtype=np.uint8).tobytes(), array=k, kind='u8', stage=stage)
    put('sent.bin', varint(np.diff(sent, prepend=0)), array='sent', kind='dvb', stage='core')

    meta_pub = strip_paths(meta, roots)
    meta_pub['provenance']['web_build'] = _dt.datetime.now().astimezone().isoformat(timespec='seconds')
    mb = gz(json.dumps(meta_pub, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    (data / 'meta.json.bin').write_bytes(mb)
    man = {'build': meta_pub['provenance']['web_build'], 'n': n, 'meta': {'file': 'meta.json.bin', 'bytes': len(mb)}, 'files': files,
           'perm_bytes': {k: (data / f'{k}.perm.bin').stat().st_size for k in perms}}
    (data / 'manifest.json').write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'meta': meta_pub, 'n': n, 'bytes_core': sum(f['bytes'] for f in files if f['stage'] == 'core') + len(mb),
            'bytes_all': sum(p.stat().st_size for p in data.iterdir())}


# ---------------------------------------------------------------------------
# 画面
# ---------------------------------------------------------------------------
WEB_NOTE = (
    '<div id="webnote" style="background:#eef4fb;border-bottom:1px solid #d8d7d0;'
    'padding:6px 16px;font-size:12px;color:#33322e">'
    '<b>Web 版</b>：検索はすべてこのブラウザの中で行う（検索語はどこにも送らない）。'
    'コーパスは青空文庫の公開作品から作った JLit Corpus 2026，形態素解析は UniDic による。'
    'サンプリングと無作為な並べ替えは，手元のサーバ版（Python）と同じ用例を選ぶとは限らない。'
    '<a href="../" style="margin-left:.6em">JLit Corpus 2026 について</a>'
    '<a href="__REPO__" style="margin-left:.6em">リポジトリ</a></div>')


def build_kwic_page(out: Path, build: str) -> None:
    src = (HERE / 'kwic_app.html').read_text(encoding='utf-8')
    rep = [
        # サーバ版のマニュアル（/manual）→ 同じフォルダの manual.html
        ('href="/manual', 'href="manual.html'),
        # CSV の書き出しはブラウザの中で作る
        ("window.location = '/api/export.csv?q=' + q;", 'window.jlitExportCsv(q);'),
        ('<body>', '<body>\n' + WEB_NOTE.replace('__REPO__', REPO_URL)),
        # 版を付けて読む（古い shim.js・engine.js がブラウザに残っていても使われない）
        ('<script>', f'<script src="shim.js?v={build}"></script>\n<script>'),
    ]
    for a, b in rep:
        k = src.count(a)
        if k == 0:
            sys.exit(f'kwic_app.html に想定した箇所が無い: {a[:40]}…（20_build_site.py を直すこと）')
        src = src.replace(a, b, 1 if a in ('<body>', '<script>') else -1)
    (out / 'index.html').write_text(src, encoding='utf-8')
    man = (HERE / 'kwic_manual.html').read_text(encoding='utf-8')
    note = ('<div style="background:#eef4fb;border:1px solid #d8d7d0;border-radius:8px;'
            'padding:10px 14px;margin:12px 0;font-size:13px">'
            '<b>Web 版で読むときの注意</b>：このマニュアルは手元でサーバ（16_kwic_server.py）を'
            '動かす使い方を前提に書いてある。Web 版ではサーバの起動は要らず，'
            '画面の操作は同じである。サンプリングと無作為な並べ替えの結果は，'
            'サーバ版と同じ用例になるとは限らない。</div>')
    m = re.search(r'<body[^>]*>', man)
    if m:
        man = man[:m.end()] + '\n' + note + man[m.end():]
    (out / 'manual.html').write_text(man, encoding='utf-8')
    shutil.copy2(HERE / 'kwic_web' / 'engine.js', out / 'engine.js')
    shim = (HERE / 'kwic_web' / 'shim.js').read_text(encoding='utf-8')
    if shim.count("new Worker('engine.js')") != 1:
        sys.exit('shim.js に想定した箇所が無い（20_build_site.py を直すこと）')
    (out / 'shim.js').write_text(shim.replace("new Worker('engine.js')", f"new Worker('engine.js?v={build}')"),
                                 encoding='utf-8')


def build_topics(viewer: Path | None, out: Path, roots: list[str]) -> dict | None:
    if viewer is None:
        return None
    out.mkdir(parents=True, exist_ok=True)
    s = viewer.read_text(encoding='utf-8')
    s2 = s
    for root in roots:
        s2 = s2.replace(root + '/', '').replace(root, '.')
    left = sorted(set(re.findall(r'/(?:Users|home)/[^"\'<>\s,]+', s2)))
    if left:
        print('[warn] トピックビューアに手元の絶対パスが残っている（名前だけにする）:')
        for p in left[:8]:
            print('       ', p)
        for p in left:
            s2 = s2.replace(p, os.path.basename(p.rstrip('/')))
    (out / 'index.html').write_text(s2, encoding='utf-8')
    shutil.copy2(HERE / 'topic_viewer_manual.html', out / 'topic_viewer_manual.html')
    return {'bytes': (out / 'index.html').stat().st_size}


# ---------------------------------------------------------------------------
# 紹介ページ
# ---------------------------------------------------------------------------
def fill(tpl: str, vals: dict) -> str:
    def sub(m):
        k = m.group(1)
        if k not in vals:
            sys.exit(f'ページの置き換え先が無い: {{{{{k}}}}}')
        return html.escape(str(vals[k]))
    return re.sub(r'\{\{(\w+)\}\}', sub, tpl)


def page_values(meta: dict, n: int, core: int, viewer: dict | None) -> dict:
    ws = meta['works']
    years = []
    for w in ws:
        try:
            years.append(int(float(w['year'])))
        except (TypeError, ValueError):
            pass
    pv = meta['provenance']
    return {
        'works': f'{len(ws):,}',
        'authors': f'{len({w["author"] for w in ws if w["author"]}):,}',
        'tokens': f'{n:,}',
        'year_from': min(years) if years else '?',
        'year_to': max(years) if years else '?',
        'dictionary': f'{pv.get("dictionary", "?")} {pv.get("dictionary_version", "")}'.strip(),
        'built': pv.get('built_at', '?'),
        'web_build': pv.get('web_build', '?')[:10],
        'kwic_mb': f'{core / 1048576:.0f}',
        'viewer_mb': f'{viewer["bytes"] / 1048576:.1f}' if viewer else '—',
        'repo': REPO_URL,
        'year_now': _dt.date.today().year,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--kwic', default=str(ROOT / 'data' / 'kwic'), help='KWIC の索引（15_kwic_index.py の出力）')
    ap.add_argument('--viewer', default=None,
                    help='トピックビューアの HTML（既定：my_work/results/ か results/ の topic_viewer.html）')
    ap.add_argument('--no-viewer', action='store_true', help='トピックビューアを入れない')
    ap.add_argument('--out', default=str(ROOT / 'site_build'))
    ap.add_argument('--home-css', default=None,
                    help='手元で確かめるときだけ：ホームページの css フォルダ（site_build/css/ に写す。公開はしない）')
    args = ap.parse_args()

    kwic_dir, out = Path(args.kwic), Path(args.out)
    if not (kwic_dir / 'kwic_index.json').exists():
        sys.exit(f'KWIC の索引が無い: {kwic_dir}\n  python3 scripts/15_kwic_index.py を先に実行すること。')
    viewer = None
    if not args.no_viewer:
        cands = [Path(args.viewer)] if args.viewer else [
            ROOT / 'my_work' / 'results' / 'topic_viewer.html', ROOT / 'results' / 'topic_viewer.html']
        viewer = next((c for c in cands if c.exists()), None)
        if viewer is None:
            sys.exit('トピックビューアの HTML が無い: ' + ' / '.join(map(str, cands))
                     + '\n  Step 8 のノートブックで作るか，--viewer で指定するか，--no-viewer を付けること。')
    # 由来に残るパスの根元：このリポジトリ，索引を作ったリポジトリ，ビューアを作ったリポジトリ
    roots = {str(ROOT), str(kwic_dir.resolve().parents[1])}
    if viewer is not None:
        for par in viewer.resolve().parents:
            if (par / 'scripts' / '19_topic_viewer.py').exists():
                roots.add(str(par))
                break
    roots = sorted(roots, key=len, reverse=True)

    if out.exists():
        shutil.rmtree(out)
    (out / 'jlit' / 'kwic').mkdir(parents=True)
    print(f'[kwic] 索引を詰める ← {kwic_dir}')
    info = pack_kwic(kwic_dir, out / 'jlit' / 'kwic', roots)
    build_kwic_page(out / 'jlit' / 'kwic', re.sub(r'\W', '', info['meta']['provenance']['web_build']))
    print(f'       {info["n"]:,} 形態素。最初に読む分 {info["bytes_core"] / 1048576:.1f} MB，'
          f'全体 {info["bytes_all"] / 1048576:.1f} MB')
    vinfo = None
    if viewer is not None:
        print(f'[topc] トピックビューア ← {viewer}')
        vinfo = build_topics(viewer, out / 'jlit' / 'topics', roots)

    vals = page_values(info['meta'], info['n'], info['bytes_core'], vinfo)
    for rel in ('jlit/index.html', 'teaching/dh-text-analytics/index.html'):
        tpl = (ROOT / 'site' / rel).read_text(encoding='utf-8')
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(fill(tpl, vals), encoding='utf-8')
    if args.home_css:
        shutil.copytree(args.home_css, out / 'css')
        print(f'[css ] 確かめる用にホームページの体裁を写した（公開しない）← {args.home_css}')
    else:
        print('[note] 紹介ページの体裁はホームページの css/style.css を使う。手元で見た目を確かめるには '
              '--home-css にホームページの css フォルダを渡すこと。')
    if vinfo is None:
        print('[warn] トピックビューアを入れていない。紹介ページのリンクは切れる。')

    # Web サーバが読めるように：ファイルは 644，フォルダは 755
    for p in out.rglob('*'):
        p.chmod(0o755 if p.is_dir() else 0o644)
    out.chmod(0o755)

    total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    print(f'[out ] {out}（{total / 1048576:.1f} MB）')
    print('       手元で確かめる: python3 -m http.server 8000 --directory ' + str(out))
    print('       → http://127.0.0.1:8000/jlit/')
    print('[next] Cyberduck でサーバの public_html を開き，site_build/ の jlit と teaching を'
          'ドラッグして置く（2回目からは「上書き」）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

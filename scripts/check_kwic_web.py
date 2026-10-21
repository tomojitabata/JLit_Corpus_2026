#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_kwic_web.py
=================
KWIC コンコーダンサの **Web 版（scripts/kwic_web/engine.js）が，サーバ版
（kwic_core.py）と同じ結果を返すか**を確かめる。

なぜ必要か
------------
Web 版は検索の中身を JavaScript で書き直したものである。両方を同じに保たないと，
公開した Web 版と授業で使うサーバ版とで，同じ検索の件数や共起語が食い違う。
どちらも「もっともらしい」結果を返すので，見ただけでは気づけない。
そこで同じ検索を両方に掛け，結果を項目ごとに比べる。

比べ方
------
* 用例の行・時代区分と作品ごとの件数・共起語・異綴形・揺れの一覧・広い文脈は
  **完全に一致**すること（小数は丸めの差を許す）
* 一致した語形の表は，件数が同じ語の並びが Python 版と違ってよい
  （numpy の並べ替えが同点の順を決めないため）
* サンプリングと無作為な並べ替えは比べない（乱数の作り方が違う）

使い方
------
    python3 scripts/check_kwic_web.py                  # 合成コーパスで
    python3 scripts/check_kwic_web.py --index data/kwic  # 実際の索引でも（数分かかる）

Node.js（``node``）が要る。無ければ何もせずに知らせる。
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np                                      # noqa: E402
import kwic_core as K                                   # noqa: E402

OK, NG = '[ok  ]', '[NG  ]'


def export_arrays(kw: K.KwicIndex, d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    meta = {'provenance': kw.prov, 'works': kw.works, 'band_labels': kw.band_labels,
            'vocab': {k: v for k, v in kw.v.items()}}
    (d / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    for k in ('surf', 'lem', 'orth', 'pos12', 'cfm', 'gos', 'sent', 'work'):
        np.asarray(kw.a[k], dtype='<i4').tofile(d / f'{k}.i32')


def py_call(kw: K.KwicIndex, c: dict) -> dict:
    """16_kwic_server.py の Handler と同じ引数の扱いで呼ぶ。"""
    sys.path.insert(0, str(HERE))
    import importlib
    srv = importlib.import_module('16_kwic_server')
    h = srv.Handler.__new__(srv.Handler)
    h.kw = kw
    q = c.get('query') or {}
    try:
        if c['path'] == '/api/meta':
            return {'status': 200, 'json': kw.facets()}
        if c['path'] == '/api/passage':
            return {'status': 200, 'json': kw.passage(
                int(q.get('pos', 0)), before=min(400, int(q.get('before', 120))),
                after=min(400, int(q.get('after', 120))), span=max(1, int(q.get('span', 1))))}
        if c['path'] == '/api/variants':
            pos = [p for p in str(q.get('pos', '')).split(',') if p]
            return {'status': 200, 'json': kw.variant_table(
                min_freq=max(1, int(q.get('min_freq', 20))), min_var=max(1, int(q.get('min_var', 2))),
                min_types=max(2, int(q.get('min_types', 2))), pos=pos or None,
                sort=q.get('sort', 'minor'), limit=max(1, min(2000, int(q.get('limit', 300)))))}
        if c['path'] == '/api/search':
            return {'status': 200, 'json': h._search_from(c.get('body') or {})}
    except K.QueryError as e:
        return {'status': 400, 'json': {'error': str(e), 'kind': 'query'}}
    raise ValueError(c['path'])


def same(a, b, where: str, out: list) -> None:
    """2つの JSON を比べ，違うところを out に積む。"""
    # Python の NaN は JSON では null として渡る（JavaScript の JSON.stringify）
    if isinstance(a, float) and math.isnan(a) and b is None:
        return
    if isinstance(a, bool) or isinstance(b, bool):
        if bool(a) != bool(b):
            out.append(f'{where}: {a!r} ≠ {b!r}')
        return
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) or isinstance(b, float):
            # 丸めた値どうしなら，最後の桁の 1 つぶんの差は許す（丸め方の違い）
            r = repr(float(a))
            dec = len(r.split('.')[1]) if '.' in r and 'e' not in r else 9
            tol = 1.01 * 10 ** -min(dec, 9)
            if not (math.isclose(a, b, rel_tol=1e-9, abs_tol=tol)
                    or (math.isnan(a) and math.isnan(b))):
                out.append(f'{where}: {a!r} ≠ {b!r}')
        elif a != b:
            out.append(f'{where}: {a!r} ≠ {b!r}')
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in ('elapsed_ms',):
                continue
            if k not in a or k not in b:
                out.append(f'{where}.{k}: 片方にしか無い')
                continue
            same(a[k], b[k], f'{where}.{k}', out)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f'{where}: 長さ {len(a)} ≠ {len(b)}')
            return
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, f'{where}[{i}]', out)
        return
    if a != b:
        out.append(f'{where}: {a!r} ≠ {b!r}')


def types_same(a: list, b: list) -> bool:
    """件数が同じ語の並びは問わずに，一致した語形の表を比べる。"""
    if [x['hits'] for x in a] != [x['hits'] for x in b]:
        return False
    if not a:
        return True
    edge = a[-1]['hits']
    pa = sorted((x['form'], x['hits']) for x in a if x['hits'] > edge)
    pb = sorted((x['form'], x['hits']) for x in b if x['hits'] > edge)
    return pa == pb


def compare(py: dict, js: dict, label: str) -> list:
    out = []
    if py['status'] != js['status']:
        return [f'{label}: 状態 {py["status"]} ≠ {js["status"]}（{py["json"].get("error", "")} / {js["json"].get("error", "")}）']
    a, b = py['json'], js['json']
    if isinstance(a, dict) and 'types' in a and isinstance(a['types'], list):
        if not types_same(a['types'], b.get('types', [])):
            out.append(f'{label}.types: 一致した語形の表が違う')
        a = {k: v for k, v in a.items() if k != 'types'}
        b = {k: v for k, v in b.items() if k != 'types'}
    same(a, b, label, out)
    return out


def queries(kw: K.KwicIndex, real: bool) -> list[dict]:
    base = dict(context=7, limit=200, collocates=30, coll_window=4)
    qs = []
    words = ['汽車', '言う', '乗る', '汽車|電車', '乗*', '*車', '/動詞', '言う/動詞',
             'L:言う', '汽車 に 乗る', '汽車 が', '* が', 're:^汽.$', 'に']
    if real:
        words += ['恋', '自由', '云ふ', '文明', '/助動詞', 'て いる', '御*', 're:^[ァ-ヴ]{4,}$',
                  '心 が', '/形容詞', 'する/動詞 た']
    sorts = ['position', 'left1', 'left2', 'right1', 'right2', 'year', 'author', 'title']
    for w in words:
        qs.append(dict(base, query=w))
    for s in sorts:
        qs.append(dict(base, query=words[0] if not real else '汽車', sort=s))
    for m in ('logdice', 'mi', 'mi3', 't', 'g2', 'dp_fwd', 'dp_bwd', 'co'):
        qs.append(dict(base, query='汽車', coll_sort=m))
    qs += [
        dict(base, query='汽車', stream='surface'),
        dict(base, query='S:乗つ'),
        dict(base, query='汽車', coll_pos=['動詞']),
        dict(base, query='汽車', coll_pos=['名詞-普通名詞', '助詞']),
        dict(base, query='汽車', coll_focus='乗る'),
        dict(base, query='汽車', coll_focus='乗る', coll_pos=['動詞'], coll_window=2),
        dict(base, query='汽車', exclude_punct=True, bands=[0, 1]),
        dict(base, query='汽車', limit=5, offset=3),
        dict(base, query='言う', variants=True),
        dict(base, query='言う た', variants=True),
        dict(base, query='存在しない語x'),
        dict(base, query='/存在しない品詞'),
        dict(base, query=''),
        dict(base, query='汽車', sort='badsort'),
        dict(base, query='汽車', coll_sort='bad'),
    ]
    auth = sorted({w['author'] for w in kw.works if w['author']})
    if auth:
        qs.append(dict(base, query='汽車', authors=auth[:2]))
    calls = [{'path': '/api/search', 'body': q} for q in qs]
    calls += [{'path': '/api/meta'}]
    for p in (0, 3, kw.n // 2, kw.n - 1):
        calls.append({'path': '/api/passage', 'query': {'pos': str(p), 'before': '50', 'after': '50', 'span': '2'}})
    for s in ('minor', 'types', 'freq'):
        calls.append({'path': '/api/variants', 'query': {'min_freq': '1' if not real else '20', 'sort': s}})
    calls.append({'path': '/api/variants', 'query': {'min_freq': '1', 'pos': '動詞'}})
    return calls


def run(kw: K.KwicIndex, tmp: Path, real: bool) -> int:
    d = tmp / 'arr'
    export_arrays(kw, d)
    calls = queries(kw, real)
    qf = tmp / 'calls.json'
    qf.write_text(json.dumps(calls, ensure_ascii=False), encoding='utf-8')
    r = subprocess.run(['node', '--max-old-space-size=8192', str(HERE / 'kwic_web' / 'run_node.js'), str(d), str(qf)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f'{NG} Node.js での実行に失敗した:\n{r.stderr[-2000:]}')
        return 1
    js = json.loads(r.stdout)
    bad = 0
    for c, j in zip(calls, js):
        label = c['path'] + ' ' + json.dumps(c.get('body') or c.get('query') or {}, ensure_ascii=False)[:90]
        diff = compare(py_call(kw, c), j, label)
        if diff:
            bad += 1
            print(f'{NG} {label}')
            for x in diff[:6]:
                print('        ', x)
    print(f'{OK if not bad else NG} {len(calls) - bad} / {len(calls)} の問い合わせで一致')
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--index', default=None, help='実際の索引（data/kwic）でも確かめる')
    args = ap.parse_args()
    if not shutil.which('node'):
        print('[skip] Node.js（node）が無いので確かめられない。Web 版を直したときは，'
              'Node.js の入ったマシンで実行すること。')
        return 0
    import check_kwic as C
    rc = 0
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        print('合成コーパス:')
        kw = C.build(tmp / 'syn', with_w4=True)
        rc |= run(kw, tmp / 'syn', real=False)
        if args.index:
            print(f'実際の索引: {args.index}')
            rc |= run(K.KwicIndex(args.index), tmp / 'real', real=True)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())

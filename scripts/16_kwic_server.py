#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
16_kwic_server.py
=================
KWIC コンコーダンサをブラウザで使う。**標準ライブラリだけ**で動く小さな
サーバ（外部の Web フレームワークも CDN も使わない）。

    python3 scripts/16_kwic_server.py --open

    → http://127.0.0.1:8765/ が開く

検索・絞り込み・並べ替え・共起語・CSV 書き出し・**用例から本文へ広げる**
までを1枚の画面で行う。画面は ``scripts/kwic_app.html``，中身（索引と検索）は
``scripts/kwic_core.py``。

約束
----
* **127.0.0.1 でだけ待ち受ける。** 研究室の他のマシンからは見えない
  （認証の無い簡易サーバなので，ネットワークに公開しないための仕様である）。``--host`` で
  変えられるが，変えるときは何を公開することになるかを自分で判断すること。
* **語はすべて JSON で渡し，画面では ``textContent`` で入れる。**
  本文はコーパス由来の任意の文字列なので，HTML に文字列連結で差し込まない。
* **由来を画面に出す。** 辞書名・版・索引の作成時刻・作品数・形態素数。
  どの辞書で切った本文を読んでいるか分からない用例は，証拠にならない。
* 検索式が誤っていれば**理由を返す**。黙って0件にしない。

書き出し
--------
``--export`` を付けると，与えた検索式の結果を**そのまま読める HTML**に
書き出す（授業の配布・リポートの付録用。サーバは要らない）。

    python3 scripts/16_kwic_server.py --export my_work/results/kwic_汽車.html \\
        --query 汽車 --stream lemma --limit 200
"""
from __future__ import annotations

import argparse
import getpass
import html
import json
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kwic_core import (API_VERSION, KwicIndex, QueryError,  # noqa: E402
                       code_signature, to_csv)

ROOT = Path(__file__).resolve().parent.parent
APP = Path(__file__).resolve().parent / 'kwic_app.html'
MANUAL = Path(__file__).resolve().parent / 'kwic_manual.html'
MAX_BODY = 1 << 20          # 1 MB。検索式にそれ以上は要らない


class Handler(BaseHTTPRequestHandler):
    kw: KwicIndex = None            # type: ignore[assignment]
    owner = ''
    index_path = ''
    sig = ''
    app_html = b''
    manual_html = b''
    server_version = 'JLitKWIC/1.0'

    # ---- 返し方 ---------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str,
              extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        # 画面は自分が出す1枚だけ。**外の資源は読み込まない**（CDN も使わない）。
        # connect-src 'self' が無いと，自分の /api/ にも fetch できない
        # （画面が真っ白になる）。
        self.send_header('Content-Security-Policy',
                         "default-src 'none'; connect-src 'self'; "
                         "style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                         "img-src data: blob:; form-action 'none'")
        self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                   'application/json; charset=utf-8')

    def log_message(self, fmt, *args):                  # noqa: D102
        # 既定の1行ログは検索ごとに出て煩わしい。必要なときだけ出す
        if os.environ.get('JLIT_KWIC_VERBOSE'):
            super().log_message(fmt, *args)

    # ---- GET ------------------------------------------------------------
    def do_GET(self) -> None:                           # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ('/', '/index.html'):
                # **起動したときに読んだ画面**を出す。ファイルをその都度読むと，
                # 教材を更新したあと「新しい画面＋古いサーバ」の組になり，
                # 画面が知らない形の結果を受け取って壊れる
                self._send(200, self.app_html, 'text/html; charset=utf-8')
            elif u.path in ('/manual', '/manual.html'):
                # 操作マニュアル。画面から別のウィンドウで開く
                if self.manual_html:
                    self._send(200, self.manual_html, 'text/html; charset=utf-8')
                else:
                    self._json({'error': f'マニュアルが無い: {MANUAL}'}, 404)
            elif u.path == '/api/meta':
                self._json(self.kw.facets())
            elif u.path == '/api/whoami':
                # 誰の，どの索引のサーバか。127.0.0.1 はマシンの中の全ユーザーに
                # 共通なので，前の人がログアウトせずに離れる（ファストユーザー
                # スイッチ）と，その人のサーバが同じポートに残っている。ノートブックは
                # これを見て，自分のサーバでなければ別のポートを使う。
                self._json({'user': self.owner, 'index': self.index_path,
                            'sig': self.sig, 'pid': os.getpid(),
                            'api': API_VERSION})
            elif u.path == '/api/passage':
                self._json(self.kw.passage(
                    int(q.get('pos', ['0'])[0]),
                    before=min(400, int(q.get('before', ['120'])[0])),
                    after=min(400, int(q.get('after', ['120'])[0])),
                    span=max(1, int(q.get('span', ['1'])[0]))))
            elif u.path == '/api/variants':
                # コーパス全体で表記の揺れが大きい語彙素の一覧
                pos = [p for p in q.get('pos', [''])[0].split(',') if p]
                self._json(self.kw.variant_table(
                    min_freq=max(1, int(q.get('min_freq', ['20'])[0])),
                    min_var=max(1, int(q.get('min_var', ['2'])[0])),
                    min_types=max(2, int(q.get('min_types', ['2'])[0])),
                    pos=pos or None,
                    sort=q.get('sort', ['minor'])[0],
                    limit=max(1, min(2000, int(q.get('limit', ['300'])[0])))))
            elif u.path == '/api/export.csv':
                res = self._search_from(json.loads(q.get('q', ['{}'])[0]))
                import io
                import csv as _csv
                buf = io.StringIO()
                w = _csv.writer(buf)
                # **1行目に由来を書く**（kwic_core.to_csv と同じ）。どの検索式・
                # どの辞書の索引から何件のうち何件を出したかが分からない表は証拠にならない
                pv = res.get('provenance', {})
                w.writerow(['# query', res['query'], 'stream', res['stream'],
                            'total', res['total'], 'shown', res['shown'],
                            'dictionary', pv.get('dictionary', ''),
                            'index_built', pv.get('built_at', '')])
                w.writerow(['時代区分', '著者', '作品', '初出年', '文体',
                            '左文脈', 'キーワード', '右文脈',
                            '語彙素', '品詞', '作品内位置', '作品の語幹'])
                bl = self.kw.band_labels
                for r in res['rows']:
                    w.writerow([
                        bl[r['band']] if 0 <= r['band'] < len(bl) else '初出年不明',
                        r['author'], r['title'], r['year'], r['style'],
                        ''.join(t['surf'] for t in r['left']),
                        ''.join(t['surf'] for t in r['key']),
                        ''.join(t['surf'] for t in r['right']),
                        ' '.join(t['lem'] for t in r['key']),
                        ' '.join(t['pos'] for t in r['key']),
                        r['in_work'], r['stem']])
                name = urllib.parse.quote(f'kwic_{res["query"]}.csv')
                self._send(200, '﻿'.encode() + buf.getvalue().encode('utf-8'),
                           'text/csv; charset=utf-8',
                           {'Content-Disposition':
                            f"attachment; filename*=UTF-8''{name}"})
            else:
                self._json({'error': f'存在しないパス: {u.path}'}, 404)
        except QueryError as e:
            self._json({'error': str(e), 'kind': 'query'}, 400)
        except Exception as e:                          # noqa: BLE001
            self._json({'error': f'{type(e).__name__}: {e}'}, 500)

    # ---- POST（検索）----------------------------------------------------
    def do_POST(self) -> None:                          # noqa: N802
        u = urllib.parse.urlparse(self.path)
        if u.path != '/api/search':
            self._json({'error': f'存在しないパス: {u.path}'}, 404)
            return
        try:
            n = int(self.headers.get('Content-Length', '0'))
            if n > MAX_BODY:
                self._json({'error': '要求が大きすぎる'}, 413)
                return
            req = json.loads(self.rfile.read(n) or b'{}')
            self._json(self._search_from(req))
        except QueryError as e:
            # **検索式の誤りは理由を返す。** 0件と区別できないのが最悪である
            self._json({'error': str(e), 'kind': 'query'}, 400)
        except Exception as e:                          # noqa: BLE001
            import traceback
            traceback.print_exc()
            self._json({'error': f'{type(e).__name__}: {e}'}, 500)

    def _search_from(self, req: dict) -> dict:
        return self.kw.search(
            req.get('query', ''),
            stream=req.get('stream', 'lemma'),
            context=max(1, min(30, int(req.get('context', 7)))),
            works=req.get('works') or None,
            bands=req.get('bands') or None,
            authors=req.get('authors') or None,
            styles=req.get('styles') or None,
            genres=req.get('genres') or None,
            exclude_punct=bool(req.get('exclude_punct')),
            sort=req.get('sort', 'position'),
            limit=max(1, min(2000, int(req.get('limit', 200)))),
            offset=max(0, int(req.get('offset', 0))),
            sample=max(0, int(req.get('sample', 0))),
            seed=int(req.get('seed', 20260920)),
            collocates=max(0, min(200, int(req.get('collocates', 0)))),
            coll_window=max(1, min(20, int(req.get('coll_window', 4)))),
            coll_sort=str(req.get('coll_sort', 'logdice')),
            coll_pos=[str(x) for x in (req.get('coll_pos') or [])] or None,
            coll_min=max(1, min(1000, int(req.get('coll_min', 2)))),
            variants=bool(req.get('variants', True)))


# ---------------------------------------------------------------------------
# 書き出し（サーバの要らない1枚）
# ---------------------------------------------------------------------------
def export_html(kw: KwicIndex, res: dict, path: str | os.PathLike) -> Path:
    """検索結果を，そのまま読める HTML に書き出す。

    **由来を必ず書く**（検索式・列・辞書・索引の作成時刻・総ヒット数）。
    間引いたときはそのことも書く。
    """
    e = html.escape
    bl = kw.band_labels
    rows = []
    for r in res['rows']:
        band = bl[r['band']] if 0 <= r['band'] < len(bl) else '初出年不明'
        rows.append(
            '<tr><td class="src">' + e(band) + '</td>'
            '<td class="src">' + e(r['author']) + '</td>'
            '<td class="src">' + e(r['title']) + '</td>'
            '<td class="l">' + e(''.join(t['surf'] for t in r['left'])) + '</td>'
            '<td class="k">' + e(''.join(t['surf'] for t in r['key'])) + '</td>'
            '<td class="r">' + e(''.join(t['surf'] for t in r['right'])) + '</td>'
            '<td class="num">' + e(str(r['in_work'])) + '</td></tr>')
    p = kw.prov
    note = (f'検索式「{res["query"]}」／列 '
            f'{"語彙素" if res["stream"] == "lem" else "表層形"}／'
            f'総ヒット {res["total"]:,} 件のうち {res["shown"]:,} 件を表示'
            + (f'／**{res["sample"]} 件に無作為間引き（シード {res["seed"]}）**'
               if res['sampled'] else '')
            + f'／辞書 {p.get("dictionary", "?")} '
              f'{p.get("dictionary_version", "")}／'
              f'索引 {p.get("built_at", "?")} 作成'
              f'（{p.get("works", "?")} 作品・{p.get("tokens", 0):,} 形態素）')
    band_rows = ''.join(
        f'<tr><td>{e(b["label"])}</td><td class="num">{b["hits"]:,}</td>'
        f'<td class="num">{b["tokens"]:,}</td>'
        f'<td class="num">{b["per_10k"]:.3f}</td></tr>'
        for b in res['by_band'])
    out = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<title>KWIC {e(res['query'])}</title>
<style>
body{{margin:0;padding:24px 16px 48px;background:#f7f7f4;color:#1f1e1b;
 font-family:"Hiragino Sans","Noto Sans JP",system-ui,sans-serif;line-height:1.6}}
.wrap{{max-width:1180px;margin:0 auto}}
h1{{font-size:1.15rem;margin:0 0 .2em}}
.sub{{color:#5a5a55;font-size:.82rem;margin:0 0 1em}}
.card{{background:#fff;border:1px solid #d8d7d0;border-radius:10px;padding:14px 16px;
 margin-bottom:14px}}
table{{border-collapse:collapse;width:100%;font-size:.84rem}}
th,td{{padding:4px 7px;border-bottom:1px solid #eceae3;vertical-align:top}}
th{{text-align:left;color:#5a5a55;font-weight:600;position:sticky;top:0;background:#fff}}
td.l{{text-align:right;white-space:nowrap;direction:ltr}}
td.k{{text-align:center;white-space:nowrap;font-weight:700;color:#0d366b;
 background:#eef4fb}}
td.r{{white-space:nowrap}}
td.src{{white-space:nowrap;color:#3d3c37;font-size:.78rem}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;color:#5a5a55}}
.scroll{{max-height:none;overflow:auto}}
.prov{{color:#8a8a82;font-size:.72rem;margin-top:10px}}
</style></head><body><div class="wrap">
<h1>KWIC コンコーダンス — {e(res['query'])}</h1>
<p class="sub">{e(note)}</p>
<div class="card"><div class="scroll"><table>
<thead><tr><th>時代区分</th><th>著者</th><th>作品</th>
<th style="text-align:right">左文脈</th><th style="text-align:center">キーワード</th>
<th>右文脈</th><th style="text-align:right">位置</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="prov">この表は {e(p.get('tsv_dir', ''))} の解析結果から作った。
左右の文脈は前後 {res['context']} 語。</p></div>
<div class="card"><h2 style="font-size:.95rem;margin:.1em 0 .6em">時代区分ごとの分布</h2>
<table><thead><tr><th>時代区分</th><th style="text-align:right">ヒット</th>
<th style="text-align:right">形態素</th><th style="text-align:right">1万語あたり</th>
</tr></thead><tbody>{band_rows}</tbody></table></div>
</div></body></html>
"""
    pp = Path(path)
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(out, encoding='utf-8')
    return pp


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--index', default=str(ROOT / 'data' / 'kwic'))
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--host', default='127.0.0.1',
                    help='既定は 127.0.0.1（このマシンからだけ見える）')
    ap.add_argument('--open', action='store_true', help='ブラウザを開く')
    ap.add_argument('--export', default=None, metavar='HTML',
                    help='検索結果を HTML に書き出して終わる（サーバは起動しない）')
    ap.add_argument('--csv', default=None, metavar='CSV',
                    help='検索結果を CSV に書き出す（--export と併用できる）')
    ap.add_argument('--query', default=None)
    ap.add_argument('--stream', default='lemma', choices=['lemma', 'surface'])
    ap.add_argument('--context', type=int, default=7)
    ap.add_argument('--limit', type=int, default=200)
    ap.add_argument('--sort', default='position')
    args = ap.parse_args()

    kw = KwicIndex(args.index)
    p = kw.prov
    print(f'[idx ] {args.index}')
    print(f'       辞書 {p.get("dictionary")} {p.get("dictionary_version")}'
          f'／作成 {p.get("built_at")}')
    print(f'       {p.get("works")} 作品・{p.get("tokens"):,} 形態素・'
          f'表層形 {p.get("types_surface"):,} 種・語彙素 {p.get("types_lemma"):,} 種')
    if not kw.v2:
        print('[warn] 索引が古い（版 1）。異綴形と，共起語の品詞の中分類による絞り込みは'
              '使えない。\n       python3 scripts/15_kwic_index.py で作り直すこと。')
    if p.get('unmatched_meta'):
        print(f'[warn] メタデータに突合できない作品 '
              f'{len(p["unmatched_meta"])} 点（出典が「メタデータ無し」と出る）')

    if args.export or args.csv:
        if not args.query:
            sys.exit('--export / --csv には --query が必要である')
        res = kw.search(args.query, stream=args.stream, context=args.context,
                        limit=args.limit, sort=args.sort)
        print(f'[hit ] {res["total"]:,} 件（{res["elapsed_ms"]} ms）')
        if args.export:
            print(f'[out ] {export_html(kw, res, args.export)}')
        if args.csv:
            print(f'[out ] {to_csv(kw, res, args.csv)}')
        return 0

    if not APP.exists():
        sys.exit(f'画面のファイルが無い: {APP}')
    Handler.kw = kw
    Handler.owner = getpass.getuser()
    Handler.index_path = os.path.realpath(args.index)
    Handler.sig = code_signature(args.index)
    Handler.app_html = APP.read_bytes()
    Handler.manual_html = MANUAL.read_bytes() if MANUAL.exists() else b''
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f'http://{args.host}:{args.port}/'
    print(f'\n[serve] {url}   （終わるときは Ctrl-C）')
    if args.host == '127.0.0.1':
        print('        このマシンからだけ見える（認証の無い簡易サーバなので'
              'これが既定である）。')
    else:
        print(f'        ⚠ {args.host} で待ち受けている。**同じネットワークの他のマシンから'
              '本文が読める**。何を公開することになるか確かめること。')
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n[stop ] 終了')
    finally:
        httpd.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

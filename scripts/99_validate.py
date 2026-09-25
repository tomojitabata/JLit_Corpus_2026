#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
99_validate.py
==============
コーパスの健全性検査。**分析を始める前に必ず通すこと。**
v1 で見つかった 3 種類の欠陥（重複テクスト・外字欠落・奥付混入）を
自動で検出する。新しく増補したときも同じ検査を通す。

検査項目
--------
1. **重複・高重複** — **文字 12-gram** の包含率で，同一本文や大幅な重複を検出。
   （v1 では『灰色の巨人』と『魔法博士』が検出された）
2. **外字の欠落** — ``※`` ``〓`` の残存。
3. **奥付・入力者注の混入** — 「入力：」「校正：」「青空文庫作成ファイル」等。
4. **踊り字の残存** — ``ゝゞヽヾ`` ``〳〵`` が正規化後も残っていないか。
5. **メタデータの整合** — 必須列の欠損，初出年の範囲，ファイルとの対応。
6. **長さの偏り** — 本文の文字数と，05 のリポートがあれば語数の最長／最短比。
7. **未知語率** — 05 のリポートがあれば，異常値を報告。
8. **抽出の取りこぼし** — ``--xhtml`` を渡すと，青空文庫の原本と
   本文字数を突き合わせ，抽出率が通常を下回る作品を検出する。

使い方
------
    python3 99_validate.py --corpus data/plain/full --meta metadata/corpus_metadata_v2.csv \\
        [--tokenise-report data/tokens/tokenise_report.csv] [--out results/validation.csv]

終了コード: 致命的問題があれば 1，警告のみなら 0。

単位についての注意
------------------
``--corpus`` に渡すのは ``data/plain/full``，すなわち**分かち書きされていない
生テクスト**である。``str.split()`` で語数を数えてはいけない。空白が無いので
段落がそのまま「語」になり，『花月の夜』の語数が 6 と出る。本スクリプトは
本文を**文字数**で数え，語数が必要な箇所は ``--tokenise-report`` から採る。
"""
from __future__ import annotations

import argparse
import csv
import itertools
import os
import re
import sys
from collections import Counter

COLOPHON = ['入力：', '入力:', '校正：', '校正:', '底本：', '底本:',
            '青空文庫作成ファイル', 'ファイル作成：', 'www.aozora.gr.jp',
            '第4水準', '第3水準', 'このファイルは、インターネットの図書館']
ANNOTATION_LEAK = ['はママ］', '］', '［＃', '《', '》']


def shingles(text: str, n: int = 12, step: int = 6) -> set:
    """重複検出用の n-gram 集合。**文字単位**で取る。

    ``text.split()`` で語に切ってはいけない。本検査が受け取るのは
    ``data/plain/full`` すなわち**分かち書きされていない生テクスト**である。
    空白で切ると段落がそのまま「語」になり，8-gram が 8 段落の並びを
    指すことになって，部分的な重複をまったく捉えられない。

    日本語の近重複検出では文字 n-gram が標準的である。12 文字は
    一句に相当し，偶然の一致が起きにくい。メモリを抑えるためハッシュで持つ。
    """
    body = re.sub(r'\s', '', text)
    if len(body) < n:
        return set()
    return {hash(body[i:i + n]) for i in range(0, len(body) - n, step)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', required=True)
    ap.add_argument('--meta', default=None)
    ap.add_argument('--tokenise-report', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--dup-threshold', type=float, default=0.15)
    ap.add_argument('--xhtml', default=None,
                    help='data/aozora/xhtml。渡すと原本と突き合わせて'
                         '抽出の取りこぼしを検出する')
    ap.add_argument('--min-chars', type=int, default=3000,
                    help='これ未満の本文を「短すぎる」として報告する')
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(args.corpus) if f.endswith('.txt'))
    if not files:
        sys.exit(f'テクストが見つからない: {args.corpus}')
    texts = {f: open(os.path.join(args.corpus, f), encoding='utf-8').read() for f in files}

    # ------------------------------------------------------------------
    # 作品名の対応表。**検査結果はファイル名だけでは読めない。**
    # 「000129_004376 の未知語率が 2.95%」と言われても，それが鴎外の
    # 翻訳であることが分からなければ，直すべき欠陥なのか底本の性質なのか
    # 判断できない。--meta があれば作者と作品名を添える。
    # キーの 0 埋めは 06 と同じ落とし穴なので，両方の綴りを登録する。
    # ------------------------------------------------------------------
    labels, orth = {}, {}
    if args.meta and os.path.exists(args.meta):
        with open(args.meta, encoding='utf-8-sig') as fh:
            for m in csv.DictReader(fh):
                pid = (m.get('aozora_person_id') or '').strip()
                wid = (m.get('aozora_work_id') or '').strip()
                lab = f"{m.get('author_ja', '?')}『{m.get('title_aozora', '?')}』"
                keys = []
                if pid and wid:
                    keys += [f'{pid.zfill(6)}_{wid.zfill(6)}', f'{pid}_{wid}']
                if m.get('file_v1'):
                    keys.append(os.path.splitext(m['file_v1'])[0])
                for k in keys:
                    labels[k] = lab
                    orth[k] = m.get('kana_orthography', '')

    def lab(fname):
        """ファイル名に作者・作品名を添える。引けなければファイル名のまま。"""
        stem = fname[:-4] if fname.endswith('.txt') else fname
        return f'{fname} {labels[stem]}' if stem in labels else fname

    findings = []

    def add(level, check, target, detail):
        findings.append({'level': level, 'check': check, 'target': target,
                         'detail': detail})

    # 1. 重複
    print(f'[1/8] 重複検査 ({len(files)} ファイル, {len(files) * (len(files) - 1) // 2:,} 組)')
    sh = {f: shingles(t) for f, t in texts.items()}
    for a, b in itertools.combinations(files, 2):
        A, B = sh[a], sh[b]
        if not A or not B:
            continue
        c = len(A & B) / min(len(A), len(B))
        if c >= args.dup_threshold:
            lv = 'FATAL' if c >= 0.40 else 'WARN'
            add(lv, 'duplicate', f'{a} ↔ {b}', f'文字12-gram 包含率 {c:.1%}')
            print(f'   [{lv}] 包含率 {c:.1%}\n           {lab(a)}\n           {lab(b)}')

    # 2. 外字欠落
    # ※ と 〓 は別のものである。一緒に数えてはいけない。
    #   〓 … 面区点も U+ も字名も解決できなかった外字。復元不能な**欠字**
    #   ※ … 外字マーカーの消し残し（欠陥），あるいは底本そのものが使う記号
    #        （編者注の印，伏字）。後者は本文の一部であって欠陥ではない
    print('[2/8] 外字欠落')
    for f, t in texts.items():
        n_g, n_m = t.count('〓'), t.count('※')
        if n_g:
            lv = 'FATAL' if n_g > 20 else 'WARN'
            add(lv, 'gaiji_unresolved', f, f'復元できなかった外字 〓 が {n_g} 箇所')
            print(f'   [{lv:<5}] {lab(f)}: 〓 {n_g} 箇所')
        if n_m:
            add('WARN', 'gaiji_marker', f,
                f'※ が {n_m} 箇所。底本の記号（編者注・伏字）か'
                '外字マーカーの消し残しかを確認すること')
            print(f'   [WARN ] {lab(f)}: ※ {n_m} 箇所（底本の記号か消し残しか要確認）')

    # 3. 奥付混入
    print('[3/8] 奥付・入力者注の混入')
    for f, t in texts.items():
        flat = t.replace(' ', '').replace('\n', '')
        hits = [m for m in COLOPHON if m.replace(' ', '') in flat]
        if len(hits) >= 2:
            add('FATAL', 'colophon', f, '検出: ' + ' / '.join(hits[:5]))
            print(f'   [FATAL] {lab(f)}: {" / ".join(hits[:5])}')
        leak = [m for m in ('］', '［＃', '《', '》') if m in t]
        if leak:
            add('WARN', 'annotation_leak', f, '注記記号の残存: ' + ' '.join(leak))
            print(f'   [WARN ] {lab(f)}: 注記記号 {" ".join(leak)}')

    # 4. 踊り字
    print('[4/8] 踊り字の残存')
    for f, t in texts.items():
        n = sum(t.count(c) for c in 'ゝゞヽヾ') + len(re.findall(r'／＼|／″＼|〳〵|〴〵', t))
        if n:
            add('WARN', 'odoriji', f, f'仮名踊り字/くの字点が {n} 箇所（正規化漏れ）')
            print(f'   [WARN ] {lab(f)}: {n} 箇所')

    # 5. メタデータ整合
    if args.meta:
        print('[5/8] メタデータ整合')
        with open(args.meta, encoding='utf-8-sig') as fh:
            meta = list(csv.DictReader(fh))
        required = ['id', 'author_ja', 'title_aozora', 'year_first', 'ndc',
                    'genre_main', 'period']
        for m in meta:
            who = f"{m.get('author_ja', '?')}『{m.get('title_aozora', '?')}』"
            for col in required:
                if not m.get(col):
                    add('FATAL', 'meta_missing', m.get('id', '?'),
                        f'{col} が空  {who}')
            try:
                y = int(m['year_first'])
                if not (1850 <= y <= 1970):
                    add('WARN', 'meta_year', m['id'],
                        f'初出年 {y} が想定範囲外  {who}')
            except (ValueError, KeyError):
                add('FATAL', 'meta_year', m.get('id', '?'), '初出年が数値でない')
            if m.get('completeness') in ('DUPLICATE', 'PARTIAL'):
                add('FATAL', 'completeness', m['id'],
                    f"{m['completeness']} {who}: {m.get('note', '')[:60]}")
            # superseded は「壊れている」のではなく「本文を持たない書誌行」。
            # 止める必要はないが，**集計に足すと同じ作品を二重に数える**ので
            # 黙って通してもいけない。
            elif m.get('completeness') == 'superseded':
                add('WARN', 'superseded', m['id'],
                    f'{who} 本文を持たない書誌専用の行。コーパス集計から外すこと')
            elif m.get('completeness') == 'merged':
                add('INFO', 'merged', m['id'],
                    f'{who} 分冊。本文は canonical の巻に統合済み。集計から外れる')
            elif m.get('completeness') == 'too_short':
                add('WARN', 'too_short', m['id'],
                    f'{who} チャンク長に満たないため分析から除外: '
                    f"{m.get('tokens', '?')} 語")
            if m.get('completeness') == 'TBD':
                add('FATAL', 'meta_tbd', m['id'], 'completeness が TBD のまま')
        tbd = [(m['id'], k) for m in meta for k, v in m.items()
               if (v or '').strip() == 'TBD']
        if tbd:
            cnt = Counter(k for _, k in tbd)
            add('FATAL', 'meta_tbd', '-',
                'TBD のセルが %d 件: %s' % (
                    len(tbd), '，'.join(f'{k}×{c}' for k, c in cnt.most_common())))
        ids = [m['id'] for m in meta]
        for i, c in Counter(ids).items():
            if c > 1:
                add('FATAL', 'meta_dup_id', i, f'{c} 回出現')
        ysrc = Counter(m.get('year_source', '') for m in meta)
        if ysrc.get('editor'):
            add('INFO', 'meta_year_source', '-',
                f"初出年が編者補記のもの {ysrc['editor']} 件。典拠の確認が望ましい")
        print(f'   メタデータ {len(meta)} 行を検査')

    # 6. 長さの偏り
    # 単位に注意。この検査が受け取るのは分かち書きされていない生テクストなので，
    # ``len(t.split())`` は段落数であって語数ではない（『花月の夜』が 6 と出る）。
    # 本文は**文字数**で数え，語数は 05 のリポートがあればそちらから採る。
    print('[6/8] 長さの偏り')
    lens = {f: len(re.sub(r'\s', '', t)) for f, t in texts.items()}
    lo, hi = min(lens.values()), max(lens.values())
    ratio = hi / max(1, lo)
    lvl = 'WARN' if ratio > 10 else 'INFO'
    add(lvl, 'length_skew', '-',
        f'本文 最短 {lo:,}字（{min(lens, key=lens.get)}）/ '
        f'最長 {hi:,}字（{max(lens, key=lens.get)}）= {ratio:.1f}倍')
    print(f'   [{lvl}] 本文 最短 {lo:,}字 / 最長 {hi:,}字 = {ratio:.1f}倍')
    print(f'          最短 {lab(min(lens, key=lens.get))}')
    print(f'          最長 {lab(max(lens, key=lens.get))}')

    # 極端に短いファイルは，作品単位の統計（TTR・会話文比率・Delta）を
    # 不安定にする。取得の失敗（本文抽出の打ち切り）であることも多い。
    tiny = {f: n for f, n in lens.items() if n < args.min_chars}
    if tiny:
        for f, n in sorted(tiny.items(), key=lambda kv: kv[1]):
            add('WARN', 'too_short', f,
                f'本文 {n:,} 字。作品単位の統計に耐えない。'
                '本文抽出が途中で切れていないか 03 の出力を確認すること')
            print(f'   [WARN ] {lab(f)}: 本文 {n:,} 字（短すぎる）')
        print(f'          {len(tiny)} 件が {args.min_chars:,} 字未満。'
              '除外するか，短いことを承知で使うかを決めること')

    tok_lens = {}
    if args.tokenise_report and os.path.exists(args.tokenise_report):
        with open(args.tokenise_report, encoding='utf-8-sig') as fh:
            for r in csv.DictReader(fh):
                try:
                    tok_lens[r['file']] = int(r['tokens'])
                except (KeyError, ValueError):
                    pass
    if tok_lens:
        tlo, thi = min(tok_lens.values()), max(tok_lens.values())
        tratio = thi / max(1, tlo)
        tlvl = 'WARN' if tratio > 10 else 'INFO'
        add(tlvl, 'length_skew_tokens', '-',
            f'語数 最短 {tlo:,} / 最長 {thi:,} = {tratio:.1f}倍'
            f'（最長 {max(tok_lens, key=tok_lens.get)}）')
        print(f'   [{tlvl}] 語数 最短 {tlo:,} / 最長 {thi:,} = {tratio:.1f}倍'
              '  ← チャンク分割はこちらで判断する')
        print(f'          最短 {lab(min(tok_lens, key=tok_lens.get))}')
        print(f'          最長 {lab(max(tok_lens, key=tok_lens.get))}')
        if tratio > 10:
            print('          → 06_build_datasets.py の --max-chunks で'
                  'チャンク数を制限すること')
    else:
        print('   [info ] 語数での比較は --tokenise-report を渡すと出る')

    # 7. 未知語率
    if args.tokenise_report and os.path.exists(args.tokenise_report):
        print('[7/8] 未知語率')
        with open(args.tokenise_report, encoding='utf-8-sig') as fh:
            rows = list(csv.DictReader(fh))
        rates = [(r['file'], float(r['unknown_rate'])) for r in rows]
        med = sorted(x for _, x in rates)[len(rates) // 2]

        # **表記別に見ること。** 現代語辞書（cwj）で測ると，旧字旧仮名の
        # 未知語率は新字新仮名の 8 倍ほどになった（実測: 中央値 1.70% 対
        # 0.22%）。全体の中央値と比べると，旧字旧仮名の作品が軒並み
        # WARN になり，本当の異常が埋もれる。表記が同じ仲間の中で外れて
        # いるかどうかを見る。表記は kana_orthography 列から採る。
        #
        # 2026-09-22 に本番の辞書を unidic-novel に替え，全体の未知語率は
        # 0.66% → 0.17% に下がった。表記による差も縮んだが，**層別で見る
        # という方針は変えない**。残る未知語の大半はカタカナ外来語なので，
        # この検査が高い値を指す作品は「前処理が悪い」のではなく
        # 「外来語が多い」可能性がある（docs/dictionary_comparison.md §11）。
        # 判断は 14_unknown_profile.py の分類を見てから下すこと。
        orth = {}
        if args.meta:
            with open(args.meta, encoding='utf-8-sig') as fh:
                for m in csv.DictReader(fh):
                    pid = (m.get('aozora_person_id') or '').strip().zfill(6)
                    wid = (m.get('aozora_work_id') or '').strip().zfill(6)
                    if pid and wid:
                        orth[f'{pid}_{wid}'] = m.get('kana_orthography', '')
        groups = {}
        for f, x in rates:
            groups.setdefault(orth.get(f[:-4] if f.endswith('.txt') else f, ''),
                              []).append((f, x))
        for g, items in sorted(groups.items()):
            vs = sorted(x for _, x in items)
            gmed = vs[len(vs) // 2]
            print(f'   {g or "（表記不明）":<8} n={len(items):>3}  '
                  f'中央値 {gmed:.2%}  最大 {vs[-1]:.2%}')
            for f, x in sorted(items, key=lambda t: -t[1]):
                # 仲間内の中央値の 2.5 倍，かつ 2 ポイント以上離れていたら報告
                if x > max(gmed * 2.5, gmed + 0.02):
                    add('WARN', 'unknown_rate', f,
                        f'未知語率 {x:.2%}（{g or "表記不明"} の中央値 {gmed:.2%}）')
                    print(f'     [WARN ] {lab(f)}: {x:.2%} '
                          f'（同じ表記の中央値 {gmed:.2%}）')
        print(f'   全体の中央値 {med:.2%}')
    else:
        print('[7/8] 未知語率 — リポートが指定されていないため省略')

    # 8. 抽出の取りこぼし（--xhtml を渡したときだけ）
    #
    # **短い作品が「本当に短い」のか「抽出に失敗して短くなった」のかは，
    # 出来上がったテクストだけを見ても分からない。** 原本と突き合わせる。
    # 原本の本文字数に対する抽出後の比は作品によらずほぼ一定なので，
    # 比がその水準を下回る作品を外れ値として拾える。
    # たとえば『夜明け前（五）』が 191,582 字中 12,671 字しか取れていない
    # といった取りこぼしは，この検査ですぐに分かる。
    if args.xhtml and os.path.isdir(args.xhtml):
        print('[8/8] 抽出の取りこぼし（原本との突合）')
        ratios, noext = {}, []
        for f, t in texts.items():
            src = os.path.join(args.xhtml, f[:-4] + '.html')
            if not os.path.exists(src):
                noext.append(f)
                continue
            raw = open(src, 'rb').read()
            for enc in ('shift_jis', 'cp932', 'utf-8'):
                try:
                    s = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                s = raw.decode('shift_jis', 'replace')
            m_ = re.search(r'<div[^>]*class="main_text"[^>]*>(.*?)'
                           r'(?=<div[^>]*class="bibliographical_information"'
                           r'|</body>)', s, re.S)
            src_t = m_.group(1) if m_ else s
            src_t = re.sub(r'<rp>.*?</rp>|<rt>.*?</rt>', '', src_t, flags=re.S)
            src_t = re.sub(r'\s+', '', re.sub(r'<[^>]+>', '', src_t))
            if len(src_t) < 200:
                continue
            ratios[f] = len(re.sub(r'\s', '', t)) / len(src_t)
        if ratios:
            vals = sorted(ratios.values())
            med_r = vals[len(vals) // 2]
            for f, x in sorted(ratios.items(), key=lambda t_: t_[1]):
                if x < med_r * 0.75:
                    add('FATAL', 'extract_loss', f,
                        f'原本に対する抽出率 {x:.2f}（中央値 {med_r:.2f}）。'
                        '本文が途中で切れている疑い')
                    print(f'   [FATAL] {lab(f)}: 抽出率 {x:.2f}'
                          f'（中央値 {med_r:.2f}）')
            print(f'   {len(ratios)} 件を突合。抽出率の中央値 {med_r:.2f}')
        if noext:
            add('WARN', 'no_source', '-',
                f'原本が見つからない作品が {len(noext)} 件')
            print(f'   [WARN ] 原本が見つからない: {len(noext)} 件')
    else:
        print('[8/8] 抽出の取りこぼし — --xhtml を渡すと原本と突き合わせる')

    # ---- まとめ -----------------------------------------------------------
    n_fatal = sum(1 for f in findings if f['level'] == 'FATAL')
    n_warn = sum(1 for f in findings if f['level'] == 'WARN')
    print(f'\n{"=" * 62}')
    print(f'  FATAL {n_fatal} 件 / WARN {n_warn} 件 / INFO '
          f'{sum(1 for f in findings if f["level"] == "INFO")} 件')
    print(f'{"=" * 62}')
    if args.out:
        os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
        with open(args.out, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=['level', 'check', 'target', 'detail'])
            w.writeheader()
            w.writerows(findings)
        print(f'  詳細 → {args.out}')
    if n_fatal:
        print('\n  FATAL があるまま分析に進まないこと。'
              '語数・頻度・トピック分布のすべてが汚染される。')
    return 1 if n_fatal else 0


if __name__ == '__main__':
    sys.exit(main())

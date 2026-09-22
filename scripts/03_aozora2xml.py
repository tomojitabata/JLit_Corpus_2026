#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_aozora2xml.py
================
青空文庫 XHTML → TEI 風 XML。

設計方針
--------
**削除せず，分離する。** v1 コーパスの欠陥（外字592箇所の喪失，奥付の混入，
ルビの不可逆な消去）は，いずれも「本文から要らないものを正規表現で削る」という
方法に起因する。本スクリプトは削らない。すべてをタグで分類し，
何を数えるかは後段（``04_normalise.py``）の設定で決める。

出力する要素
------------
=====================  ====================================================
``<teiHeader>``        書誌（作品ID・NDC・初出・文字遣い・底本・親本）
``<text><body>``       本文
``<head n="1|2|3">``   見出し（大見出し・中見出し・小見出し）
``<p>``                段落
``<said>``             会話。第一階層の会話符で囲まれた部分。地の文との分離に用いる
``<said part="I|M|F">``  段落をまたぐ会話の断片（最初・中間・最後）
``<quote type="embedded">``  ``--embed-min`` 段落以上にわたる引用。
                       書簡・演説・手記など，対話ではなく**埋め込まれた
                       テクスト**。その内側の対話は ``<said>`` で標示する
``<quote>``            第二階層の会話符で囲まれた引用・書名など
``<ruby rt="よみ">``   ルビ付き語。基底文字を内容に，読みを ``@rt`` に置く
``<g ref="1-84-7">``   外字。面区点から復元した文字を内容に置く
``<g ref="unresolved" n="注記本文"/>``  復元できなかった外字
``<note type="...">``  入力者注・校訂注・組版指示（**本文カウントから除外**）
``<ab rend="indent-N">``  字下げブロック
``<lb/>``              底本の改行
=====================  ====================================================

使い方
------
    python3 03_aozora2xml.py --in data/aozora/xhtml --log data/aozora/fetch_log.csv \\
                             --out data/xml [--report data/xml/conversion_report.csv]

依存
----
    pip install lxml
（cssselect は不要。要素の探索は XPath だけで行う）
"""
from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.aozora import resolve_gaiji  # noqa: E402

try:
    from lxml import html as LH
except ImportError:                                            # pragma: no cover
    sys.exit("lxml が必要です:  pip install lxml")


GAIJI_ALT_RE = re.compile(r'※?\(?(?P<ch>[^,()]*),\s*(?P<men>\d)-(?P<ku>\d{1,2})-(?P<ten>\d{1,2})\)?')
JISAGE_RE = re.compile(r'jisage_(\d+)')

NOTE_KINDS = (
    ('底本では', 'textual'),
    ('ママ', 'textual'),
    ('誤植', 'textual'),
    ('字下げ', 'layout'),
    ('傍点', 'layout'),
    ('太字', 'layout'),
    ('横組', 'layout'),
    ('罫囲み', 'layout'),
    ('ページ', 'layout'),
    ('図', 'figure'),
    ('挿絵', 'figure'),
    ('写真', 'figure'),
)


def classify_note(text: str) -> str:
    for k, v in NOTE_KINDS:
        if k in text:
            return v
    if '水準' in text or 'U+' in text or 'unicode' in text.lower():
        return 'gaiji'
    return 'editorial'


class Stats:
    def __init__(self):
        self.gaiji_resolved = 0
        self.gaiji_unresolved = 0
        self.ruby = 0
        self.notes = 0
        self.heads = 0
        self.said = 0            # 会話の数（段落をまたぐものも 1 と数える）
        self.said_parts = 0      # <said> 要素の数（断片化したぶんだけ増える）
        self.said_cont = 0       # 継続引用符による接続の回数
        self.said_unclosed = 0   # 閉じ括弧のない開き括弧の数
        self.quote_embedded = 0  # 段落をまたぐ長大な引用（書簡・演説など）
        self.speech_mark = ''    # その作品の第一階層の会話符
        self.speech_markup = ''  # full / partial / none
        self.extract_ratio = 1.0 # 本文／body 全体。低いと抽出が切れている
        self.div_dropped = 0     # 取り除いた余剰の </div>（底本 HTML の破れ）
        self.old_format = False  # main_text を持たない旧形式。奥付を手で落とした
        self.said_density = 0.0  # 1万字あたりの会話数
        self.chars_body = 0

    def grade_speech_markup(self, min_density: float) -> None:
        """**会話標示が信用できるかどうか**を判定して記録する。

        会話文比率を 0 と測ることと，測れないことは違う。底本が会話符を
        使っていない作品（樋口一葉，戯曲），あるいは ``「`` で起こして
        「と云う」で閉じる口述筆記（福沢諭吉『福翁自伝』は ``「`` 330 に対し
        ``」`` 38）では，比率 0 は**欠測**であって観測値ではない。これを 0 と
        して分析に入れると，文体指標も話法の通時変化も系統的に歪む。

        ``none``     会話符による標示が皆無
        ``partial``  標示はあるが信用できない（未閉が多い／密度が低すぎる）
        ``full``     標示が信用できる
        """
        self.said_density = (self.said / self.chars_body * 10000
                             if self.chars_body else 0.0)
        unclosed_ratio = (self.said_unclosed / (self.said + self.said_unclosed)
                          if (self.said + self.said_unclosed) else 0.0)
        if self.said == 0:
            self.speech_markup = 'none'
        elif unclosed_ratio >= 0.20 or self.said_density < min_density:
            self.speech_markup = 'partial'
        else:
            self.speech_markup = 'full'


def gaiji_from_img(el, st: Stats) -> str:
    """``<img class="gaiji" alt="※(噓, 1-84-7)">`` を <g> に変換する。"""
    alt = el.get('alt', '')
    src = el.get('src', '')
    m = GAIJI_ALT_RE.search(alt)
    ch, ref = None, ''
    if m:
        ref = f"{m['men']}-{int(m['ku'])}-{int(m['ten'])}"
        cand = (m['ch'] or '').strip('※ ')
        ch, kind = resolve_gaiji(f"第3水準{m['men']}-{m['ku']}-{m['ten']}")
        if not ch and cand and len(cand) == 1:
            ch = cand
    if not ch:
        m2 = re.search(r'(\d)-(\d{1,2})-(\d{1,2})', src)
        if m2:
            ref = f"{m2.group(1)}-{int(m2.group(2))}-{int(m2.group(3))}"
            ch, _ = resolve_gaiji(f"第3水準{m2.group(1)}-{m2.group(2)}-{m2.group(3)}")
    if ch:
        st.gaiji_resolved += 1
        return f'<g ref={quoteattr(ref)}>{escape(ch)}</g>'
    st.gaiji_unresolved += 1
    return f'<g ref="unresolved" n={quoteattr(alt or src)}/>'


def gaiji_from_note(note_text: str, st: Stats) -> str | None:
    """``※`` の直後に来る外字注記を <g> にする。"""
    ch, kind = resolve_gaiji(note_text)
    if ch:
        st.gaiji_resolved += 1
        ref = ''
        m = re.search(r'第[34]水準(\d)-(\d{1,2})-(\d{1,2})', note_text)
        if m:
            ref = f"{m.group(1)}-{int(m.group(2))}-{int(m.group(3))}"
        else:
            ref = 'uplus'
        return f'<g ref={quoteattr(ref)}>{escape(ch)}</g>'
    st.gaiji_unresolved += 1
    return None


GAIJI_MARKER = '※'


def find_last_marker(out: list, lookback: int = 6) -> tuple[int, int] | None:
    """直前の外字マーカ ``※`` の位置（出力断片の番号, 文字位置）を返す。

    ``※`` は直前の文字断片の末尾にあるのが普通だが，**ルビの基底文字の中**に
    現れることもある::

        <ruby rt="どんさうじゆけい">※相寿桂</ruby><span class="notes">
        ［＃「女＋（而／大）」、U+5A86、7巻-16-下-14］</span>

    ``out[-1].endswith('※')`` だけを見ていると，この形式で ``※`` が本文に
    残り，森鴎外『伊沢蘭軒』だけで 74 箇所の文字が失われる。そこで出力の
    末尾から数断片だけ遡り，**タグの外にある**最後の ``※`` を探す。
    """
    for i in range(len(out) - 1, max(-1, len(out) - lookback - 1), -1):
        pos, intag = -1, False
        for j, c in enumerate(out[i]):
            if c == '<':
                intag = True
            elif c == '>':
                intag = False
            elif c == GAIJI_MARKER and not intag:
                pos = j
        if pos >= 0:
            return i, pos
    return None


def walk(el, st: Stats, out: list) -> None:
    """XHTML の要素木を走査して XML 断片を積む。"""
    tag = el.tag if isinstance(el.tag, str) else ''
    cls = el.get('class', '') or ''

    if tag == 'ruby':
        rb = ''.join(x.text_content() for x in el.iter() if isinstance(x.tag, str) and x.tag == 'rb')
        rt = ''.join(x.text_content() for x in el.iter() if isinstance(x.tag, str) and x.tag == 'rt')
        if not rb:
            rb = ''.join(t for t in el.itertext()
                         if t not in ('（', '）')) .replace(rt, '')
        st.ruby += 1
        out.append(f'<ruby rt={quoteattr(rt.strip())}>{escape(rb.strip())}</ruby>')
        if el.tail:
            out.append(escape(el.tail))
        return

    if tag == 'img' and 'gaiji' in cls:
        out.append(gaiji_from_img(el, st))
        if el.tail:
            out.append(escape(el.tail))
        return

    if tag == 'span' and 'notes' in cls:
        txt = el.text_content().strip()
        inner = txt.strip('［＃］')
        kind = classify_note(inner)
        # 直前に ※ があるなら，注記の中身が何であれ外字注記である。
        # 青空文庫は JIS X 0213 に無い字を ※［＃小書き片仮名ヲ、160-9］のように
        # **名前だけ**で注記することがあり，面区点も U+ も持たない。
        # kind=='gaiji' を条件にしていると，この形式で ※ が本文に残り，
        # 文字が失われたまま記号 1 字として形態素解析に入ってしまう。
        loc = find_last_marker(out)
        if loc is not None:
            g = gaiji_from_note(inner, st) or \
                f'<g ref="unresolved" n={quoteattr(inner)}/>'
            i, pos = loc
            out[i] = out[i][:pos] + g + out[i][pos + 1:]
        else:
            st.notes += 1
            out.append(f'<note type={quoteattr(kind)}>{escape(inner)}</note>')
        if el.tail:
            out.append(escape(el.tail))
        return

    if tag == 'br':
        out.append('<lb/>')
        if el.tail:
            out.append(escape(el.tail))
        return

    if tag in ('h1', 'h2', 'h3', 'h4', 'h5'):
        level = {'h1': 1, 'h2': 1, 'h3': 2, 'h4': 3, 'h5': 3}[tag]
        st.heads += 1
        out.append(f'<head n="{level}" rend={quoteattr(cls)}>'
                   f'{escape(el.text_content().strip())}</head>')
        if el.tail:
            out.append(escape(el.tail))
        return

    if tag == 'div' and JISAGE_RE.search(cls):
        n = JISAGE_RE.search(cls).group(1)
        out.append(f'<ab rend="indent-{n}">')
        if el.text:
            out.append(escape(el.text))
        for ch in el:
            walk(ch, st, out)
        out.append('</ab>')
        if el.tail:
            out.append(escape(el.tail))
        return

    # 既定：テキストと子要素をそのまま流す
    if el.text:
        out.append(escape(el.text))
    for ch in el:
        walk(ch, st, out)
    if el.tail:
        out.append(escape(el.tail))


QUOTE_OPEN, QUOTE_CLOSE = '「', '」'
DQUOTE_OPEN, DQUOTE_CLOSE = '『', '』'
P_RE = re.compile(r'(<p>)(.*?)(</p>)', re.S)

# 会話の連鎖を打ち切る境界は**見出しだけ**にする。
# 字下げブロック ``<ab>`` を境界に含めると，会話の中に引用詩歌が
# 字下げで入る型（岡本かの子ほか）で会話が復帰できなくなるため。
HEAD_BOUNDARY_RE = re.compile(r'<head')

MAX_PAR_DEFAULT = 150      # 会話が何段落続くまで追跡するか
EMBED_MIN_DEFAULT = 8      # 何段落以上なら「埋め込みテクスト」と見なすか

PAIR_KAGI = re.compile(r'「[^「」]{0,600}」', re.S)
PAIR_FUTAE = re.compile(r'『[^『』]{0,600}』', re.S)


def detect_speech_marks(body: str) -> tuple[str, str, str, str]:
    """作品ごとに**第一階層の会話符**を決める。

    明治〜大正の底本では ``『』`` を第一階層に使い，``「」`` を書名や
    引用語句に充てるものが珍しくない（島崎藤村『破戒』，石川啄木『鳥影』）。
    一律に ``「」`` を会話と見なすと，そうした作品の会話が丸ごと
    取りこぼされる。閉じた対の数を数えて多いほうを第一階層とする。

    戻り値は ``(開き, 閉じ, 第二階層の開き, 第二階層の閉じ)``。
    """
    vis = re.sub(r'<note\b.*?</note>', '', body, flags=re.S)
    vis = re.sub(r'<[^>]+>', '', vis)
    kagi = len(PAIR_KAGI.findall(vis))
    futae = len(PAIR_FUTAE.findall(vis))
    if futae >= 20 and futae > kagi * 1.5:
        return DQUOTE_OPEN, DQUOTE_CLOSE, QUOTE_OPEN, QUOTE_CLOSE
    return QUOTE_OPEN, QUOTE_CLOSE, DQUOTE_OPEN, DQUOTE_CLOSE


def visible_positions(inner: str) -> list[int]:
    """段落内で，タグの外かつ注記の外にある文字の位置を返す。"""
    pos: list[int] = []
    intag = False
    innote = 0
    tagbuf = ''
    for i, c in enumerate(inner):
        if c == '<':
            intag = True
            tagbuf = '<'
            continue
        if intag:
            tagbuf += c
            if c == '>':
                intag = False
                if tagbuf.startswith('<note'):
                    innote += 1
                elif tagbuf.startswith('</note'):
                    innote = max(0, innote - 1)
            continue
        if innote:
            continue
        pos.append(i)
    return pos


def plan_spans(paras: list[tuple[str, bool]], openc: str, closec: str,
               max_par: int, embed_min: int, st: Stats) -> dict:
    """**第一走査**。括弧を文書全体で対応づけ，位置ごとの役割を決める。

    括弧の対応は正規言語ではないので，走査で決めるほかない。ここでは
    次の三つを区別する。

    継続引用符
        長い引用が段落や章をまたぐとき，底本は**各段落（各章）の頭に
        開き括弧を繰り返し，閉じ括弧は引用全体の末尾に一度だけ置く**。
        夏目漱石『こころ』下「先生と遺書」はこの型で，56 章それぞれの
        冒頭に ``「`` があり閉じるのは最終章だけである。繰り返された
        開き括弧を「入れ子の開始」と誤読すると破綻するので，境界の
        直後に来る開き括弧は**同一の引用の継続**として扱う。

    埋め込みテクスト
        ``embed_min`` 段落以上にわたる引用は，対話ではなく書簡・演説・
        手記といった**埋め込まれたテクスト**である。これを ``<said>`` に
        すると『こころ』下の全体が「会話」になってしまうので
        ``<quote type="embedded">`` とし，**その内側の対話を ``<said>``
        として別に標示する**。

    未閉の開き括弧
        どこまでも閉じない ``「`` は，ただの文字として残す。従来は
        巻き添えで同じ連鎖の**閉じている対**まで標示を捨てていたが，
        ここでは未閉の括弧だけを降格し，正しく閉じている対は標示を残す。
    """
    n = len(paras)
    events: list[list[tuple[int, int]]] = []
    lead: list[int | None] = []
    for inner, _ in paras:
        vis = visible_positions(inner)
        events.append([(p, 1 if inner[p] == openc else -1)
                       for p in vis if inner[p] in (openc, closec)])
        fv = next((p for p in vis if not inner[p].isspace()), None)
        lead.append(fv if (fv is not None and inner[fv] == openc) else None)

    chains: list[dict] = []
    stray: list[tuple[int, int]] = []
    repeat: set[tuple[int, int]] = set()
    cur: dict | None = None

    for i in range(n):
        if cur is not None:
            if lead[i] is not None:
                # 段落が開き括弧で始まり，かつ会話が開いたままなら，
                # それは入れ子ではなく**継続引用符の繰り返し**と見る。
                # 日本語の組版で段落頭の開き括弧が入れ子を表すことはまずない。
                cur['cont'] += 1
                cur['clock'] = i
                repeat.add((i, lead[i]))
            elif paras[i][1] or (i - cur['clock']) > max_par:
                cur['last'] = i - 1              # 追跡を打ち切る
                cur['dangling'] = list(cur['stack'])
                chains.append(cur)
                cur = None
        for pos, d in events[i]:
            if (i, pos) in repeat:
                continue                         # 繰り返された開き括弧は素通し
            if d > 0:
                if cur is None:
                    cur = {'first': i, 'clock': i, 'open': (i, pos),
                           'stack': [(i, pos)], 'inner': [], 'cont': 0,
                           'dangling': [], 'close': None, 'last': i}
                else:
                    cur['stack'].append((i, pos))
                    cur['inner'].append(('o', i, pos, len(cur['stack'])))
            else:
                if cur is None:
                    stray.append((i, pos))
                    continue
                lvl = len(cur['stack'])
                cur['stack'].pop()
                if not cur['stack']:
                    cur['close'] = (i, pos)
                    cur['last'] = i
                    chains.append(cur)
                    cur = None
                else:
                    cur['inner'].append(('c', i, pos, lvl))
    if cur is not None:
        cur['last'] = n - 1
        cur['dangling'] = list(cur['stack'])
        chains.append(cur)

    roles: dict[tuple[int, int], tuple] = {}
    PLAIN = ('plain', None, None, None)

    for ch in chains:
        dangling = set(ch['dangling'])
        st.said_cont += ch['cont']
        if dangling:
            st.said_unclosed += len(dangling)
            for p in dangling:
                roles[p] = PLAIN
            promote = 2          # 連鎖そのものは要素にしない
        else:
            extent = ch['last'] - ch['first'] + 1
            if extent >= embed_min:
                elem, attrs = 'quote', ' type="embedded"'
                st.quote_embedded += 1
                promote = 2      # 埋め込みテクストの内側の対話を拾う
            else:
                elem, attrs = 'said', ''
                st.said += 1
                promote = None
            roles[ch['open']] = ('open', elem, attrs, ch['close'][0])
            roles[ch['close']] = ('close', elem, attrs, ch['close'][0])

        pend: list[tuple[int, int]] = []
        for kind, i, pos, lvl in ch['inner']:
            if (i, pos) in dangling:
                continue
            if promote and lvl == promote:
                if kind == 'o':
                    pend.append((i, pos))
                elif pend:
                    oi, op = pend.pop()
                    roles[(oi, op)] = ('open', 'said', '', i)
                    roles[(i, pos)] = ('close', 'said', '', i)
                    st.said += 1
                else:
                    roles[(i, pos)] = PLAIN
            else:
                roles[(i, pos)] = PLAIN
        for p in pend:                           # 内側の未閉括弧
            roles[p] = PLAIN
            st.said_unclosed += 1

    for p in stray:
        roles[p] = PLAIN
    return roles


def render_paragraphs(paras: list[tuple[str, bool]],
                      roles: dict, marks: tuple[str, str, str, str],
                      st: Stats) -> list[str]:
    """**第二走査**。役割表に従って要素を書き出す。

    要素は段落末で必ず閉じ，次の段落の冒頭で開き直す（XML の入れ子を
    段落構造と交差させないため）。断片には TEI の ``@part`` を付け，
    ``I`` 最初の断片／``M`` 中間／``F`` 最後の断片とする。
    """
    _openc, _closec, s_open, s_close = marks
    stack: list[tuple[str, str, int]] = []       # (要素名, 属性, 閉じる段落)
    out: list[str] = []
    for i, (inner, _) in enumerate(paras):
        buf: list[str] = []
        for elem, attrs, close_par in stack:     # 前段落からの継続
            buf.append(f'<{elem}{attrs} part="{"F" if close_par == i else "M"}">')
            if elem == 'said':
                st.said_parts += 1
        intag = False
        innote = 0
        tagbuf = ''
        sec = False
        for pos, c in enumerate(inner):
            if c == '<':
                intag = True
                tagbuf = '<'
                buf.append(c)
                continue
            if intag:
                tagbuf += c
                buf.append(c)
                if c == '>':
                    intag = False
                    if tagbuf.startswith('<note'):
                        innote += 1
                    elif tagbuf.startswith('</note'):
                        innote = max(0, innote - 1)
                continue
            if innote:
                buf.append(c)
                continue
            role = roles.get((i, pos))
            if role is not None:
                kind, elem, attrs, close_par = role
                if kind == 'open':
                    part = '' if close_par == i else ' part="I"'
                    buf.append(f'<{elem}{attrs}{part}>')
                    buf.append(c)
                    stack.append((elem, attrs, close_par))
                    if elem == 'said':
                        st.said_parts += 1
                elif kind == 'close' and stack:
                    buf.append(c)
                    elem, _a, _cp = stack.pop()
                    buf.append(f'</{elem}>')
                else:
                    buf.append(c)
                continue
            if c == s_open and not sec and not stack:
                buf.append('<quote>')
                buf.append(c)
                sec = True
            elif c == s_close and sec:
                buf.append(c)
                buf.append('</quote>')
                sec = False
            else:
                buf.append(c)
        if sec:
            buf.append('</quote>')
        for elem, _attrs, _cp in reversed(stack):
            buf.append(f'</{elem}>')
        out.append(''.join(buf))
    return out


def mark_speech(body: str, st: Stats, max_par: int = MAX_PAR_DEFAULT,
                embed_min: int = EMBED_MIN_DEFAULT,
                marks: tuple[str, str, str, str] | None = None) -> str:
    """段落化された本文に会話・引用の標示を付ける。段落化のあとに呼ぶこと。"""
    if marks is None:
        marks = detect_speech_marks(body)
    openc, closec = marks[0], marks[1]
    st.speech_mark = openc + closec

    tokens: list[tuple] = []
    last = 0
    for m in P_RE.finditer(body):
        if m.start() > last:
            tokens.append(('gap', body[last:m.start()]))
        tokens.append(('p', m.group(1), m.group(2), m.group(3)))
        last = m.end()
    if last < len(body):
        tokens.append(('gap', body[last:]))

    paras: list[tuple[str, bool]] = []
    pending_head = False
    for t in tokens:
        if t[0] == 'gap':
            if HEAD_BOUNDARY_RE.search(t[1]):
                pending_head = True
        else:
            paras.append((t[2], pending_head))
            pending_head = False

    roles = plan_spans(paras, openc, closec, max_par, embed_min, st)
    marked = render_paragraphs(paras, roles, marks, st)

    out: list[str] = []
    k = 0
    for t in tokens:
        if t[0] == 'gap':
            out.append(t[1])
        else:
            out.append(t[1] + marked[k] + t[3])
            k += 1
    return ''.join(out)


AB_SPLIT_RE = re.compile(r'(<ab\b[^>]*>|</ab>)')


def _paras(segment: str) -> list[str]:
    """``<lb/>`` の連なりで区切り，空でない塊を ``<p>`` に包む。"""
    paras = []
    for ch in re.split(r'(?:<lb/>\s*){1,}', segment):
        t = ch.strip()
        if not t:
            continue
        if re.fullmatch(r'(<head[^>]*>.*?</head>|\s)+', t, flags=re.S):
            paras.append(t)                       # 見出しは <p> で包まない
        elif re.fullmatch(r'(<[^>]+>|\s)*', t):
            paras.append(t)                       # タグのみの塊はそのまま
        else:
            paras.append(f'<p>{t}</p>')
    return paras


def split_paragraphs(body: str) -> str:
    """段落化。``<ab>`` ブロックの境界をまたがないようにする。"""
    out = []
    for seg in AB_SPLIT_RE.split(body):
        if AB_SPLIT_RE.fullmatch(seg):
            out.append(seg)                       # <ab …> / </ab> はそのまま
        else:
            out.extend(_paras(seg))
    return '\n'.join(x for x in out if x.strip())


DIV_RE = re.compile(r'<div\b[^>]*>|</div\s*>', re.I)
MAIN_OPEN_RE = re.compile(r'<div[^>]*\bclass\s*=\s*["\']?[^"\'>]*\bmain_text\b', re.I)
COLOPHON_OPEN_RE = re.compile(
    r'<div[^>]*\bclass\s*=\s*["\']?[^"\'>]*\bbibliographical_information\b', re.I)


HR_RE = re.compile(r'<hr\b[^>]*>', re.I)
#: 奥付の始まりを示す語。``<div class="bibliographical_information">`` を
#: 持たない旧形式のファイルでは，これが唯一の手掛かりになる。
COLOPHON_TEXT_RE = re.compile(
    r'底本[：:]|入力[：:]|校正[：:]|青空文庫作成ファイル|表記について')


def trim_old_format(text: str) -> tuple[str, bool]:
    """``main_text`` を持たない旧形式のファイルから本文だけを切り出す。

    2001年ごろまでに公開されたファイルには ``<div class="main_text">``
    どころか ``<div>`` が1つも無いものがある
    （海野十三『敗戦日記』000160_001255 など）。``find_main_text()`` の
    フォールバックは div を class で取り除く実装なので，**div が無い
    ファイルには何も効かず，奥付も外字注記もまるごと本文に入る**。
    99_validate が奥付混入として FATAL を出していたのはこれである。

    旧形式は ``<hr>`` で前付・本文・奥付を区切る。奥付を示す語が直後に
    現れる ``<hr>`` を見つけて，そこから後ろを落とす。前付側も，文書の
    冒頭近くに ``<hr>`` があればそこまでを落とす。
    戻り値は ``(切り出した文字列, 切り出したか)``。
    """
    if MAIN_OPEN_RE.search(text):
        return text, False
    hrs = list(HR_RE.finditer(text))
    if not hrs:
        return text, False
    # **前から**探すこと。奥付の中にも ``<hr>`` があるので，後ろから
    # 探すと奥付の途中で切ってしまい，「底本：」以下がまるごと本文に残る。
    # 奥付を示す語が直後に現れる**最初の** ``<hr>`` が本文の終わりである。
    end = len(text)
    for h in hrs:
        if h.start() < len(text) * 0.2:      # 前付の区切りは飛ばす
            continue
        tail = text[h.end():h.end() + 400]
        if COLOPHON_TEXT_RE.search(re.sub(r'<[^>]+>', '', tail)):
            end = h.start()
            break
    start = 0
    body = re.search(r'<body[^>]*>', text, re.I)
    if body:
        start = body.end()
        head_limit = start + int((end - start) * 0.05)
        for h in hrs:
            if start < h.start() < head_limit:
                start = h.end()
            else:
                break
    if end <= start:
        return text, False
    return text[:start] + '<div class="main_text">' \
        + text[start:end] + '</div></body></html>', True


def repair_main_text_divs(text: str) -> tuple[str, int]:
    """``main_text`` を途中で閉じてしまう余分な ``</div>`` を取り除く。

    青空文庫が生成する XHTML には ``<div>`` と ``</div>` の数が合わない
    ものがある。島崎藤村『夜明け前（五）』は開き 72 に対し閉じ 125 で，
    余分な閉じタグが本文の途中に現れる。lxml は最初の余剰で
    ``<div class="main_text">`` を閉じてしまうので，**本文の 94% が落ちる**。
    例外も警告も出ない。

    そこで解析の前に文字列として直す。``main_text`` の開始から奥付
    （``bibliographical_information``）の直前までを走査し，入れ子の深さを
    1 未満にする ``</div>`` を捨てる。捨てた数を返す。

    元のファイルには手を触れない。直すのは解析に渡す文字列だけである。
    """
    m = MAIN_OPEN_RE.search(text)
    if not m:
        return text, 0
    start = text.find('>', m.start()) + 1
    c = COLOPHON_OPEN_RE.search(text, start)
    end = c.start() if c else len(text)

    # 深さ 1 以下で現れる </div> を拾う。そのうち**最後の一つ**が
    # main_text 本来の閉じタグで，それ以前のものが余剰である。
    closers, depth = [], 1
    for t in DIV_RE.finditer(text, start, end):
        if t.group(0).lower().startswith('</'):
            if depth <= 1:
                closers.append((t.start(), t.end()))
            else:
                depth -= 1
        else:
            depth += 1
    surplus = closers[:-1] if closers else []
    if not surplus:
        return text, 0

    out, pos = [], start
    for a_, b_ in surplus:
        out.append(text[pos:a_])
        pos = b_
    out.append(text[pos:end])
    return text[:start] + ''.join(out) + text[end:], len(surplus)


CHARSET_RE = re.compile(rb'charset\s*=\s*["\']?\s*([A-Za-z0-9_\-]+)', re.I)
XMLDECL_RE = re.compile(r'^\s*<\?xml[^>]*\?>', re.I)


def decode_html(raw: bytes) -> str:
    """青空文庫 XHTML を確実に文字列に直す。

    配布ファイルは Shift_JIS が多く，``<meta ... charset=Shift_JIS>`` で宣言される。
    lxml にバイト列を渡すと宣言を取りこぼして文字化けすることがあるため，
    **こちらで復号してから渡す**。v1 コーパスの由来不明の乱れを繰り返さないための措置。
    """
    m = CHARSET_RE.search(raw[:4096])
    declared = m.group(1).decode('ascii', 'ignore').lower() if m else ''
    aliases = {'shift_jis': 'cp932', 'shift-jis': 'cp932', 'sjis': 'cp932',
               'x-sjis': 'cp932', 'windows-31j': 'cp932',
               'euc-jp': 'euc_jp', 'iso-2022-jp': 'iso2022_jp'}
    order = []
    if declared:
        order.append(aliases.get(declared, declared))
    order += ['cp932', 'utf-8', 'euc_jp']
    for enc in order:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', 'replace')


# class 属性に main_text を含む div を XPath だけで探す。
# （cssselect パッケージに依存しないため。lxml 単体で動く）
HAS_CLASS = ('contains(concat(" ", normalize-space(@class), " "), " {} ")')
MAIN_XPATH = f'//div[{HAS_CLASS.format("main_text")}]'
BIB_XPATH = f'//div[{HAS_CLASS.format("bibliographical_information")}]'


def find_main_text(doc):
    """本文ブロックを返す。見つからなければ body から奥付等を除いたものを返す。

    青空文庫の XHTML はほぼすべて ``<div class="main_text">`` を持つが，
    ごく初期のファイルには無いものがある。その場合は body 全体から
    奥付・ヘッダ・フッタを取り除いて本文とみなす。
    """
    found = doc.xpath(MAIN_XPATH)
    if found:
        return found[0]
    bodies = doc.xpath('//body')
    if not bodies:
        return None
    body = bodies[0]
    for cls in ('bibliographical_information', 'notation_notes',
                'after_text', 'card'):
        for el in body.xpath(f'.//div[{HAS_CLASS.format(cls)}]'):
            el.getparent().remove(el)
    for el in body.xpath('.//h1 | .//h2[@class="header"] | .//hr'):
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
    return body


MIN_DENSITY_DEFAULT = 1.5  # 1万字あたりの会話数がこれ未満なら標示を信用しない


def convert(path: str, meta: dict, st: Stats,
            max_par: int = MAX_PAR_DEFAULT,
            embed_min: int = EMBED_MIN_DEFAULT,
            min_density: float = MIN_DENSITY_DEFAULT) -> str:
    with open(path, 'rb') as fh:
        raw = fh.read()
    text = decode_html(raw)
    # 文字列から解析するので，残っている XML 宣言は取り除く（lxml が拒否する）
    text = XMLDECL_RE.sub('', text, count=1)
    # main_text を持たない旧形式は，奥付を落としてから解析する。
    # 順序に注意: repair_main_text_divs より**先**に呼ぶこと。
    text, st.old_format = trim_old_format(text)
    text, st.div_dropped = repair_main_text_divs(text)
    doc = LH.fromstring(text)
    main = find_main_text(doc)
    if main is None:
        raise ValueError(f'本文のブロックが見つからない: {path}')
    main = [main]
    out: list[str] = []
    walk(main[0], st, out)
    body = ''.join(out)
    # 段落化を先に行う。会話標示は段落の内側で完結させる必要があるため。
    body = split_paragraphs(body)
    body = mark_speech(body, st, max_par=max_par, embed_min=embed_min)

    bib = doc.xpath(BIB_XPATH)
    colophon = ' / '.join(t.strip() for t in bib[0].itertext() if t.strip()) if bib else ''

    st.chars_body = len(re.sub(r'<[^>]+>', '', body))
    st.grade_speech_markup(min_density)

    # 本文抽出が途中で切れていないかを見る。<div class="main_text"> の中に
    # </div> が紛れていると lxml がそこで閉じ，本文の大半が落ちる。
    # 島崎藤村『夜明け前（五）』では body 215,718 字に対し main_text が
    # 13,944 字しか取れていなかった。エラーは出ないので，比で検知する。
    #
    # 比べるのは **同じ尺度どうし**でなければならない。chars_body は注記と
    # ルビの読みを除いた値なので，これを body 全体（ルビの読みも奥付も含む）
    # と比べると，総ルビの作品が不当に低く出る。そこで
    # 「抽出した main_text の text_content」対「奥付等を除いた body の
    # text_content」で比べる。
    st.extract_ratio = 1.0
    try:
        main_len = len(re.sub(r'\s', '', main[0].text_content()))
        doc2 = LH.fromstring(text)
        bodies = doc2.xpath('//body')
        if bodies and main_len:
            bd = bodies[0]
            for cls in ('bibliographical_information', 'notation_notes',
                        'after_text', 'card'):
                for el in bd.xpath(f'.//div[{HAS_CLASS.format(cls)}]'):
                    el.getparent().remove(el)
            whole = len(re.sub(r'\s', '', bd.text_content()))
            if whole:
                st.extract_ratio = round(min(1.0, main_len / whole), 3)
    except Exception:                                           # noqa: BLE001
        pass

    def a(k):
        return quoteattr(str(meta.get(k, '') or ''))

    header = f"""  <teiHeader>
    <fileDesc>
      <titleStmt><title>{escape(meta.get('title_aozora', ''))}</title>
        <author key={a('person_id')}>{escape(meta.get('author_ja', ''))}</author></titleStmt>
      <publicationStmt>
        <idno type="jlit">{escape(meta.get('id', ''))}</idno>
        <idno type="aozora_work">{escape(meta.get('work_id', ''))}</idno>
        <idno type="aozora_person">{escape(meta.get('person_id', ''))}</idno>
        <ref target={a('card_url')}/>
      </publicationStmt>
      <sourceDesc>
        <bibl type="first_publication" n={a('shoshutsu')}>
          <date from={a('year_first')} to={a('year_first_end')}/></bibl>
        <bibl type="base_text">{escape(colophon)}</bibl>
      </sourceDesc>
    </fileDesc>
    <profileDesc>
      <textClass>
        <classCode scheme="NDC">{escape(str(meta.get('ndc', '')))}</classCode>
        <catRef scheme="orthography" target={a('kana_orth')}/>
        <catRef scheme="speech_mark" target={quoteattr(st.speech_mark)}/>
        <catRef scheme="speech_markup" target={quoteattr(st.speech_markup)}/>
      </textClass>
    </profileDesc>
    <encodingDesc>
      <p>青空文庫 XHTML より 03_aozora2xml.py が自動生成。外字は面区点から
         EUC-JIS-2004 経由で復元し &lt;g&gt; に格納。ルビは &lt;ruby @rt&gt; に保持。
         入力者注・組版指示は &lt;note&gt; に分離し本文カウントから除外する。</p>
    </encodingDesc>
  </teiHeader>"""

    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<TEI>\n{header}\n'
           f'  <text><body>\n{body}\n  </body></text>\n</TEI>\n')

    # 整形式（well-formed）であることをここで確かめる。
    # 壊れた XML を書き出すと，後段の 04_normalise.py が読めずに落ちる。
    try:
        ET.fromstring(xml)
    except ET.ParseError as e:
        line, colno = e.position
        snippet = xml.splitlines()[line - 1][max(0, colno - 120):colno + 60] \
            if 0 < line <= len(xml.splitlines()) else ''
        raise ValueError(
            f'生成した XML が整形式でない（{e}）\n'
            f'         該当箇所: …{snippet}…') from e
    return xml


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='indir', required=True)
    ap.add_argument('--log', required=True, help='02_fetch_aozora.py が書いた fetch_log.csv')
    ap.add_argument('--out', required=True)
    ap.add_argument('--report', default=None)
    ap.add_argument('--max-par', type=int, default=MAX_PAR_DEFAULT,
                    help='会話を何段落まで追跡するか（既定 %(default)s）')
    ap.add_argument('--embed-min', type=int, default=EMBED_MIN_DEFAULT,
                    help='何段落以上の引用を埋め込みテクストと見なすか'
                         '（既定 %(default)s）')
    ap.add_argument('--min-said-density', type=float, default=MIN_DENSITY_DEFAULT,
                    help='1万字あたりの会話数がこれ未満なら会話標示を'
                         'partial と判定する（既定 %(default)s）')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    with open(args.log, encoding='utf-8-sig') as fh:
        log = {r['file']: r for r in csv.DictReader(fh)}

    report = []
    for name in sorted(os.listdir(args.indir)):
        if not name.endswith('.html'):
            continue
        meta = dict(log.get(name, {}))
        meta.setdefault('person_id', name.split('_')[0])
        meta.setdefault('work_id', name.split('_')[1].split('.')[0])
        st = Stats()
        try:
            xml = convert(os.path.join(args.indir, name), meta, st,
                          max_par=args.max_par, embed_min=args.embed_min,
                          min_density=args.min_said_density)
        except Exception as e:                                  # noqa: BLE001
            print(f'  [ERR ] {name}: {e}')
            continue
        dest = os.path.join(args.out, name.replace('.html', '.xml'))
        with open(dest, 'w', encoding='utf-8') as fh:
            fh.write(xml)
        row = {'file': os.path.basename(dest),
               'author': meta.get('author_ja', ''), 'title': meta.get('title_aozora', ''),
               'chars_body': st.chars_body, 'heads': st.heads, 'ruby': st.ruby,
               'notes': st.notes,
               'speech_mark': st.speech_mark,
               'speech_markup': st.speech_markup,
               'said': st.said, 'said_parts': st.said_parts,
               'said_density': round(st.said_density, 2),
               'said_cont': st.said_cont,
               'quote_embedded': st.quote_embedded,
               'said_unclosed': st.said_unclosed,
               'extract_ratio': st.extract_ratio,
               'div_dropped': st.div_dropped,
               'gaiji_resolved': st.gaiji_resolved,
               'gaiji_unresolved': st.gaiji_unresolved}
        report.append(row)
        flags = []
        if st.gaiji_unresolved:
            flags.append('外字未解決')
        if st.said_unclosed:
            flags.append(f'未閉の開き括弧{st.said_unclosed}')
        if st.quote_embedded:
            flags.append(f'埋込テクスト{st.quote_embedded}')
        if st.speech_mark != '「」':
            flags.append(f'会話符{st.speech_mark}')
        if st.speech_markup != 'full':
            flags.append(f'会話標示{st.speech_markup}')
        if st.div_dropped:
            flags.append(f'余剰</div>を{st.div_dropped}個除去')
        if st.extract_ratio < 0.7:
            flags.append(f'**本文抽出が {st.extract_ratio:.0%} しかない**')
        flag = ('  <-- ' + '・'.join(flags)) if flags else ''
        print(f'  [ok  ] {row["file"]:<22} 本文{st.chars_body:>8,}字 '
              f'ルビ{st.ruby:>5} 注{st.notes:>4} 会話{st.said:>5} '
              f'外字 復元{st.gaiji_resolved:>4}/未{st.gaiji_unresolved:>3}{flag}')

    if report:
        dest = args.report or os.path.join(args.out, 'conversion_report.csv')
        with open(dest, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=list(report[0].keys()))
            w.writeheader()
            w.writerows(report)
        tot_r = sum(r['gaiji_resolved'] for r in report)
        tot_u = sum(r['gaiji_unresolved'] for r in report)
        tot_c = sum(r['said_unclosed'] for r in report)
        tot_e = sum(r['quote_embedded'] for r in report)
        tot_k = sum(r['said_cont'] for r in report)
        futae = [r['file'] for r in report if r['speech_mark'] == '『』']
        print(f'\n[ok  ] {len(report)} ファイル変換。外字 復元 {tot_r} / 未解決 {tot_u}')
        print(f'[note] 継続引用符での接続 {tot_k} 箇所，'
              f'埋め込みテクスト <quote type="embedded"> {tot_e} 件')
        if futae:
            print(f'[note] 第一階層の会話符が『』の作品 {len(futae)} 件: '
                  + '，'.join(futae))
        if tot_c:
            print(f'[note] 閉じ括弧のない開き括弧 {tot_c} 箇所はただの文字として残した。'
                  'scripts/find_unclosed_quotes.py で位置を確認できる')
        short = [r for r in report if r['extract_ratio'] < 0.7]
        if short:
            print(f'\n[FATAL] 本文抽出が 70% 未満のファイルが {len(short)} 件ある。'
                  'main_text の途中に </div> が紛れている可能性が高い:')
            for r in short:
                print(f"        {r['file']}  {r['author']}『{r['title']}』  "
                      f"抽出率 {r['extract_ratio']:.0%}  本文 {r['chars_body']:,}字")
        for grade in ('none', 'partial'):
            g = [r for r in report if r['speech_markup'] == grade]
            if g:
                print(f'[note] 会話標示 {grade} が {len(g)} 件 — '
                      '会話文比率は 0 ではなく**欠測**として扱うこと: '
                      + '，'.join(f"{r['title']}" for r in g))
        print(f'[ok  ] レポート → {dest}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

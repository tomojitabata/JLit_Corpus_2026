#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
00_build_metadata_v2.py
=======================
近代日本文学コーパス（JLit）メタデータ改訂版 v2 の生成スクリプト。

v1（コーパスdescription.xlsx）の問題点
--------------------------------------
* ``year``  : 底本の刊年を初出年として記録している事例が複数ある
               （例：小栗虫太郎「潜航艇『鷹の城』」1977 → 初出 1935）。
* ``ndc``   : NDC 番号ではなく青空文庫の分類ラベル文字列（「小説、物語」）。
* ``genre`` : 英語ラベル・日本語ラベル・別題・誤綴が混在（"Histrical novel" 等）。
* ``comments``: 語りの視点・叢書形態・文体注記が無統制に混在。
* ``brow``  : high / low の二値。児童書とノンフィクションが分離されていない。

v2 の設計
---------
1 作品 = 1 行。列は「典拠のある書誌情報」「統制語彙による分類」「テクスト実測値」
「既知の問題」の 4 ブロックに分ける。典拠は ``*_source`` 列で明示する。

実行:
    python3 00_build_metadata_v2.py --corpus <dir> --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import unicodedata
from collections import Counter

# --------------------------------------------------------------------------
# 1. 典拠付き書誌レコード
#    src = 'card'   : 青空文庫図書カードの記載どおり
#    src = 'editor' : カードに記載がないため編者（田畑）が補ったもの。要確認。
# --------------------------------------------------------------------------
# (file_stem, author, title_aozora, person_id, work_id, ndc, y_from, y_to,
#  kana_orth, year_src, genre_main, genre_sub, form, audience, register,
#  narration, serial, completeness, note)
RECORDS = [
 ("中島敦_光と風と夢","中島敦","光と風と夢","000119","1743","913",1942,1942,"新字新仮名","editor",
  "Fiction","Historical","novel","general","canonical","mixed","magazine","complete",
  "初出『文學界』1942年5月。カードに初出欄なし"),
 ("乱歩_宇宙怪人","江戸川乱歩","宇宙怪人","001779","56674","K913",1953,1953,"新字新仮名","card",
  "Fiction","Juvenile-Mystery","novel","juvenile","popular","third","magazine","complete",
  "「少年」1953年1〜12月号"),
 ("乱歩_少年探偵団","江戸川乱歩","少年探偵団","001779","56669","K913",1937,1937,"新字新仮名","card",
  "Fiction","Juvenile-Mystery","novel","juvenile","popular","third","magazine","complete",
  "「少年倶楽部」1937年1〜12月号"),
 ("乱歩_灰色の巨人","江戸川乱歩","灰色の巨人","001779","56676","K913",1955,1955,"新字新仮名","card",
  "Fiction","Juvenile-Mystery","novel","juvenile","popular","third","magazine","DUPLICATE",
  "【重大】当該ファイルの本文は『魔法博士』と同一。『灰色の巨人』は未収録。要再取得"),
 ("乱歩_魔法博士","江戸川乱歩","魔法博士","001779","56679","K913",1956,1956,"新字新仮名","card",
  "Fiction","Juvenile-Mystery","novel","juvenile","popular","third","magazine","complete",
  "「少年」1956年1〜12月号"),
 ("啄木_鳥影","石川啄木","鳥影","000153","46947","913",1908,1908,"新字旧仮名","card",
  "Fiction","Naturalist","novel","general","canonical","third","newspaper","complete",
  "「東京毎日新聞」1908年11〜12月。旧仮名・仮名踊り字を含む"),
 ("坂口安吾_JIRORIの女","坂口安吾","ジロリの女","001095","42827","913",1948,1948,"新字新仮名","card",
  "Fiction","Modern","novella","general","canonical","first","magazine","complete",
  "ファイル名の 'JIRORI' はローマ字化。正題は「ジロリの女」"),
 ("坂口安吾_不連続殺人","坂口安吾","不連続殺人事件","001095","42626","913",1947,1948,"新字新仮名","card",
  "Fiction","Detective","novel","general","middlebrow","first","magazine","complete",
  "「日本小説」1947年8月〜1948年8月"),
 ("坂口安吾_二流の人","坂口安吾","二流の人","001095","42919","913",1947,1947,"新字旧仮名","card",
  "Fiction","Historical","novella","general","canonical","third","book","complete",
  "カード記載は九州書房版1947年。雑誌初出（1941「文體」）説あり。v1 の1944は典拠不明"),
 ("坂口安吾_復員殺人","坂口安吾","復員殺人事件","001095","43163","913",1949,1950,"新字新仮名","card",
  "Fiction","Detective","novel","general","middlebrow","first","magazine","incomplete",
  "「座談」1949〜1950年、未完"),
 ("坂口安吾_金銭無情","坂口安吾","金銭無情","001095","42866","913",1947,1947,"新字旧仮名","card",
  "Fiction","Modern","cycle","general","canonical","third","magazine","complete",
  "「金銭無情」「失恋難」「夜の王様」「王様失脚」4篇の連作。v1 の 'Experimental?' を改める"),
 ("壷井栄_二十四の瞳","壺井栄","二十四の瞳","001875","57856","913",1952,1952,"新字新仮名","card",
  "Fiction","Modern","novel","general","middlebrow","third","magazine","complete",
  "ファイル名の「壷」は正字「壺」"),
 ("壷井栄_妻の座","壺井栄","妻の座","001875","60071","913",1947,1949,"新字新仮名","card",
  "Fiction","Modern","novel","general","middlebrow","third","magazine","complete",
  "「新日本文学」1947年8月、1949年2〜4月・7月"),
 ("多喜二_不在地主","小林多喜二","不在地主","000156","49181","913",1929,1929,"新字新仮名","card",
  "Fiction","Proletarian","novella","general","canonical","third","magazine","complete",
  "「中央公論」1929年11月号ほか。外字欠落27件"),
 ("多喜二_党生活者","小林多喜二","党生活者","000156","833","913",1933,1933,"新字新仮名","editor",
  "Fiction","Proletarian","novella","general","canonical","first","magazine","complete",
  "初出は「転換時代」の題で『中央公論』1933年4・5月。カードに初出欄なし"),
 ("多喜二_工場細胞","小林多喜二","工場細胞","000156","1466","913",1930,1930,"新字新仮名","card",
  "Fiction","Proletarian","novella","general","canonical","third","magazine","complete",
  "「改造」1930年4〜6月号。仮名踊り字23件"),
 ("多喜二_蟹工船","小林多喜二","蟹工船","000156","1465","913",1929,1929,"新字新仮名","card",
  "Fiction","Proletarian","novella","general","canonical","third","magazine","complete",
  "「戦旗」1929年5・6月号"),
 ("夢野久作_DOGRA","夢野久作","ドグラ・マグラ","000096","2093","913",1935,1935,"新字新仮名","editor",
  "Fiction","Detective","novel","general","middlebrow","first","book","complete",
  "1935年1月 松柏館書店刊（自費出版）。カードに初出欄なし"),
 ("夢野久作_少女地獄","夢野久作","少女地獄","000096","935","913",1936,1936,"新字新仮名","card",
  "Fiction","Detective","collection","general","middlebrow","mixed","book","complete",
  "「少女地獄」黒白書房1936年3月。3篇の作品集"),
 ("夢野久作_押絵の奇蹟","夢野久作","押絵の奇蹟","000096","930","913",1929,1929,"新字新仮名","card",
  "Fiction","Detective","novella","general","middlebrow","first","magazine","complete",
  "「新青年」1929年1月。書簡体"),
 ("太宰_人間失格","太宰治","人間失格","000035","301","913",1948,1948,"新字新仮名","editor",
  "Fiction","Modern","novel","general","canonical","first","magazine","complete",
  "初出『展望』1948年6〜8月"),
 ("太宰_斜陽","太宰治","斜陽","000035","1565","913",1947,1947,"新字新仮名","editor",
  "Fiction","Modern","novel","general","canonical","first","magazine","complete",
  "初出『新潮』1947年7〜10月"),
 ("室生犀星_蜜のあわれ","室生犀星","蜜のあわれ","001579","53503","913",1959,1959,"新字新仮名","card",
  "Fiction","Experimental","novella","general","canonical","dialogue","magazine","complete",
  "「新潮」1959年1〜4月。全篇対話体。v1 の「超現実主義的」を改める"),
 ("宮本百合子_二つの庭","宮本百合子","二つの庭","000311","2011","913",1947,1947,"新字新仮名","card",
  "Fiction","Modern","novel","general","canonical","third","magazine","complete",
  "「中央公論」1947年1・3〜9月号。三部作の第2部"),
 ("宮本百合子_伸子","宮本百合子","伸子","000311","2015","913",1924,1926,"新字新仮名","card",
  "Fiction","Modern","novel","general","canonical","third","magazine","complete",
  "「改造」1924年9月〜1926年9月。v1 の1927は単行本年"),
 ("宮本百合子_播州平野","宮本百合子","播州平野","000311","2013","913",1946,1947,"新字新仮名","editor",
  "Fiction","Modern","novel","general","canonical","first","magazine","complete",
  "「新日本文学」創刊号（1946年3月）ほか。カードに年次記載なし"),
 ("宮本百合子_道標","宮本百合子","道標","000311","2012","913",1947,1950,"新字新仮名","card",
  "Fiction","Modern","novel","general","canonical","third","magazine","complete",
  "「展望」1947年10月〜1950年12月。外字欠落86件"),
 ("小栗虫太郎_潜航艇「鷹の城」","小栗虫太郎","潜航艇「鷹の城」","000125","43656","913",1935,1935,"新字新仮名","card",
  "Fiction","Detective","novella","general","middlebrow","first","magazine","complete",
  "【修正】v1 の1977は底本（現代教養文庫）刊年。初出は「新青年」1935年4〜5月号"),
 ("小栗虫太郎_白蟻","小栗虫太郎","白蟻","000125","666","913",1935,1935,"新字新仮名","editor",
  "Fiction","Detective","novella","general","middlebrow","first","magazine","complete",
  "「新青年」1935年。カードに初出欄なし"),
 ("小栗虫太郎_黒死館","小栗虫太郎","黒死館殺人事件","000125","1317","913",1934,1934,"新字新仮名","card",
  "Fiction","Detective","novel","general","middlebrow","first","magazine","complete",
  "「新青年」1934年4〜12月号。外字欠落19件、ラテン文字1098字"),
 ("岡本かの子_仏教人生読本","岡本かの子","仏教人生読本","000076","52342","180",1934,1934,"新字新仮名","editor",
  "Nonfiction","Religious-Didactic","treatise","general","middlebrow","none","book","complete",
  "【修正】NDC 180（仏教）。v1 の「自己啓発？/Essay?」を改める"),
 ("岡本かの子_母子叙情","岡本かの子","母子叙情","000076","1282","913",1937,1937,"新字新仮名","card",
  "Fiction","Modern","novella","general","canonical","third","magazine","complete",
  "「文学界」1937年3月号"),
 ("岡本かの子_生々流転","岡本かの子","生々流転","000076","45641","913",1939,1940,"新字新仮名","editor",
  "Fiction","Modern","novel","general","canonical","first","magazine","incomplete",
  "作者急逝により未完。仮名踊り字191件・外字欠落20件"),
 ("徳冨蘆花_不如帰","徳冨蘆花","小説 不如帰","000280","1706","913",1898,1899,"新字新仮名","editor",
  "Fiction","Melodrama","novel","general","popular","third","newspaper","complete",
  "「國民新聞」1898年11月〜1899年5月。文語的措辞が濃厚（文語標識156.5/万語）。外字欠落47件"),
 ("林芙美子_放浪記初出","林芙美子","放浪記（初出）","000291","45649","913",1928,1930,"新字新仮名","card",
  "Nonfiction","Autobiographical-Diary","diary-novel","general","middlebrow","first","magazine","complete",
  "「女人藝術」1928年10月〜1930年10月。初出形。校訂注の混入1件（「裏」はママ］）"),
 ("林芙美子_新版放浪記","林芙美子","新版 放浪記","000291","1813","913",1928,1930,"新字新仮名","card",
  "Nonfiction","Autobiographical-Diary","diary-novel","general","middlebrow","first","book","revised",
  "初出は上に同じ。本テクストは1949年の改稿版（新版）。版差研究用のペア"),
 ("海野十三_地球要塞","海野十三","地球要塞","000160","3239","913",1940,1941,"新字新仮名","card",
  "Fiction","SF","novel","general","popular","first","magazine","complete",
  "「譚海」1940年8月〜1941年2月号"),
 ("海野十三_怪塔王","海野十三","怪塔王","000160","3370","K913",1938,1938,"新字新仮名","card",
  "Fiction","Juvenile-SF","novel","juvenile","popular","third","newspaper","complete",
  "「東日本小学生新聞」1938年4〜12月"),
 ("海野十三_敗戦日記","海野十三","海野十三敗戦日記","000160","1255","915",1944,1945,"新字新仮名","editor",
  "Nonfiction","Diary","diary","general","documentary","first","book","complete",
  "【重大】青空文庫の奥付・入力者注・外字一覧が本文末尾に残存。外字欠落110件。要再構築"),
 ("海野十三_浮かぶ飛行島","海野十三","浮かぶ飛行島","000160","3527","K913",1938,1938,"新字新仮名","card",
  "Fiction","Juvenile-SF","novel","juvenile","popular","third","magazine","complete",
  "「少年倶楽部」1938年1〜12月"),
 ("海野十三_火星兵団","海野十三","火星兵団","000160","3368","K913",1939,1940,"新字新仮名","card",
  "Fiction","Juvenile-SF","novel","juvenile","popular","third","newspaper","complete",
  "「大毎小学生新聞」「東日本小学生新聞」1939年9月〜1940年12月"),
 ("漱石_こころ","夏目漱石","こころ","000148","773","913",1914,1914,"新字新仮名","card",
  "Fiction","Modern","novel","general","canonical","first","newspaper","complete",
  "「朝日新聞」1914年4〜8月"),
 ("漱石_三四郎","夏目漱石","三四郎","000148","794","913",1908,1908,"新字新仮名","card",
  "Fiction","Bildungsroman","novel","general","canonical","third","newspaper","complete",
  "「朝日新聞」1908年9月1日〜12月29日"),
 ("漱石_坊っちゃん","夏目漱石","坊っちゃん","000148","752","913",1906,1906,"新字新仮名","card",
  "Fiction","Modern","novella","general","canonical","first","magazine","complete",
  "「ホトトギス」1906年4月"),
 ("漱石_草枕","夏目漱石","草枕","000148","776","913",1906,1906,"新字新仮名","card",
  "Fiction","Aesthetic","novella","general","canonical","first","magazine","complete",
  "「新小説」1906年9月。漢文脈が濃い"),
 ("福翁_学問","福沢諭吉","学問のすすめ","000296","47061","370",1872,1876,"新字新仮名","card",
  "Nonfiction","Enlightenment-Didactic","treatise","general","canonical","none","book","complete",
  "初編1872年、全17編1876年完結。文語標識407.3/万語 ＝ 本コーパス中最も文語的"),
 ("福翁_自伝","福沢諭吉","福翁自伝","000296","1864","289",1898,1899,"新字新仮名","card",
  "Nonfiction","Autobiography","autobiography","general","canonical","first","newspaper","complete",
  "「時事新報」1898年7月〜1899年2月。口述筆記による言文一致の早期例"),
 ("綺堂_両国の秋","岡本綺堂","両国の秋","000082","478","913",1916,1916,"新字新仮名","editor",
  "Fiction","Ghost-Story","short-story","general","popular","third","magazine","complete",
  "カードに初出欄なし。v1 の1916を暫定的に維持、要確認"),
 ("綺堂_半七捕物帳","岡本綺堂","半七捕物帳 69 白蝶怪","000082","964","913",1936,1936,"新字新仮名","editor",
  "Fiction","Detective-Edo","short-story","general","popular","first","magazine","complete",
  "【修正】シリーズ第69話。v1 の1917はシリーズ開始年。白蝶怪は後期作。要確認"),
 ("綺堂_玉藻の前","岡本綺堂","玉藻の前","000082","480","913",1917,1918,"新字新仮名","editor",
  "Fiction","Historical-Fantasy","novel","general","popular","third","newspaper","complete",
  "カードに初出欄なし"),
 ("綺堂_青蛙堂鬼談","岡本綺堂","青蛙堂鬼談","000082","1307","913",1926,1926,"新字新仮名","editor",
  "Fiction","Ghost-Story","collection","general","popular","mixed","magazine","complete",
  "枠物語形式の怪談集。カードに初出欄なし"),
 ("綺堂_飛騨の怪談","岡本綺堂","飛騨の怪談","000082","49682","913",1912,1913,"新字新仮名","card",
  "Fiction","Ghost-Story","novel","general","popular","first","newspaper","complete",
  "【修正】「やまと新聞」1912年11月13日〜1913年1月21日。v1 の1913は完結年"),
 ("芥川_偸盗","芥川竜之介","偸盗","000879","31","913",1917,1917,"新字新仮名","card",
  "Fiction","Historical","novella","general","canonical","third","magazine","complete",
  "「中央公論」1917年4・7月"),
 ("芥川_文芸的な","芥川竜之介","文芸的な、余りに文芸的な","000879","26","914",1927,1927,"新字旧仮名","card",
  "Nonfiction","Criticism","essay","general","canonical","first","magazine","complete",
  "【修正】NDC 914（随筆・評論）。「改造」1927年4〜8月"),
 ("藤村_千曲川","島崎藤村","千曲川のスケッチ","000158","1503","914",1911,1912,"新字新仮名","editor",
  "Nonfiction","Sketch-Essay","essay","general","canonical","first","magazine","complete",
  "【修正】NDC 914。写生文。カードに初出欄なし"),
 ("藤村_夜明け前","島崎藤村","夜明け前（第一部上〜第二部下）","000158","1504-1507","913",1929,1935,"新字新仮名","editor",
  "Fiction","Historical","novel","general","canonical","third","magazine","complete",
  "【修正】「中央公論」1929〜1935年。v1 の1935は完結年。青空文庫では4カードに分割"),
 ("藤村_家","島崎藤村","家（下）","000158","1510","913",1910,1911,"新字新仮名","editor",
  "Fiction","Modern","novel","general","canonical","third","newspaper","PARTIAL",
  "【重大】下巻のみ収録。上巻（card1509）が欠落。作品全体としては不完全"),
 ("藤村_新生","島崎藤村","新生","000158","843","913",1918,1919,"新字新仮名","card",
  "Fiction","Modern","novel","general","canonical","third","newspaper","complete",
  "【修正】「東京朝日新聞」1918年5月〜1919年10月。v1 の1919は完結年"),
 ("藤村_破戒","島崎藤村","破戒","000158","1502","913",1906,1906,"新字旧仮名","card",
  "Fiction","Naturalist","novel","general","canonical","third","book","complete",
  "緑蔭叢書1906年3月（自費出版）。新字旧仮名・仮名踊り字218件"),
 ("鴎外_ヰタ","森鴎外","ヰタ・セクスアリス","000129","695","913",1909,1909,"新字新仮名","card",
  "Fiction","Modern","novella","general","canonical","first","magazine","complete",
  "「昴」1909年7月"),
 ("鴎外_伊沢蘭軒","森鴎外","伊沢蘭軒","000129","2084","913",1916,1917,"新字旧仮名","card",
  "Nonfiction","Historical-Biography","biography","general","canonical","first","newspaper","complete",
  "史伝。漢字率51.3%（最高）。外字欠落166件（最多）"),
 ("鴎外_大塩平八郎","森鴎外","大塩平八郎","000129","2298","913",1914,1914,"新字旧仮名","editor",
  "Fiction","Historical","novella","general","canonical","third","magazine","complete",
  "初出『中央公論』1914年1月。カードに初出欄なし"),
 ("鴎外_渋江抽斎","森鴎外","渋江抽斎","000129","2058","913",1916,1916,"新字新仮名","card",
  "Nonfiction","Historical-Biography","biography","general","canonical","first","newspaper","complete",
  "「大阪毎日新聞」「東京日日新聞」1916年1月13日〜5月17日。外字欠落17件"),
 ("鴎外_青年","森鴎外","青年","000129","2522","913",1910,1911,"新字新仮名","card",
  "Fiction","Bildungsroman","novel","general","canonical","third","magazine","complete",
  "【修正】「昴」1910年3月〜1911年8月。v1 の1910は開始年のみ。ラテン文字1895字（独語）"),
]

AUTHOR_INFO = {
 # author: (reading, sex, birth, death)
 "中島敦": ("なかじま あつし","M",1909,1942),
 "江戸川乱歩": ("えどがわ らんぽ","M",1894,1965),
 "石川啄木": ("いしかわ たくぼく","M",1886,1912),
 "坂口安吾": ("さかぐち あんご","M",1906,1955),
 "壺井栄": ("つぼい さかえ","F",1899,1967),
 "小林多喜二": ("こばやし たきじ","M",1903,1933),
 "夢野久作": ("ゆめの きゅうさく","M",1889,1936),
 "太宰治": ("だざい おさむ","M",1909,1948),
 "室生犀星": ("むろう さいせい","M",1889,1962),
 "宮本百合子": ("みやもと ゆりこ","F",1899,1951),
 "小栗虫太郎": ("おぐり むしたろう","M",1901,1946),
 "岡本かの子": ("おかもと かのこ","F",1889,1939),
 "徳冨蘆花": ("とくとみ ろか","M",1868,1927),
 "林芙美子": ("はやし ふみこ","F",1903,1951),
 "海野十三": ("うんの じゅうざ","M",1897,1949),
 "夏目漱石": ("なつめ そうせき","M",1867,1916),
 "福沢諭吉": ("ふくざわ ゆきち","M",1835,1901),
 "岡本綺堂": ("おかもと きどう","M",1872,1939),
 "芥川竜之介": ("あくたがわ りゅうのすけ","M",1892,1927),
 "島崎藤村": ("しまざき とうそん","M",1872,1943),
 "森鴎外": ("もり おうがい","M",1862,1922),
}

NDC_LABEL = {
 "913": "日本文学／小説・物語",
 "K913": "児童書／日本の小説・物語",
 "914": "日本文学／評論・随筆",
 "915": "日本文学／日記・書簡・紀行",
 "289": "伝記／個人伝記",
 "370": "教育（総記）",
 "180": "仏教（総記）",
 # 増補 45 点で新たに要った分類。空欄のままだと
 # ndc_label で層別したときにその行だけ落ちる。
 "911": "日本文学／詩歌",
 "912": "日本文学／戯曲",
 "908": "文学／叢書・全集（翻訳詩集を含む）",
 "949": "その他の諸文学／小説（翻訳）",
 "983": "ロシア・ソヴィエト文学／小説",
 "121": "哲学／日本思想",
 "367": "社会科学／家族・女性・性問題",
 "382": "民俗学／風俗史・民俗誌",
 "779": "演芸／落語・寄席芸（口演速記）",
}


def period_of(year: int) -> str:
    """初出年を文学史的時代区分に写像する。"""
    if year < 1887:
        return "1_明治前期(〜1886)"
    if year < 1900:
        return "2_明治中期(1887-1899)"
    if year < 1912:
        return "3_明治後期(1900-1911)"
    if year < 1926:
        return "4_大正(1912-1925)"
    if year < 1945:
        return "5_昭和戦前(1926-1944)"
    return "6_昭和戦後(1945-)"


# 明治前期（〜1886）は青空文庫の所蔵そのものが薄く，どれだけ増補しても
# 他の区分と同じ規模にはならない。**標本の量が足りない区分でモデルを学習すると，
# 時代ではなく一人の作家の文体を学習する。** そこで前期を中期に畳んだ
# 5区分を用意し，記述統計（Step 1・4）は6区分，学習（Step 6）は5区分と
# 使い分ける。区分の根拠が文学史ではなく**標本の量**であることを，
# 列を分けることで明示する。design_targets.yaml の bands5 と対応。
BAND5 = {
    "1_明治前期(〜1886)": "1_明治前期〜中期(〜1899)",
    "2_明治中期(1887-1899)": "1_明治前期〜中期(〜1899)",
}


def period5_of(period: str) -> str:
    """6区分の ``period`` を，学習に使う5区分へ畳む。

    畳むのは明治前期と中期だけで，他はそのまま通す。空や TBD も
    そのまま返す（呼び手が「空の行がある」ことに気づけるように）。
    """
    return BAND5.get(period, period)


BUNGO = ['なり','けり','ごとし','べし','ざる','ざり','たり','候','けむ','らむ',
         'しむ','ども','なれ','べから','ごとき','ざれ','たる','ぬる']
# 口語標識。**文語標識と機能で対になるもの**を選ぶ。分母を大きくするのが
# 目的ではなく，文語と口語を同じ土俵で比べるのが目的である。
#
#   断定  なり・なれ        ↔  だ・である・です・だっ
#   否定  ざる・ざり・ざれ  ↔  ない・なかっ・ません
#   推量  べし・らむ・けむ  ↔  だろ・でしょ
#
# ⚠ **「た」は入れない（2026-09-27 決定）。** 過去の助動詞は文語の
#   「き・けり・たり」と対をなすので入れたくなるが，会話に偏って出る。
#   地の文が文語・会話が口語の**雅俗折衷体**では，「た」を数えると
#   会話の量を測っているだけになり，比が沈む。実測すると樋口一葉
#   『たけくらべ』の比は 0.455 → 0.346，『にごりえ』は 0.369 → 0.247 まで
#   落ち，B 群と見分けがつかなくなった。「だ・ない・なかっ・だろ」は
#   地の文にも会話にも出るので，文体の指標として素直である。
KOGO = ['です','ます','である','だっ','ました','でしょ','ません','ている','ていた','じゃ',
        'だ','ない','なかっ','だろ']
P1 = ['私','僕','俺','おれ','わたし','わたくし','あたし','自分','わし']
P3 = ['彼','彼女','かれ']


class NotTokenised(ValueError):
    """分かち書きされていないテクストを渡されたときの例外。"""


def count_markers(toks: list[str], markers) -> int:
    """語形の並びから文体標識を数える。

    UniDic の短単位では ``である`` が ``で``＋``ある``，``ている`` が
    ``て``＋``いる`` に切れる。単独トークンとしてしか数えないと，
    口語標識がほとんど 0 になってしまう。そこで連続する数トークンの
    連結も照合する。1 箇所は最短一致で 1 回だけ数える。
    """
    mset = set(markers)
    longest = max(len(m) for m in markers)
    total = 0
    for i in range(len(toks)):
        acc = ''
        for j in range(i, min(len(toks), i + 4)):
            acc += toks[j]
            if len(acc) > longest:
                break
            if acc in mset:
                total += 1
                break
    return total


def measure(path: str, strict: bool = True) -> dict:
    """**分かち書き済み**テクストから実測値を取る。

    ここが要注意である。``tokens`` は ``str.split()`` で数えるので，
    空白で区切られていない日本語テクストを渡すと**段落数を語数として
    数えてしまう**。TTR は 1000 近くになり，文体標識はすべて 0 になり，
    それでも例外は出ない。実際 ``data/plain/full/`` を渡して
    『夜明け前』の語数が 1,957（正しくは約 20 万）になる事故が起きた。

    そこで既定で入力を検査する。1 トークンあたりの平均文字数が
    大きすぎるものは分かち書きされていないとみなして例外を投げる。
    渡すべきは ``data/tokens/tokens_surface/`` である。
    """
    s = open(path, encoding='utf-8').read()
    toks = s.split()
    n = max(1, len(toks))
    body = re.sub(r'\s', '', s)
    b = max(1, len(body))
    if strict and toks and (b / n) > 8:
        raise NotTokenised(
            f'分かち書きされていない（1トークン平均 {b / n:.1f} 字）: {path}\n'
            '         渡すべきは data/tokens/tokens_surface/ である。\n'
            '         data/plain/full/ を渡すと段落数を語数として数えてしまう。')
    c = Counter(toks)
    def g(xs):
        return count_markers(toks, xs)
    return {
        'tokens': len(toks),
        'types': len(c),
        'ttr_x1000': round(1000 * len(c) / n, 2),
        'kanji_ratio': round(len(re.findall(r'[一-鿿㐀-䶿]', body)) / b, 4),
        'katakana_ratio': round(len(re.findall(r'[゠-ヿ]', body)) / b, 4),
        'bungo_per10k': round(10000 * g(BUNGO) / n, 1),
        'kogo_per10k': round(10000 * g(KOGO) / n, 1),
        'p1_per10k': round(10000 * g(P1) / n, 1),
        'p3_per10k': round(10000 * g(P3) / n, 1),
        'bungo_ratio': bungo_ratio(round(10000 * g(BUNGO) / n, 1),
                                   round(10000 * g(KOGO) / n, 1)),
        'gaiji_lost': s.count('※'),
        'kana_odoriji': sum(s.count(x) for x in 'ゝゞヽヾ'),
        'kanji_odoriji': s.count('々'),
        'ditto_mark': s.count('〃'),
        'latin_chars': len(re.findall(r'[A-Za-zＡ-Ｚａ-ｚ]', s)),
        'arabic_digits': len(re.findall(r'[0-9０-９]', s)),
    }


def bungo_ratio(bungo: float, kogo: float) -> float:
    """文語標識が文語・口語標識全体に占める割合。0（純口語）〜1（純文語）。"""
    tot = bungo + kogo
    return round(bungo / tot, 3) if tot else 0.0


#: ``style_class`` の境界。**二つの軸で決める**（2026-09-27 改訂）。
#: 変えたら ``bungo_ratio`` 列と文語密度の分布とともに報告すること。
A_BUNGO_MIN = 80.0     # A の条件その1: 文語密度（／万語）
A_RATIO_MIN = 0.20     # A の条件その2: 比。**両方**満たすこと
C_RATIO_MAX = 0.10     # C の条件: 比がこれ以下


def style_class(bungo: float, kogo: float) -> str:
    """文語／過渡／口語の三値分類（助動詞標識による経験的分類）。

    **比ひとつでは切れない。二つの軸で決める。**

        A_文語体    文語密度 ≧ 80／万語 **かつ** 比 ≧ 0.20
        C_口語体    比 ≦ 0.10
        B_過渡的文体  残り

    なぜ二つ要るのか（2026-09-27 の改訂）
    -------------------------------------
    比だけで切っていたところ，**分母の小さい順に6点並べると6点とも
    「B_過渡的文体」**になっていた。しかもその文語密度は 13〜27／万語で，
    119点の中央値 26.5 を下回る。文語が濃いのではなく，口語標識が
    数えられていなかった。当時の ``KOGO`` には「だ」の終止形が無く，
    だ止め・体言止めの作品（林芙美子『浮雲』，長塚節『土』，岸田国士
    『紙風船』）が文語でも口語でもない扱いになって比が跳ねていた。

        林芙美子『晩菊』 文語 16.1  口語 197.3  比 0.075  C
        林芙美子『浮雲』 文語 17.8  口語  55.3  比 0.244  B   ← 分母だけが違う

    口語標識を足すと分母は安定した（開きが 20.1 倍 → 4.8 倍）。しかし
    今度は**雅俗折衷体**が沈んだ。地の文が文語・会話が口語の作品
    （樋口一葉『たけくらべ』『にごりえ』，幸田露伴『五重塔』，尾崎紅葉
    『金色夜叉』）では，足した口語標識が会話の側にあるので比だけが
    下がり，文語密度は下がらない。『たけくらべ』は文語密度 97.7——
    B 群（13〜31）の3倍以上——でありながら比は 0.455 である。

    **重なっている相手が軸ごとに違う**のだから，両方を課せば切れる。
    実測では A 16点／B 14点／C 89点に落ち着き，口語体と判定される 75点は
    改訂前と完全に一致した（C の 89点は，改訂前の 75点＋B から落ちた 14点）。

    閾値の根拠
    ----------
    80  … 樋口一葉『にごりえ』の 84.8 を A に残す。B 群の上位
          （徳冨蘆花『不如帰』174.5，三宅花圃『藪の鶯』128.4）も入るが，
          いずれも地の文が文語体で，A が妥当である
    0.20 … 夢野久作『押絵の奇蹟』(1929) を A から外す。書簡体なので
          文語密度は 93.2 あるが比は 0.168 で，他の A（最低 0.369）から
          離れている。**昭和4年の作品を文語体とは呼ばない**
    0.10 … これを 0.07 にすると太宰治『人間失格』や小林多喜二『蟹工船』が
          「過渡的文体」に上がる。口語体でないとは言えない。0.10 なら
          三遊亭円朝の速記口演体も島崎藤村『破戒』も C に来て，話し言葉・
          言文一致という実態に合う

    経験的な分類であって定義ではない。``scripts/check_style_class.py`` で
    閾値を変えたときの影響を先に見られる。
    """
    r = bungo_ratio(bungo, kogo)
    if bungo >= A_BUNGO_MIN and r >= A_RATIO_MIN:
        return "A_文語体"
    if r <= C_RATIO_MAX:
        return "C_口語体"
    return "B_過渡的文体"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', required=True, help='v1 の分かち書きテクストのあるディレクトリ')
    ap.add_argument('--out', required=True, help='出力ディレクトリ')
    args = ap.parse_args()

    rows = []
    for i, r in enumerate(RECORDS, 1):
        (stem, author, title, pid, wid, ndc, yf, yt, kana, ysrc,
         gmain, gsub, form, audience, register, narration, serial, comp, note) = r
        path = os.path.join(args.corpus, stem + '.txt')
        m = measure(path) if os.path.exists(path) else {}
        reading, sex, birth, death = AUTHOR_INFO[author]
        card = f"https://www.aozora.gr.jp/cards/{pid}/card{wid.split('-')[0]}.html"
        row = {
            'id': f"JLIT{i:03d}",
            'file_v1': stem + '.txt',
            'author_ja': author,
            'author_reading': reading,
            'author_sex': sex,
            'author_birth': birth,
            'author_death': death,
            'title_aozora': title,
            'aozora_person_id': pid,
            'aozora_work_id': wid,
            'aozora_card_url': card,
            'year_first': yf,
            'year_first_end': yt,
            'year_source': ysrc,
            'period': period_of(yf),
            'ndc': ndc,
            'ndc_label': NDC_LABEL[ndc],
            'genre_main': gmain,
            'genre_sub': gsub,
            'form': form,
            'audience': audience,
            'register_level': register,
            'narration': narration,
            'first_medium': serial,
            'kana_orthography': kana,
            'completeness': comp,
            'note': note,
        }
        row.update(m)
        if m:
            row['style_class'] = style_class(m['bungo_per10k'], m['kogo_per10k'])
        rows.append(row)

    os.makedirs(args.out, exist_ok=True)
    keys = list(rows[0].keys())
    csv_path = os.path.join(args.out, 'corpus_metadata_v2.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path}  ({len(rows)} rows, {len(keys)} columns)")
    return 0


if __name__ == '__main__':
    sys.exit(main())

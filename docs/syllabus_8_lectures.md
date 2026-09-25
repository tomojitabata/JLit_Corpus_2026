# 講義計画（全8ステップ） — 近代日本文学コーパスの構築と計量分析

**担当**: 田畑智司
**実施環境**: DH Lab 共用 iMac（macOS 27）／各自の Windows 11 も可
**1コマ**: 90分（講義 30分＋実習 50分＋講評 10分）を想定
**コマ数**: 8 Step ／ **実質 12 コマ程度**を見込む（下記）
**評価**: 各ステップの課題（60%）＋最終リポート（40%）

**用語集**: `docs/glossary.md`。Step ごとにキーワード・解説・確認問題を置いた。
各ノートブックの冒頭からも参照している。**予習ではキーワードだけを見て自分で
説明を試み，復習では確認問題を解く**——という使い方を初回に説明すること。

---

## なぜ「回」ではなく「Step」か

8つの Step で構成するが，これは8コマという意味ではない。環境構築・
スクリプトの不具合・底本の取り直しで必ず遅れが出るので，**実質 12 コマ
程度**を見込んでいる。とくに Step 1（環境構築）と Step 2（取得と変換）は
受講生の機材差で大きくばらつく。

Step は**カレンダー上の回ではなく到達点**である。前の Step の成果物
（XML，正規化テクスト，トークン列）ができていなければ次へは進めない。
逆に，できていれば何コマかかっても構わない。各 Step の末尾に
「このステップの到達点（次へ進む条件）」を置いたのはそのためで，
そこに書かれたことができているかどうかが，先へ進んでよいかの判断基準になる。

この設計は受講生に最初に説明すること。「第N回」と呼ぶと，
日付で進むもの・遅れたら置いていかれるものと受け取られる。
Step と呼べば，**到達度で進むもの**だと伝わる。

### コマ配分の目安

| Step | 想定コマ数 | ばらつきの要因 |
|---|---|---|
| Step 1 | 1–2 | 環境構築。Windows 勢で UniDic / MALLET が詰まりやすい |
| Step 2 | 2–3 | 青空文庫からの取得（1秒/ファイルの待ち），マニフェストの修正 |
| Step 3 | 1–2 | 形態素解析は時間がかかるが手順は安定 |
| Step 4 | 1 | |
| Step 5 | 1–2 | word2vec の学習待ち。安定性検査で10回学習させる（時間が無ければ5回） |
| Step 6 | 2 | スライス設計の試行錯誤 |
| Step 7 | 1 | |
| Step 8 | 2 | トピック数の sweep と最終リポートの相談 |
| **計** | **11–15** | |

### 受講環境の三系統

| | 環境 | 特徴 | 教員側の手当て |
|---|---|---|---|
| A | DH Lab 共用 iMac | **ホームはマシンごとに別々**（XCreds。各マシンのホームは保持されるが共有されない）。admin 権限は使わせない | 初回にマシンを固定して割り当て，`my_work/` のバックアップ（受講生自身の GitHub）を Step 1 で作らせる |
| B | 持ち込みの MacBook | マシンが1台なので環境構築は一度でよい。Intel 機も混じる | A と同じスクリプトに `--personal` を付けるだけ |
| C | 自分の Windows 11 | 文字コードとパスで詰まる | `PYTHONUTF8=1`，MALLET を `C:\JLit\` に |

A の制約が最も重い。**受講生が「前回の作業が消えた」と言ってきたら，
まずマシンを確認する。** たいていは別のマシンにログインしただけで，作業は
前回のマシンのホームに残っている。 `00_env_check.py` が毎回マシン名を表示するので，
リポートに控えさせること。

**作業の終わりは必ずログアウトさせる。** ログイン画面に切り替えるだけ（ファスト
ユーザースイッチ）だと，その人の Jupyter・KWIC のサーバ・計算中の処理が裏で動き続け，
次にそのマシンを使う人の処理が遅くなる。KWIC は前の人のサーバが残っていても別のポートを
自動で使い，`00_bootstrap_mac.sh` は前の人の取得が裏で続いていれば止まって待たせる
ようにしてあるが，計算資源の食い合いはスクリプトでは防げない。DH ラボの管理者に，
ファストユーザースイッチの無効化か，一定時間操作が無いときの自動ログアウトを
依頼しておくとよい。

重い共有物（JDK・MALLET・UniDic 約 1.7 GB・取得済み XHTML）は
`/Users/Shared/jlit` に置く。macOS ではここが admin 権限なしに全ユーザーから
読み書きでき，**同じマシンなら次の受講生が取り直さずに済む**（`00_bootstrap_mac.sh` は
`jlit` と `aozora-cache` を `/Users/Shared` と同じパーミッション 1777（誰でも書けるが他人のファイルは
消せない）で作る。辞書・JDK・MALLET は読み取り専用で置く）。
青空文庫への負荷も減る（1 秒/件の取得を人数分繰り返さない）。

Java の導入に admin 権限が要らないよう，Temurin の tar.gz を展開する方式に
してある。それでも Java が入らない環境が出たら，`tomotopy` で Step 8 を
代替させ，リポートに明記させること。

---

## 全体設計の考え方

8 Step を **「コーパスを作る4 Step」＋「コーパスで測る4 Step」** に分ける。

```
Step 1  診断     ── このコーパスで何が言えて，何が言えないか
Step 2  再構築1  ── 書誌の典拠と XML マークアップ
Step 3  再構築2  ── 正規化・形態素解析・検証          ← ここで FATAL 0 にする
Step 4  記述統計 ── 頻度で何が見えるかを確定させる
─────────────────────────────────────────
Step 5  word2vec の原理
Step 6  通時 word2vec（意味変化）
Step 7  doc2vec（作品表現・交絡の統制）
Step 8  MALLET（主題の多様化）＋総合
```

前半 4 Step を飛ばして Step 5 から始めることは**しない**。
外字が592箇所欠け，重複が1件あり，奥付が本文に混入したコーパスで
word embedding を学習しても，測っているのは前処理の副作用である。
「作る」工程そのものが，この授業の中身の半分を占める。

各ステップの実習内容は `notebooks/0X_*.ipynb` に実行可能な形で入っている。
受講生は配布版を直接開かず，`my_work/notebooks/` のコピーで実行する。
教材を直して push したら，受講生には授業のはじめに `bash scripts/update.sh` を実行させる
（手元の変更を退避してから更新するので，`git pull` の衝突で止まらない）。

### 置き場の分担（受講生はマスターに一切書き込まない）

| 置き場 | 役割 | 教員側の準備 |
|---|---|---|
| コースのリポジトリ（GitHub `tomojitabata/JLit_Corpus_2026`） | 教材の配布。受講生は clone と `update.sh` だけ | main ブランチを保護する（Settings → Branches）。受講生の clone では `update.sh`／`setup_my_work.sh` が push 先を `DISABLED` にする |
| 受講生の GitHub（private の `jlit-work`） | `my_work/`（実行したノートブック・図・表・原稿）のバックアップ。マシンをまたぐ持ち運び | Step 1 で `setup_my_work.sh` まで済ませる（手順書 §5.1–5.2）。トークンの作成で詰まりやすいので時間を取る |
| Zulip `dh-uosaka.zulipchat.com` の非公開チャネル **2026年度テクスト分析論B** | 課題（トピック「Step 1」…「Step 8」）・最終リポート（トピック「最終リポート」）・連絡 | 初回の講義で受講生を招待する。新しく加わった人も過去のメッセージを読める設定にする。自分のメッセージの削除は管理者のみ・編集は短時間に限る（提出記録を残すため） |

提出の書き方は手順書 §5.3。各 Step の課題はテンプレート `my_work/results/StepN_report.md`
（設問・図表・再現のための情報）に**全体で600–1000字程度**で書き，その中身を
Zulip のトピック「Step N」に貼って図（SVG）・表を同じメッセージに添付する（1人1通）。
最終リポートはテンプレート `final_report.md` の構成で**全体で4000–6000字程度**，PDF で提出し，
要旨と PDF を1通で投稿する。再提出は元を直さず同じトピックに新しく投稿する。

テンプレートは `scripts/make_notebooks.py` が各 Step の「このステップの課題」の設問から
`templates/` に生成し，`copy_notebooks.py` が受講生の `my_work/results/` に配る。
**設問を直したら make_notebooks.py を実行し直す**（ノートブックとテンプレートが一緒に更新される）。
**チャネル名は年度ごとに変わる**ので，`scripts/make_notebooks.py` 冒頭の
`ZULIP_CHANNEL` と手順書 §5 を書き換えてからノートブックを生成し直すこと。
本ドキュメントは**教員用の進行メモ**である。

---

## Step 1 コーパスとは何か — 設計・代表性・v1 の診断

`notebooks/01_corpus_design.ipynb`

### 講義（30分）
- コーパス＝母集団からの標本。代表性（Biber 1993）
- 青空文庫という標本枠の性質：著作権が切れたもの，誰かが入力したもの
- 本コーパス v1（64点・605万語）の来歴

### 実習（50分）
1. **環境構築**（`docs/00_setup_students.md` に従う。最優先）
   - `python scripts/00_env_check.py` で `ALL OK` を目指す
   - **辞書は `unidic-novel-v202512`（本番）。** 共用 iMac では，各マシンで最初に
     セットアップした人が国立国語研究所から1回だけ取得し（zip 約 1.7 GB），
     そのマシンの `/Users/Shared/jlit` に置く。同じマシンの2人目以降は
     `00_bootstrap_mac.sh` が辞書の有無を確かめて `[have]` で飛ばす
     （マシンどうしでは共有されないので，マシンごとに1回ずつ取得が起きる）
   - `[ WARN] … — **本番ではない**` が出たら，そのまま進ませない
2. `metadata/corpus_metadata_v2.csv` を読み，偏りを数える
3. 文語⇄口語の散布図を描く
4. 統計量が近すぎるファイルを探し，**重複を自力で発見する**

### このステップの勘所
受講生に「答え」を先に言わない。統計量の近いペアを探させ，
自分で `乱歩_灰色の巨人.txt` ＝ 『魔法博士』を発見させる。
**発見の体験がこの授業の姿勢を決める。**

### よくある躓き
| 症状 | 対処 |
|---|---|
| `fugashi` が import できない | 仮想環境が有効でない。`source ~/Documents/dh_project/.venv/bin/activate` |
| `mecabrc` がない | `touch /opt/homebrew/etc/mecabrc` |
| 図が □ | `docs/00_setup_students.md` §1.7 |

### 課題
テンプレート `Step1_report.md` に書き（全体で600–1000字程度），Zulip **2026年度テクスト分析論B ＞ Step 1** に図1枚（**SVG**）とともに1通で投稿。
最も深刻な偏り／それが不可能にする問い／増補候補3点の選定理由。

---

## Step 2 コーパスの再構築 — 書誌の典拠と XML マークアップ

`notebooks/02_rebuild_xml.ipynb`

### 講義（30分）
- 「削る」のではなく「分離する」。TEI の思想
- 青空文庫の2つの配布形式（.txt vs XHTML）
- 外字と JIS X 0213。面区点 → Unicode の機械的変換
- v1 の `year` 誤りは索引 CSV を使っていれば起きなかった

### 実習（50分）
1. `02_fetch_aozora.py index` で書誌索引を取得（約20MB）
2. 索引で『潜航艇「鷹の城」』を引き，v1 の 1977 が底本刊年だったことを確かめる
3. `resolve` でマニフェストの解決状況を見る。**未解決行を直すのが実習の核**
4. `works` で XHTML を取得（**各自，自分のマシンで**。1秒/件の間隔で現行64点なら約1分，
   全件で約2分。取得した XHTML はそのマシンの共有キャッシュにも置かれ，同じマシンでの
   2回目以降は青空文庫に取りに行かない）
5. `03_aozora2xml.py` で XML 化し，外字復元数を確認
6. `<said>` から会話文比率を計算

### このステップの勘所
- 取得は帯域と礼儀の問題。`sleep 1.0` を下げさせない
- 未解決行の原因（表記ゆれ・副題・分冊・新字旧字）を分類させる。
  **これがコーパス構築の実務そのもの**
- 面区点の復元をライブで見せる。`euc_jis_2004` コーデックの妙

### 課題
未解決行3つの解決／`※` だった箇所3つの復元と語義／会話文比率のジャンル比較図。

---

## Step 3 再構築からKWICへ — 踊り字の正規化・UniDic 解析・データセット再構築・KWICコンコーダンサ実装

`notebooks/03_normalise_tokenise.ipynb`

### 講義（30分）
- 踊り字の偏在は文体差ではなく底本の正書法差
  （『破戒』ゝ218 vs 藤村の他4作 0）
- `々` を展開してはいけない理由（UniDic に語彙素として登録済み）
- UniDic の語形：surface / orthBase / **lemma** / lForm
  - `lemma` は異表記を統合するが，**固有名詞は片仮名になる**
- **辞書の選定は研究設計の一部**。「解析器 MeCab ＋ 辞書 UniDic」の
  どちらを替えたのかを言い分ける。本コーパスは 4 辞書を 111 点で比べて
  `unidic-novel` に決めた（`docs/dictionary_comparison.md` §10）
- 未知語率＝前処理の品質指標。**ただし辞書を替えると意味が変わる**（§11）

### 実習（50分）
1. 踊り字展開の規則をテストで確かめる
2. `04_normalise.py` で3ストリーム（full / narration / speech）を生成
3. `lemma-policy` を4通り変えて異なり語数を比較
4. `05_tokenise_unidic.py` で解析。未知語率と未知語リストを読む。
   **出力の1行目に書かれた辞書名を確かめる**
5. `00_extend_metadata.py` で v3 を作り，**Step 1 の2枚の図（時代の構成・
   文語 ⇄ 口語）を v1 と v3 で並べて作り直す**。どの空白が埋まり，何がまだ
   偏っているかを確かめる（物差しをそろえるため，両方とも v3 の実測値で描く）
6. `06_build_datasets.py` でチャンク分割（2,000語・上限40）
7. **`99_validate.py` で FATAL 0 を達成する**

### このステップの勘所
- 未知語の上位を分類させる：人名／踊り字残り／外字／文語活用／外来語
- 文語活用の未知語は**辞書の被覆の問題**であり前処理の失敗ではない。
  本コーパスでは `unidic-novel` に替えた時点でほぼ消えた
- **残った未知語がカタカナ外来語ばかりになると，未知語率は
  「正書法の乱れ」ではなく「外来語の密度」を測る指標に変わる。**
  指標の意味が動くことを体験させる（`14_unknown_profile.py`）
- チャンク長を変えて分布を見せる。16.5倍の長さ差を実感させる

### 課題
未知語上位3ファイルの原因特定／`lemma-policy` の比較／
チャンク長3条件の比較／FATAL 0 のログ。
発展：`12_dict_compare.py` を 2 辞書で実行し，境界の食い違いを 3 例読む。

---

## Step 4 記述統計と文体計量 — MFW・Delta・PCA・特徴語

`notebooks/04_descriptive_stylometry.ipynb`

### 講義（30分）
- 文体計量の逆説：作者を最もよく識別するのは**内容と無関係な機能語**
- TTR の罠。標本サイズ依存（『夜明け前』TTR×1000 = 45.2 は語彙が貧しいのではない）
- Burrows's Delta：z 標準化＋絶対差の平均。単純さが強み
- 対数尤度比 G² と効果量。**G² は標本サイズに比例する**
- **G² は「どこで多いか」を見ない**。頻度が同じでも
  「8作品に20回ずつ」と「1作品に160回」は G² が一致する。
  bursty か evenly-distributed か → **散らばり（ディスパーション。df・DP）と culling**
- 研究史：Burrows の MFW（1987）と Delta（2002），Hoover の culling（2004），
  Burrows の Zeta・Iota（2007。中頻度・低頻度の語），Random Forests による
  Dickens の特徴語の抽出（Tabata 2012, 2015），representativeness と
  distinctiveness（Klaussner ほか 2015），stylo（2016）。
  Zeta は語が出るか出ないかだけを数えるので，bursty さに引きずられない

### 実習（50分）
1. `07_descriptive_stats.py` を実行
2. TTR / Guiraud R / Yule K を語数に対してプロットし，頑健な指標を選ぶ
3. Delta を手で実装し，最近傍一致率（作家 / 時代 / ジャンル）を比較
4. PCA を4通りの色分けで描く
5. 時代別特徴語を出し，**固有名詞を除くとどう変わるか**を確かめる
6. **culling at 10% の有無で特徴語リストを並べ**，重なり@15・入替・
   残存率を数える。除外された語の `top_work` を見て，
   **どの作品が「時代の特徴」を作っていたか**を名指しする

### このステップの勘所
最近傍一致率で **作家 > 時代** が出るはず。ここで
「通時的主張には統制が要る」という Step 6・7 への伏線を張る。

culling は**測る前に閾値を宣言する**練習でもある（Step 3 の辞書の選定と
同じ作法）。複数の閾値を試すのは感度分析であって，結果を見てから
規則を選ぶことではない。

### 課題
語彙多様性指標の選択と根拠／最近傍一致率の報告／
固有名詞除外前後の特徴語比較／MFW 語数を変えた PCA／
**culling の有無の比較表と，どちらを本分析に採るかの正当化**。

---

## Step 5 word2vec の原理 — 分布仮説から word embedding へ

`notebooks/05_word2vec_basics.ipynb`

### 講義（30分）
- Firth の分布仮説
- 共起行列 → PPMI → SVD と word2vec の関係（Levy & Goldberg 2014）
- CBOW vs skip-gram。**小規模コーパスでは skip-gram**
- window の大小：小＝統語的類似／大＝主題的類似

### 実習（50分）
1. 共起行列を**手で作り**，PPMI + SVD で近傍語を出す
2. gensim の word2vec を学習し，同じ語の近傍と比べる
3. `window` を 2 / 5 / 15 で比較
4. **安定性検査**：乱数シードを10通り変え（時間が無ければ5通り），近傍の Jaccard 一致率を測る

### このステップの勘所
Antoniak & Mimno (2018) の議論を必ず扱う。
**Jaccard < 0.3 の語について「意味が変化した」と論じてはいけない。**
頻度と安定性の散布図を見せると効果的。

### 課題
共起行列版と word2vec の比較／ハイパーパラミター3条件の近傍表／
自分の関心語10語の安定性検査／**次の Step で追う5語の選定と仮説**。

---

## Step 6 通時的 word2vec — 時代スライスと Procrustes アラインメント

`notebooks/06_diachronic_word2vec.ipynb`

### 講義（30分）
- word2vec の目的関数は**回転不変**。だから空間を直接比べられない
- 直交 Procrustes：$R = UV^\top$（$U\Sigma V^\top = \mathrm{SVD}(B^\top A)$）
- スライス設計は結果を決める。本コーパスでは **4スライスが上限**
  （明治期3区分を合わせても96万語）
- Dubossarsky et al. (2017)：**シャッフルした偽の時系列でも
  「意味変化の法則」は再現される**

### 実習（50分）
1. 乱数行列で「回転してもコサインは不変」を確かめる
2. 4スライス／2スライスを設計
3. `08_word2vec_diachronic.py` を `--balance` つきで実行
4. **対照実験**：時代ラベルをシャッフルして同じ手順を実行し，分布を重ねる
5. 近傍語の変遷を読む
6. **KWIC で原文に戻る**

### このステップの勘所
対照実験がこの授業で最も重要な手続き。
本物と偽物の分布が重なったら，その主張は成立しない。
数値で終わらせず，必ず KWIC で用例を読ませる。

### 課題
4スライスと2スライスの比較／**対照条件の図は必須**／
自分の5語の drift・近傍・KWIC 三点セット／
「意味変化した」と言える条件を3つ。

---

## Step 7 doc2vec — 作品の表現と交絡の分離

`notebooks/07_doc2vec.ipynb`

### 講義（30分）
- Paragraph Vector（Le & Mikolov 2014）。PV-DM と PV-DBOW
- **作品を1文書にしない理由**：長篇ほど学習量が多く，質が長さと相関する
- 作家効果を除く3つの方法：中心化／層化抽出／混合効果モデル
- **GroupKFold**：同じ作家が訓練とテストに分かれると，時代ではなく作家を当てる

### 実習（50分）
1. `09_doc2vec.py` を実行し，カテゴリー効果を見る
2. 作家ごとに中心化し，`period` の効果が残るか確かめる
3. 通常の交差検証と作家グループ交差検証の精度を比べる
4. 作品空間を2通りの色分けで描く

### このステップの勘所
通常 CV とグループ CV の差が大きいことを実演で示す。
「精度90%」が実は作家当てだった，という体験が効く。

### 課題
`dm=0/1` の比較／中心化前後の効果比較／
**2つの交差検証の差とその意味**／Delta 空間との近傍比較。

---

## Step 8 トピックモデル（MALLET） — 主題の多様化と総合

`notebooks/08_topic_modelling.ipynb`

### 講義（30分）
- LDA の生成過程。$\theta_d \sim \mathrm{Dir}(\alpha)$, $\phi_k \sim \mathrm{Dir}(\beta)$
- `--optimize-interval` は必ず有効に（トピックの大小を自動調整）
- 診断指標：coherence（Mimno et al. 2011）と exclusivity のトレードオフ
- **トピック分布のエントロピー**＝主題の多様さ。本研究の中心的な問いに直結

### 実習（50分）
1. ストップリストを読む。**研究の問いによって変える**（語り分析では代名詞を残す）
2. トピック数スイープ（時間があれば。なければ既定 K=50）
3. `10_mallet.py all` を実行
4. トピックの**代表チャンクを実際に読む**。上位語だけで名づけない
5. 時代別エントロピーを出す
6. **児童書7点を除いても傾向が残るか**を確かめる
7. 3空間（Delta / doc2vec / LDA）の距離順位の Spearman 相関

### このステップの勘所
- 「主題が多様化した」という結論が，実は読者層構成の変化
  （昭和期の児童書7点）を測っていないかを必ず検証させる
- 3つの方法が一致する所見は強い。一致しない所見は考察の材料

### 最終課題（全体で4000–6000字程度。テンプレート `final_report.md` の構成で書き，PDF で提出）
問いの設定（**答えられない問いも明示**）／データと前処理の決定と理由／
方法（ハイパーパラミターと乱数シードを明記）／結果（**対照条件・交差検証・安定性検査を必ず含む**）／
考察（作家効果と時代効果の切り分け・偏りの排除・増補すべきテクスト）／
再現のための情報（教材の版・辞書・乱数シード・変えた設定。`config/pipeline.yaml` を添付）。

---

## 教材と準備物

| 回 | 事前準備 | 所要 |
|---|---|---|
| 1 | 共用 iMac に Python 3.12・Homebrew。UniDic は各マシンで最初の1人が取得（約 1.7 GB。同じマシンの2人目以降は不要） | 60分 |
| 2 | 回線（XHTML 約104件・数十MB）。各自が自分のマシンで取得（1秒/件）。同じマシンの2回目以降は共有キャッシュから | 10分 |
| 3 | UniDic 完全版が全員に入っていること | — |
| 4 | — | — |
| 5 | gensim。NumPy 2 系との組合せに注意 | — |
| 6 | Step 3 のチャンクが揃っていること | — |
| 7 | scikit-learn | — |
| 8 | **MALLET と Java**。ヒープを 4g に上げておく | 20分 |

## 参考文献（授業全体）

- Aitchison, J. (1986) *The Statistical Analysis of Compositional Data*. Chapman & Hall.
- Antoniak, M. & Mimno, D. (2018) Evaluating the stability of embedding-based word similarities. *TACL* 6.
- Biber, D. (1993) Representativeness in corpus design. *Literary and Linguistic Computing* 8(4).
- Blei, D., Ng, A. & Jordan, M. (2003) Latent Dirichlet Allocation. *JMLR* 3.
- Blondel, V. D., Guillaume, J.-L., Lambiotte, R. & Lefebvre, E. (2008) Fast unfolding of communities in large networks. *Journal of Statistical Mechanics: Theory and Experiment* 2008(10): P10008.
- Burrows, J. (1987) *Computation into Criticism: A Study of Jane Austen's Novels and an Experiment in Method*. Clarendon Press.
- Burrows, J. (2002) 'Delta': a measure of stylistic difference. *LLC* 17(3).
- Burrows, J. (2007) All the way through: testing for authorship in different frequency strata. *Literary and Linguistic Computing* 22(1): 27–47.
- Craig, H. & Kinney, A. F., eds. (2009) *Shakespeare, Computers, and the Mystery of Authorship*. Cambridge University Press.
- Dubossarsky, H. et al. (2017) Outta control: laws of semantic change and inherent biases in word representation models. *EMNLP*.
- Eder, M., Rybicki, J. & Kestemont, M. (2016) Stylometry with R: a package for computational text analysis. *The R Journal* 8(1): 107–121.
- Evert, S. (2008) Corpora and collocations. In A. Lüdeling & M. Kytö (eds), *Corpus Linguistics: An International Handbook*, Vol. 2, 1212–1248. Mouton de Gruyter.
- Gries, S. Th. (2013) 50-something years of work on collocations: what is or should be next …. *International Journal of Corpus Linguistics* 18(1): 137–165.
- Hamilton, W., Leskovec, J. & Jurafsky, D. (2016) Diachronic word embeddings reveal statistical laws of semantic change. *ACL*.
- Hoover, D. L. (2004a) Testing Burrows's Delta. *Literary and Linguistic Computing* 19(4): 453–475.
- Hoover, D. L. (2004b) Delta prime? *Literary and Linguistic Computing* 19(4): 477–495.
- 金明哲 (2021)『テキストアナリティクス』共立出版
- Klaussner, C., Nerbonne, J. & Çöltekin, Ç. (2015) Finding characteristic features in stylometric analysis. *Digital Scholarship in the Humanities* 30(Supplement 1): 114–129.
- Lau, J. H., Grieser, K., Newman, D. & Baldwin, T. (2011) Automatic labelling of topic models. *Proceedings of the 49th Annual Meeting of the Association for Computational Linguistics*: 1536–1545.
- Le, Q. & Mikolov, T. (2014) Distributed representations of sentences and documents. *ICML*.
- Levy, O. & Goldberg, Y. (2014) Neural word embedding as implicit matrix factorization. *NIPS*.
- Lin, J. (1991) Divergence measures based on the Shannon entropy. *IEEE Transactions on Information Theory* 37(1): 145–151.
- 前川喜久雄 編 (2013)『コーパス入門』（講座日本語コーパス1）朝倉書店
- Prokić, J., Çöltekin, Ç. & Nerbonne, J. (2012) Detecting shibboleths. *Proceedings of the EACL 2012 Joint Workshop of LINGVIS & UNCLH*.
- Rychlý, P. (2008) A lexicographer-friendly association score. *Proceedings of Recent Advances in Slavonic Natural Language Processing (RASLAN 2008)*: 6–9.
- Schöch, C., Schlör, D., Zehe, A., Gebhard, H., Becker, M. & Hotho, A. (2018) Burrows' Zeta: exploring and evaluating variants and parameters. *DH2018 Book of Abstracts*.
- Sievert, C. & Shirley, K. (2014) LDAvis: a method for visualizing and interpreting topics. *Proceedings of the Workshop on Interactive Language Learning, Visualization, and Interfaces*: 63–70.
- Smith, P. W. H. & Aldridge, W. (2011) Improving authorship attribution: optimizing Burrows' Delta method. *Journal of Quantitative Linguistics* 18(1): 63–88.
- Tabata, T. (2012) Approaching Dickens' style through Random Forests. *Digital Humanities 2012: Conference Abstracts*, University of Hamburg.
- Tabata, T. (2015) Stylometry of Dickens's language: an experiment with random forests. In P. L. Arthur & K. Bode (eds), *Advancing Digital Humanities: Research, Methods, Theories*. Palgrave Macmillan.
- Tabata, T. (2026) Using word embeddings as a semantic approach to key word analysis. *PALA 2026: The Philosophy of Stylistics*, Uppsala, 19–22 August 2026. 資料: <https://tinyurl.com/tabata-pala2026>
- Underwood, T. (2019) *Distant Horizons: Digital Evidence and Literary Change*. University of Chicago Press.

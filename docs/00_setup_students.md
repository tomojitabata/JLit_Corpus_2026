# 受講生向け 環境構築手順（Step 1 で実施）

この授業では3つの環境を想定している。**自分がどれかを最初に決めること。**

| | 環境 | 節 | 所要 |
|---|---|---|---|
| **A** | DH Lab 共用 iMac（macOS 27, Apple Silicon, 16 GB） | §2 | 初回 30 分 / 2回目以降 5 分 |
| **B** | 自分の MacBook（持ち込み） | §3 | 20 分（初回のみ） |
| **C** | 自分の Windows 11 PC | §4 | 40 分（初回のみ） |

A と B は**同じスクリプト1本**で済む。違いは「重い共有物をどこに置くか」だけである。

---

## 1. 最初に知っておくべきこと

### 1.0 コマンドは1行ずつ貼ること — zsh では `#` がコメントにならない

macOS の既定のシェルは **zsh** で，対話的に使っているときは
`interactive_comments` が**切れている**。つまり

```
stat -f%z ~/Downloads/foo.zip  # 手元の大きさ
```

のように行の後ろに `#` で注釈を書くと，**`#` も「手元の大きさ」も
引数として渡される。** 出るのは

```
stat: #: stat: No such file or directory
stat: 手元の大きさ: stat: No such file or directory
```

という，本題と無関係なエラーである。**コマンド自体は間違っていない。**

本手順書のコマンド例は注釈を**行の先頭**に置いてあるので，
ブロックごと貼っても通る。ただし複数行を一度に貼ると，どの行で失敗したのか
分からなくなるので，**うまくいかないときは1行ずつ貼る**こと。

（注釈を後ろに書けるようにしたい場合は `setopt interactive_comments` を
実行する。§1.5 の授業用 `.zshrc` を入れれば，最初からそうなっている。
スクリプトファイルの中では最初からコメントとして扱われるので，
この問題は対話的に使うときだけ起きる。）

**もう一つ。行の継続は `\` 1文字である。** `\\` と2つ重なっていると，
zsh はそれを「バックスラッシュという文字」1個と解釈し，**行はそこで
終わる**。次の行は独立したコマンドとして実行されるので

```
zsh: command not found: --plain
zsh: command not found: --dict
```

のように，オプション名がコマンドとして扱われる。これもコマンド自体は
正しい。長いコマンドを貼ってこうなったら，**末尾を見て `\` が1つか
確かめる**こと。

長いコマンドは，貼らずに用意したスクリプトを走らせるのが安全である
（例: `bash scripts/run_dict_compare.sh`）。

### 1.1 共用 iMac はホームがマシンごとに別々

DH Lab の iMac は **XCreds** で認証する。どのマシンにもログインでき，
ログインしたマシンに**自分のホームディレクトリが作られ，そのまま保持される**
（ゲストアカウントではないので，ログアウトしても消えない）。
ただし，**ホームはマシンごとに別々で，マシンどうしで共有・同期されない。**

> 火曜日に「2021-03」番ラベルのマシンで作った仮想環境と成果物は，木曜日に
> 同じ 2021-03 番にログインすればそのまま残っている。しかし 2021-05 番に
> ログインすると，2021-05 番には 2021-05 番のホームが新しく作られるので，
> そこには**無い**（2021-03 番に残っている）。

これは故障でも設定ミスでもなく，そういう仕組みである。したがって：

- **どのマシンを使ったかを控えておく。** 本体に貼ってあるラベル番号（例 2021-03）を
  メモし，`00_env_check.py` が毎回表示するマシン名もレポートに書く。
  できるだけ同じマシンを使うと，環境構築をやり直さずに済む
- **自分の作業は自分の GitHub にバックアップする。** 別のマシンで続きをするには，
  `my_work/` を push しておき，移った先で pull するのがいちばん確実である（§5）
- 初めてのマシンに移ったら §2 のスクリプトをもう一度実行する（そのマシンで誰かが
  既に済ませていれば，辞書などは共有されているので 5 分ほどで済む）
- **作業の終わりは必ずログアウトする**（アップルメニュー → ログアウト）。
  ログイン画面に切り替えるだけ（ファストユーザスイッチ）では，自分の Jupyter・
  KWIC の画面・計算中の処理（word2vec や MALLET）が裏で動き続け，次にそのマシンを
  使う人の処理が遅くなる

### 1.2 admin 権限は使わない

共用 iMac の admin 権限は TA が持っているが，**マシン本体への変更は最小限に
とどめる**方針である。本手順は `sudo` を一度も使わない。Homebrew も使わない
（Homebrew の導入自体に admin が要るため）。

- Python は **uv** がユーザ領域に入れる
- Java は Temurin の tar.gz を展開するだけ（インストーラを走らせない）
- MALLET も tar.gz を展開するだけ

自分の Mac なら Homebrew を使っても構わないが，**授業では同じ手順に揃える**。
共用機で詰まったときに，教員も他の受講生も同じ画面を見られるほうが早い。

### 1.3 二層の置き場

```
/Users/Shared/jlit/        ← 共用 iMac：このマシンの全員で共有する重い物
   jdk/                       Java（約 300 MB）
   mallet/                    MALLET（約 15 MB）
   unidic-novel-v202512/      **本番の辞書**（近現代口語小説UniDic・zip で約 1.7 GB）
   unidic-kindai-bungo-v202512/  検算用（近代文語UniDic。--with-bungo で入る）
   aozora-cache/              取得済みの青空文庫 XHTML（約 45 MB）
   env.sh                     環境変数をまとめたファイル

~/.jlit/                   ← 自分の Mac：同じ中身をホームに置く

~/Documents/dh_project/    ← 各自の作業フォルダ（uv のプロジェクト）
   pyproject.toml             uv add の行き先をここに固定する（スクリプトが作る）
   .venv/                     各自の仮想環境（数百 MB。作り直せる）
   JLit_Corpus_2026/          GitHub から clone したリポジトリ
```

**仮想環境はリポジトリの中ではなく，その親の `dh_project` に置く。**
理由は二つある。

- リポジトリで `uv add <パッケージ>` を実行すると，uv は親へ遡って
  `dh_project/pyproject.toml` を見つけ，**Jupyter のカーネルと同じ `.venv`** に入れる。
  リポジトリの中に `.venv` を置く旧い方式では，uv が別の場所に入れてしまい，
  「入れたのに import できない」が起きていた（§7）
- リポジトリを消して clone し直しても，仮想環境はそのまま残る

`.venv` と `pyproject.toml` は `00_bootstrap_mac.sh` が作る。
**`dh_project` の外に clone するとスクリプトは止まる**（置き直す手順を表示する）。

macOS の `/Users/Shared` は **admin 権限なしに全ユーザが読み書きできる**。
ここに置いておけば，同じマシンなら次の人が約 1.7 GB の辞書を取り直さずに済む。
青空文庫のサーバにも余計な負荷をかけない。

### 1.4 本番の辞書は1つに決まっている

| | |
|---|---|
| **本番** | 近現代口語小説UniDic `unidic-novel-v202512` |
| 検算用 | 近代文語UniDic `unidic-kindai-bungo-v202512`（Step 4 以降の一部でのみ） |
| 使わない | 現代書き言葉UniDic（cwj）／`unidic-lite` |

2026-09-22 に 4 つの辞書を 111 点で比べて決めた。全員が同じ辞書を使うこと。
根拠は `docs/dictionary_comparison.md` §10 にある。

> **⚠ なぜ「各自の好きな辞書」ではいけないか**
>
> 辞書が違えば語数・未知語率・語彙素が変わる。別の辞書で数えた頻度表を
> 並べて比べると，**作品の違いではなく辞書の違いを見ていることになる**。
> しかもエラーは出ない。だから `05_tokenise_unidic.py` は使った辞書を
> 出力の1行目と `tokenise_provenance.json` に記録し，
> `06_build_datasets.py` は `config/pipeline.yaml` と食い違えば警告する。
> **`[warn] **本番の辞書ではない。**` が出たら，そこで止まって直すこと。**

手で入れる場合（`00_bootstrap_mac.sh` を使えば自動で入る）:

```bash
mkdir -p /Users/Shared/jlit && cd /Users/Shared/jlit
curl -L -O https://clrd.ninjal.ac.jp/unidic_archive/2512/unidic-novel-v202512.zip
/usr/bin/unzip -q unidic-novel-v202512.zip
python3 ~/Documents/dh_project/JLit_Corpus_2026/scripts/check_unidic_dir.py /Users/Shared/jlit/unidic-novel-v202512
```

**展開先を Dropbox・iCloud の中にしないこと**（同期と競合して壊れる）。
`unzip` が失敗するときは `docs/dictionary_comparison.md` §9 に手順がある。

### 1.5 CPU の自動判定と授業用 `.zshrc`

持ち込みの Mac には **Apple Silicon（M シリーズ）と Intel** の両方がある。
`00_bootstrap_mac.sh` は最初に CPU を判定し，その機械に合うものを入れる。
受講生が機種を意識して手順を変える必要はない。

| | Apple Silicon | Intel |
|---|---|---|
| JDK | aarch64 版 | x64 版 |
| numba・llvmlite（UMAP 用） | 最新版 | Intel 用の最後の版に**自動で固定**（numba 0.62.1・llvmlite 0.45.1・numpy 2.3 系） |
| Homebrew の場所（`.zshrc`） | `/opt/homebrew` | `/usr/local` |

さらに次の2つも自動で処理する。

- **Apple Silicon なのにターミナルが Rosetta（Intel 互換）で動いている**ときは，
  ネイティブで実行し直す。Rosetta のままだと Intel 用の Python が入り，遅く壊れやすい
- **既存の仮想環境や JDK が別の CPU 用**（移行アシスタントで Intel 機から引き継いだ
  場合など）なら，消さずに名前を変えて退避し，作り直す

**授業用 `.zshrc`（`config/zshrc_jlit`）も同じスクリプトが入れる。**
既定では `~/.zshrc_jlit` に置き，今の `~/.zshrc` の末尾に読み込む1行を足す。
自分の設定（anaconda・pyenv など）はそのまま残る（元の `~/.zshrc` は
`~/.zshrc.bak.日時` にバックアップする）。これで次のことができるようになる。

- 辞書・Java・MALLET の環境変数（`env.sh`）を読み込む
- **`jlit` と打つだけで**，仮想環境が有効になりリポジトリに移動する
- `jl`（または `jn`）で JupyterLab が起動する
- 行の後ろの `#` がコメントになる（§1.0）。プロンプトに git のブランチ名が出る
- Rosetta で動いているターミナルを警告する

セットアップの後，**そのターミナルで一度だけ `exec zsh`** を実行すると反映される。

入れ方は `--zshrc=` で変えられる。

```bash
# 既定：~/.zshrc_jlit を置き，~/.zshrc から読み込む
bash scripts/00_bootstrap_mac.sh --zshrc=append
# ~/.zshrc を授業用に置き換える（元はバックアップに残る）
bash scripts/00_bootstrap_mac.sh --zshrc=replace
# .zshrc に触らない
bash scripts/00_bootstrap_mac.sh --zshrc=skip
```

⚠ **`pip freeze > requirements.txt` のような別名を自分の `.zshrc` に入れないこと**
（リポジトリの `requirements.txt` を上書きしてしまう）。

⚠ 共用 iMac ではホームがマシンごとに分かれる（§1.1）。マシンを移ったら
`00_bootstrap_mac.sh` をもう一度実行すれば，`.zshrc` も入り直す。

---

## 2. A：DH Lab 共用 iMac

### 2.1 リポジトリを取得する

ターミナル（`ターミナル.app`）を開いて：

```bash
mkdir -p ~/Documents/dh_project
cd ~/Documents/dh_project
git clone https://github.com/tomojitabata/JLit_Corpus_2026.git
cd JLit_Corpus_2026
```

Git の使い方は §5 で扱う。まず clone だけできればよい。

### 2.2 セットアップを走らせる

```bash
bash scripts/00_bootstrap_mac.sh
```

これ 1 本で次を行う。**`sudo` は一度も要求されない。**

0. **CPU を判定する**（Apple Silicon／Intel。§1.5）
1. `uv` を `~/.local/bin` に導入（既にあれば飛ばす）
2. `~/Documents/dh_project` を uv のプロジェクトにし（`pyproject.toml`），
   `~/Documents/dh_project/.venv` を作って `requirements.txt` のパッケージを入れる
   （Intel では numba・llvmlite の版を自動で固定する）
3. Jupyter カーネル「Python (JLit)」を登録
4. JDK 21 を `/Users/Shared/jlit/jdk` に展開（既にあれば飛ばす）
5. MALLET を `/Users/Shared/jlit/mallet` に展開し，**ヒープを搭載メモリの 1/4 に設定**
   （16 GB 機なら 4 GB。既定の 1 GB では本コーパスで落ちる）
6. **本番の辞書**（近現代口語小説UniDic v202512）を
   `/Users/Shared/jlit/unidic-novel-v202512` に展開し，
   `check_unidic_dir.py` で本当に読めるかを確かめる
7. `/Users/Shared/jlit/env.sh` を書く（`JLIT_UNIDIC_DIR` は 6 を指す）
8. 授業用 `.zshrc` を入れる（§1.5）
9. `00_env_check.py` を実行して結果を表示

終わったら，そのターミナルで一度だけ `exec zsh` を実行する。

**2回目以降はほとんどが `[have]` で飛ばされ，5 分ほどで終わる。**

### 2.3 毎回の作業開始

授業用 `.zshrc`（§1.5）を入れていれば，ターミナルを開いて

```bash
jlit
```

と打つだけでよい。入れていない場合は次の3行を打つ。

```bash
source /Users/Shared/jlit/env.sh
source ~/Documents/dh_project/.venv/bin/activate
cd ~/Documents/dh_project/JLit_Corpus_2026
```

プロンプト頭に `(.venv)` が付けば有効。

### 2.4 別のマシンに移ったとき

```bash
mkdir -p ~/Documents/dh_project && cd ~/Documents/dh_project
git clone https://github.com/tomojitabata/JLit_Corpus_2026.git && cd JLit_Corpus_2026
bash scripts/00_bootstrap_mac.sh
exec zsh
# 自分のバックアップ（my_work/）を GitHub から取ってくる（§5.2）
cd ~/Documents/dh_project/JLit_Corpus_2026
bash scripts/setup_my_work.sh <GitHubのユーザ名>
```

そのマシンで誰かが既にセットアップしていれば，JDK・MALLET・UniDic は
`[have]` で飛ばされ，仮想環境と授業用 `.zshrc` を作るだけで済む。

---

## 3. B：自分の MacBook

### 3.1 事前確認

```bash
# macOS 13 以降が望ましい
sw_vers -productVersion
# arm64 = Apple Silicon / x86_64 = Intel
uname -m
# 空きは 10 GB 以上あること
df -h / | awk 'NR==2{print $4}'
# 表示が出なければ xcode-select --install
xcode-select -p
```

`xcode-select --install`（コマンドラインツール）だけは入れておくこと。
Git とコンパイラが入る。ダイアログが出たら「インストール」を押す。
Intel Mac でも動く。スクリプトが CPU を判定し，JDK と numba・llvmlite を
その機種に合わせて入れる（§1.5）。**Apple Silicon の Mac では，ターミナルを
Rosetta で開かないこと**（`uname -m` が `x86_64` と出たら Rosetta で動いている）。

### 3.2 セットアップ

```bash
mkdir -p ~/Documents/dh_project && cd ~/Documents/dh_project
git clone https://github.com/tomojitabata/JLit_Corpus_2026.git && cd JLit_Corpus_2026
bash scripts/00_bootstrap_mac.sh --personal
```

`--personal` を付けると，共有物が `/Users/Shared/jlit` ではなく
**`~/.jlit`** に入る。自分の機械では共有する相手がいないので，ホームのほうが
片づけやすい。**それ以外は共用 iMac とまったく同じ手順である。**

### 3.3 毎回の作業開始

授業用 `.zshrc`（§1.5）を入れていれば `jlit` の1語でよい。入れていない場合は

```bash
source ~/.jlit/env.sh
source ~/Documents/dh_project/.venv/bin/activate
cd ~/Documents/dh_project/JLit_Corpus_2026
```

ホームは消えないので，`.zshrc` の設定は**この一度だけでよい**。

### 3.4 自分の Mac を使う人への注意

- **Homebrew が既に入っているなら**，それを使っても構わない
  （`brew install openjdk@21` など）。ただし授業中の質問には §2 の手順で答える
- **容量。** UniDic（zip 約 1.7 GB と展開後の辞書）＋ コーパス・モデルで 5–8 GB を見込む。
  256 GB の MacBook では `data/` の中間生成物を折々消すこと
- **メモリ。** 8 GB 機では MALLET のヒープが 2 GB になる（スクリプトが自動調整）。
  Step 8 のトピック数 sweep に時間がかかるが，動く
- **授業には持参すること。** 共用 iMac と自分の Mac を行き来する場合，
  §5 の Git 運用が前提になる

---

## 4. C：Windows 11

### 4.1 前提

PowerShell を管理者ではなく**通常の権限**で開く。

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Git.Git -e
winget install --id EclipseAdoptium.Temurin.21.JDK -e
```

`winget` が無い場合は「App Installer」を Microsoft Store から入れる。

### 4.2 リポジトリと仮想環境

```powershell
mkdir $HOME\Documents\dh_project -Force
cd $HOME\Documents\dh_project
git clone https://github.com/tomojitabata/JLit_Corpus_2026.git
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
cd JLit_Corpus_2026
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m ipykernel install --user --name jlit --display-name "Python (JLit)"
```

macOS と同じく，仮想環境は `dh_project\.venv`（リポジトリの親）に置く。

`Activate.ps1` が実行ポリシーで止まる場合：

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 4.3 UniDic と MALLET

**辞書は本番の `unidic-novel-v202512` を取る**（§1.4）。
`pip install unidic` → `python -m unidic download` で入るのは
**現代書き言葉の cwj であって本番の辞書ではない**。これで解析すると
未知語率が 4 倍近くになり，Mac 勢の結果と比べられなくなる。

```powershell
# C:\JLit\ のように空白と日本語を含まないパスに置く
mkdir C:\JLit
cd C:\JLit
curl.exe -L -O https://clrd.ninjal.ac.jp/unidic_archive/2512/unidic-novel-v202512.zip
Expand-Archive .\unidic-novel-v202512.zip -DestinationPath .
python $HOME\Documents\dh_project\JLit_Corpus_2026\scripts\check_unidic_dir.py C:\JLit\unidic-novel-v202512
```

MALLET は <https://github.com/mimno/Mallet/releases>（v202108 の `Mallet-202108-bin.zip`）から取得し，
**空白と日本語を含まないパス**に展開する。

```powershell
# 例：C:\JLit\mallet\ に展開したとき
[Environment]::SetEnvironmentVariable("MALLET","C:\JLit\mallet\bin\mallet.bat","User")
[Environment]::SetEnvironmentVariable("JLIT_UNIDIC_DIR","C:\JLit\unidic-novel-v202512","User")
```

`C:\Users\山田 太郎\Documents\...` のようなパスに置くと MALLET が動かない。

### 4.4 文字コード（Windows 固有・重要）

```powershell
[Environment]::SetEnvironmentVariable("PYTHONUTF8","1","User")
```

PowerShell を開き直してから作業する。これを設定しないと
`UnicodeDecodeError` が頻発する。本パイプラインの CSV はすべて
**BOM 付き UTF-8** で書き出してあるので，Excel でそのまま開ける。

---

## 5. 3つの置き場 — 教材・自分のバックアップ・課題の提出

この授業では，置き場を**役割ごとに3つ**に分ける。混ぜないこと。

| 置き場 | 置くもの | 受講生がすること |
|---|---|---|
| **コースのリポジトリ**（GitHub：`tomojitabata/JLit_Corpus_2026`） | 教材一式（スクリプト・配布版ノートブック・手順書） | clone と `bash scripts/update.sh` **だけ**。**push はしない**（できないように設定される） |
| **自分のバックアップ**（自分の GitHub の private リポジトリ。例 `jlit-work`） | `my_work/` — 自分が実行したノートブック・図・表・原稿 | 作業の終わりに push。別のマシン・自分の Mac では pull |
| **Zulip**（`dh-uosaka.zulipchat.com`）の非公開チャネル **2026年度テクスト分析論B** | 各 Step の課題・最終リポート・授業の連絡 | トピック「Step 1」…「Step 8」に投稿し，他の受講生の投稿を読む |

```
~/Documents/dh_project/JLit_Corpus_2026/   ← コースのリポジトリ（pull だけ）
├── notebooks/            配布版（開かない）
├── scripts/  docs/  config/  metadata/
├── data/                 生成物（どこにも push しない）
└── my_work/              ← 自分のバックアップ（独立した git。自分の GitHub へ push）
    ├── notebooks/        実行するノートブック（配布版のコピー）
    ├── results/          図（SVG）・表・課題のテンプレート StepN_report.md（save_fig() の書き出し先）
    └── _backup/          update.sh の退避先（バックアップには入れない）
```

なぜ分けるのか：

- **教材は全員で同じものを使う。** 受講生が教材に書き込めると，誰かの変更が
  全員に配られてしまう。コースのリポジトリへの push は `update.sh` と
  `setup_my_work.sh` が無効にする（`git push` しても手元で止まる）。
- **共用 iMac のホームはマシンごとに別々である**（§1.1）。自分の作業を
  自分の GitHub にバックアップしておけば，どのマシン・自分の Mac からでも続きができ，
  マシンの故障や再設定にも備えられる。
- **課題は Zulip で読み合う。** 同じトピックに全員の提出と講評が並ぶので，
  他の人のやり方から学べる。チャネルは今年度の受講生・TA・教員にしか見えない。

### 5.1 初回だけ（1）— GitHub のアカウント・空のリポジトリ・トークン

画面の文言は GitHub の更新で変わることがある。

1. <https://github.com> でアカウントを作る（大学のメールアドレスでよい）。
2. 右上の「＋」→ **New repository**。名前は `jlit-work`，**Private** を選ぶ。
   README・.gitignore・ライセンスは**付けない**（空のまま作る）。
3. **トークン**（パスワードの代わり）を作る。
   Settings → Developer settings → Personal access tokens → **Fine-grained tokens**
   → Generate new token。
   - Repository access：**Only select repositories** → `jlit-work`
   - Permissions：**Contents** を **Read and write**
   - 有効期限：**各自で決めてよい。** ただし期限が切れると `my_work/` の push・pull が
     できなくなる。授業の終わり（最終リポートの提出）より短くした人は，途中で
     トークンを作り直す必要がある（作り直し方は §7 の表）

   表示された `github_pat_…` をコピーしておく。**この画面を閉じると二度と表示されない。**

GitHub は git の操作でパスワードを受け付けない。**パスワードを聞かれたら
トークンを貼る。**

### 5.2 初回だけ（2）— `my_work/` を作ってバックアップを取る

```bash
git config --global user.name  "<氏名またはローマ字>"
git config --global user.email "<GitHub に登録したメールアドレス>"
cd ~/Documents/dh_project/JLit_Corpus_2026
bash scripts/setup_my_work.sh <GitHubのユーザ名>
```

`Username` には GitHub のユーザ名，`Password` には**トークン**を入れる。
macOS のキーチェーンに保存されるので，同じマシンでは2回目から聞かれない
（共用 iMac はホームがマシンごとなので，初めてのマシンではもう一度入れる）。

Windows（§4）では，Git for Windows に付いてくる **Git Bash** で同じ行を実行する
（`update.sh` も同じ）。

`[ OK ] バックアップができた` と出れば完了。`setup_my_work.sh` は

1. コースのリポジトリへの push を無効にし，
2. `my_work/` を独立した git リポジトリにして，push 先を自分の `jlit-work` にし，
3. `my_work/notebooks/` にノートブックをコピーし，
4. 大きいファイル（モデルなど。50 MB 超）をバックアップに入れないようチェックを置いて，
5. 最初の push をする。

**別のマシン・自分の Mac に移ったとき**も，教材を clone（§2.4）してから
同じ1行を実行する。GitHub のバックアップがあれば，それを取ってくる。

### 5.3 課題の提出（Zulip）

- 初回の授業で，Zulip の組織 `dh-uosaka.zulipchat.com` とチャネル
  **2026年度テクスト分析論B** に招待する。参加したら，左のチャネル一覧に
  このチャネルが見えることを確かめる（見えなければ申し出ること）。
- **各 Step の課題は，配られたテンプレートに書いて Zulip に1通で出す。**
  1. `my_work/results/StepN_report.md`（テンプレート。`copy_notebooks.py` が置く）に書く。
     見出し（設問・図表・再現のための情報）は**変えない**。
     字数は**全体で600–1000字程度**（図表と「再現のための情報」は含めない）。
  2. チャネル **2026年度テクスト分析論B** のトピック **「Step N」** を開き，
     テンプレートの中身をメッセージ欄に**貼り付ける**（Markdown の見出し・箇条書き・表がそのまま出る）。
  3. 図（SVG）と表（CSV）を**同じメッセージに**添付する（クリップのアイコン，またはドラッグ）。
     **1人1 Step につき1通**にまとめる。
  4. 図は番号で言及する（図1）。SVG は Zulip の画面に表示されないことがあるので，
     **図を見なくても論旨が追えるように**書く。
  5. **再提出は元の投稿を直さず，同じトピックに新しく投稿する。**
     投稿の時刻が提出の記録になる。
- **最終リポートは PDF で出す。** `my_work/results/final_report.md`（テンプレート）の構成で書き
  （**全体で4000–6000字程度**），Word・Pages・Google ドキュメントなど好きな道具で
  PDF にする。トピック **「最終リポート」** に，要旨（3〜5行）と PDF を1通で投稿する。
- テンプレートの最後の「再現のための情報」には，教材の版（`git rev-parse --short HEAD`）・
  辞書・マシン（ラベル番号）・変えた設定と乱数シードを書く。**何をどう数えたかを
  書けない結果は，他の人が確かめられない。**
- 他の受講生の投稿も読める。質問・気づきは同じトピックで返信する。
- `data/` の生成物やモデルは**添付しない**（1ファイルの大きさに上限がある）。
  課題のもとになったファイルは `my_work/` に置き，バックアップを push しておく。

### 5.4 毎回の作業の流れ

```bash
# 始め：教材と自分のバックアップを最新にする（§6.1）
jlit
bash scripts/update.sh
jupyter lab
```

```bash
# 終わり：自分のバックアップを GitHub に送る
cd ~/Documents/dh_project/JLit_Corpus_2026/my_work
git add -A
git commit -m "Step 2 の作業"
git push
```

push が済んだら，共用 iMac では**ログアウト**する（§1.1。ログイン画面への切り替えだけでは
自分の処理が裏で動き続ける）。

`update.sh` は最後に，`my_work/` にバックアップしていない変更が残っていれば知らせる。

### 5.5 図は SVG で

`save_fig()` が既定で SVG を `my_work/results/` に書き出す。SVG はテキストなので
Git の差分が取れ，拡大しても劣化しない。PNG を手で保存しないこと。

---

## 6. 起動と確認

```bash
# macOS（A も B も同じ）
# env.sh は /Users/Shared/jlit/env.sh または ~/.jlit/env.sh
source <env.sh のパス>
source ~/Documents/dh_project/.venv/bin/activate
cd ~/Documents/dh_project/JLit_Corpus_2026
# ALL OK を確認
python scripts/00_env_check.py
# 自分の作業フォルダ my_work/ とバックアップを用意する（初回のみ。§5.2）
bash scripts/setup_my_work.sh <GitHubのユーザ名>
jupyter lab
```

```powershell
# Windows
$HOME\Documents\dh_project\.venv\Scripts\Activate.ps1
cd $HOME\Documents\dh_project\JLit_Corpus_2026
python scripts\00_env_check.py
python scripts\copy_notebooks.py
jupyter lab
```

**`jupyter lab` はリポジトリ（`dh_project/JLit_Corpus_2026`）で起動すること。**
別の場所（古いコピーなど）で起動すると，そちらのスクリプトが動く。
ブラウザが開いたら **`my_work/notebooks/01_corpus_design.ipynb`** から順に進む。
**`notebooks/` は配布版なので開かない。** 配布版を実行すると出力が `.ipynb` に
書き込まれ，次の `git pull` が「Your local changes … would be overwritten」で止まる。
`my_work/` はコースのリポジトリの `.gitignore` で除外してあるので，いくら実行・編集してもよい
（うっかり配布版を開くと，最初のセルが `[注意]` を出す）。
カーネルが **Python (JLit)** になっていることを確認すること
（右上に表示される。違っていたら `Kernel → Change Kernel`）。

### 6.1 教材の更新（毎回の授業のはじめに）

**毎回の授業のはじめに**，素の `git pull` ではなく次の1行で取り込む。

```bash
cd ~/Documents/dh_project/JLit_Corpus_2026
bash scripts/update.sh
```

`update.sh` は次の順に動く。**何も消さない。**

0. コースのリポジトリへの push を無効にする（済んでいれば何もしない）
1. 手元で変わった配布ファイル（うっかり実行した `notebooks/*.ipynb` など）を
   `my_work/_backup/<日時>/` に退避し，配布版に戻す
2. 新しく配られるファイルと同名の手元ファイルがあれば，同じ所へ退避する
3. GitHub の最新版に進める
4. `my_work/` を自分の GitHub のバックアップと揃え（別のマシンで作業した分を取り込む），
   `copy_notebooks.py` で `my_work/notebooks/` を揃える。新しいステップのノートブックは
   そのままコピーされる。**教員が直したノートブックについては**
   - 自分のコピーに手を付けていなければ，新しい版で置き換える
   - 実行・編集していれば，**自分の版は残し**，新しい版を
     `08_topic_modelling__新版_0924.ipynb` のような別名で置く

何が起きるかを先に見たいときは `bash scripts/update.sh --dry-run`。

### 6.2 起動時の確認

`00_env_check.py` は次を表示する。

- **作業中のマシン名** — 共用 iMac ではこれを控えておく
- ホームの空き容量
- このマシンの共有物（`jdk` / `mallet` / `unidic-*` / `aozora-cache` の有無）
- Python・各パッケージ・UniDic・Java・MALLET・日本語フォント

**「UniDic 辞書」の行が `[  OK ]` でも中身を見ること。** 本番以外の辞書が
見つかった場合は `[ WARN] … — **本番ではない**` と出る（§1.4）。

---

## 7. よくあるつまずき

| 症状 | 原因 | 対処 |
|---|---|---|
| 前回の作業が見当たらない（共用 iMac） | 前回とは別のマシンにログインした（作業は前回のマシンのホームに残っている） | §1.1。控えたマシン名を確かめ，前回のマシンに移るか，`my_work/` を push してあれば `bash scripts/setup_my_work.sh <ユーザ名>` で取り出す |
| `uv: command not found` | PATH に `~/.local/bin` が無い | 端末を開き直す。または `export PATH="$HOME/.local/bin:$PATH"` |
| `ModuleNotFoundError: fugashi` | 仮想環境が有効でない | `source ~/Documents/dh_project/.venv/bin/activate` |
| `[ERR ] リポジトリが作業フォルダ dh_project の中にない` | `~/Documents` などに直接 clone した | 表示される手順で `~/Documents/dh_project` の中に clone し直す |
| `UniDic 辞書 未導入` | 共有辞書が無いマシン | `bash scripts/00_bootstrap_mac.sh` を再実行 |
| `[warn] **本番の辞書ではない。**` | cwj か unidic-lite に落ちている | §1.4。`JLIT_UNIDIC_DIR` を `unidic-novel-v202512` に向け直す |
| `[FATAL] param.cpp ... no such file: ./dicrc` | 辞書の展開が途中で切れている | `python3 scripts/check_unidic_dir.py <辞書のパス>`。`docs/dictionary_comparison.md` §9 |
| `[warn] トークン列は … で作られているが config は …` | 辞書を替えて 05 を回し直していない | 05 から順に回し直す。比較なら `--out` を分ける |
| `param.cpp ... no such file: mecabrc` | MeCab の設定ファイルがない | `mkdir -p ~/.jlit/etc && touch ~/.jlit/etc/mecabrc` |
| 図のラベルが □（豆腐） | 日本語フォント未設定 | macOS は Hiragino Sans が既定。`00_env_check.py` のフォント欄を見る |
| セルを実行しても**何も出ない** | 前の工程の生成物が無い | `[未実行] … がありません` が出るので，示されたセルを先に実行する |
| 図は出るが `period` が空の棒が1本だけ | メタデータが v1 の64点のまま | Step 3 の `00_extend_metadata.py` のセルを実行して v3 を作る |
| 図が画面に出ない | 上と同じか，`fig` を作る前でセルが止まった | エラーが出ていないか出力欄を最後まで見る |
| 画面の図が PNG に見える | 仕様 | 画面表示は PNG，**保存されるファイルは SVG**。`save_fig` が出すパスを見る |
| `java: command not found` | `env.sh` を source していない | §2.3 / §3.3 の2行を実行 |
| MALLET が `OutOfMemoryError` | ヒープ不足 | `$MALLET` の `MEMORY=` を増やす。8 GB 機では 2 GB が上限の目安 |
| MALLET が Windows で動かない | パスに空白・日本語 | `C:\JLit\` に置き直す |
| `UnicodeDecodeError`（Windows） | CP932 で読んでいる | §4.4 の `PYTHONUTF8=1` |
| 青空文庫の取得が遅い | 1 秒/件の間隔を守っている | 正常。共有キャッシュがあれば2回目から一瞬で済む |
| Jupyter のカーネルが違う | 既定カーネルを見ている | `Kernel → Change Kernel` で `Python (JLit)` |
| **KWIC の画面が開かない** | サーバが起動していない／ポートが使われている | `my_work/results/kwic_server.log` を見る。`Address already in use` なら，まず前に起動したサーバが残っていないか確かめる（Step 3 の「止める」セル）。それでも駄目なら `PORT` を 8766 などに変える（マシンごとに別なので，隣の人と番号を分ける必要はない） |
| `git pull` が `Your local changes to the following files would be overwritten by merge` で止まる | 配布版の `notebooks/*.ipynb` を直接開いて実行した（出力がファイルに書き込まれた） | `bash scripts/update.sh`（退避してから更新する，§6.1）。以後は `my_work/notebooks/` のコピーを開く |
| `git pull` が `untracked working tree files would be overwritten` で止まる | 新しく配られるファイルと同名のファイルを手で置いていた | 同上。`update.sh` が `my_work/_backup/<日時>/` へ退避する |
| `git push` が `'DISABLED' does not appear to be a git repository` で止まる | コースのリポジトリで push した（無効にしてある） | 正常。自分の作業は `cd my_work` してから push する（§5.4） |
| `my_work` の push で `Authentication failed` / `Invalid username or token` | パスワードの欄に GitHub のパスワードを入れた／トークンの期限切れ・権限不足 | §5.1 のトークンを入れる。作り直すときは Contents を Read and write に |
| 共用 iMac で処理が極端に遅い | 前にそのマシンを使った人がログアウトせずに離れ，その人の処理（word2vec・MALLET など）が裏で動いている | TA に伝える。自分は作業の終わりに必ずログアウトする（§1.1） |
| KWIC のセルが「ポート番号 8765 は別のもの（前の人のサーバなど）が使っているので，ポート番号 8766 を使う」と出す | 前の人の KWIC のサーバが残っている | 正常。自分の索引のサーバを別のポートで起動している。表示されたリンクを使う |
| `00_bootstrap_mac.sh` が「… が /Users/Shared/jlit に無く，そこに書く権限も無い」で止まる | そのマシンの `/Users/Shared/jlit` が旧い版のスクリプトで作られ，他の人が書けない権限になっている | 作った人（または TA）がそのマシンで `00_bootstrap_mac.sh` を再実行すると権限が直る。急ぐときは `--personal` を付けて実行する |
| `00_bootstrap_mac.sh` が「別のユーザーが取得中である」で止まる | 前の人のセットアップが裏で動いている（ログアウトせずに離れた） | 数分待って再実行する。2時間以上前のロックは自動で無視される |
| 途中から `my_work` の push・pull が `Authentication failed` になった | トークンの有効期限が切れた（§5.1） | GitHub で同じ設定のトークンを作り直し，保存済みの古いトークンを消す：`printf "protocol=https\nhost=github.com\n\n" \| git credential reject`。次の push でユーザ名と新しいトークンを入れる |
| `my_work` の push で `Repository not found` | GitHub に `jlit-work` を作っていない／ユーザ名の綴り違い | §5.1 の 2。`cd my_work && git remote -v` で push 先を確かめる |
| `[ERR ] 50 MB を超えるファイルはバックアップに入れない` | モデルなど大きい生成物をコミットしようとした | 表示のとおり `git restore --staged` で外す。作り直せるものはバックアップに入れない |
| `git pull` で `corpus_metadata_v3.csv` が衝突する | 旧い版のスクリプトで配布版を上書きした | `bash scripts/update.sh`（上書きされた版は退避される）。いまの版は `_local.csv` に書く |
| KWIC の画面が「サーバに接続できません」（セルは `[ok  ] 起動した` と出た） | 続けて「止める」のセルまで実行した，またはカーネルを止めた | 「画面を起動する」のセルをもう一度実行する。いまの版では「止める」は `STOP_KWIC = True` にしたときだけ止める |
| KWIC が `索引が無い` と言う | `15_kwic_index.py` を走らせていない | Step 3 §5.5 の索引のセルを実行する。05 を走らせ直したら索引も作り直す |
| KWIC の用例の著者が「（メタデータ無し）」 | その作品がメタデータに無い | 自分の版 `metadata/corpus_metadata_v3_local.csv` に行を足す（配布版 `corpus_metadata_v3.csv` は編集しない）。**出典の出ない用例は証拠にならない** |
| KWIC で「語彙に無い」と言われる | 列（語彙素／表層形）の選び違い・辞書の切り方・旧仮名 | 画面上の切替で列を変える。`S:` を付けるとその項だけ表層形で当たる |
| **UMAP を入れたのに図が t-SNE になる** | カーネルが `uv` の環境でない／`umap` という別パッケージ／numba と numpy の版違い | `python3 scripts/check_umap.py`（**ノートブックと同じカーネルで**）。原因別の対処が出る |
| `uv add umap-learn` したのに `ModuleNotFoundError` | **`uv add` はプロジェクト（`pyproject.toml` のある場所）単位。** `dh_project/pyproject.toml` が無い，または `dh_project` の外に clone したので，別の `.venv` に入った | `bash scripts/00_bootstrap_mac.sh` を実行し直す（`pyproject.toml` を作る）。急ぐときはカーネルの Python を名指し：`uv pip install --python "<sys.executable の値>" umap-learn` → **カーネルを再起動** |
| 同上（カーネル自体が別の Python） | JupyterLab のカーネルが `dh_project/.venv` 以外の Python | `Kernel → Change Kernel` で `Python (JLit)` に切り替えて**再起動**。無ければ `bash scripts/00_bootstrap_mac.sh` を実行し直す |
| `uv sync` のあと fugashi などが消えた | `uv sync` は `pyproject.toml` に無いものを消す | **`uv sync` は使わない。** `bash scripts/00_bootstrap_mac.sh` で入れ直す |
| `umap.UMAP` が無いというエラー | PyPI の `umap`（別物）が入っている | `uv remove umap` してから `uv add umap-learn`。`umap.__file__` が `umap/umap_.py` を指すこと |
| UMAP の初回だけ十数秒止まる | numba の JIT | 正常。2回目から速い。書けないマシンでは `NUMBA_CACHE_DIR` を自分の領域に向ける |
| **`Failed to build llvmlite` / `LLVM version is 20, llvmlite only officially supports 22`** | **Intel Mac**。llvmlite の x86_64 wheel は 0.45.1 が最後で，新しい版はソースからビルドしに行く | `bash scripts/00_bootstrap_mac.sh` を実行し直す（Intel なら版を自動で固定する，§1.5）。手で入れるなら：`uv pip install --python "<sys.executable>" --only-binary :all: "numba==0.62.1" "llvmlite==0.45.1" "numpy<2.4" umap-learn` |
| `00_env_check.py` の CPU 欄が「Python は x86_64 用（Rosetta）」 | Apple Silicon でターミナルを Rosetta で開いた／Intel 機から移行した仮想環境 | Rosetta を外したターミナルで `bash scripts/00_bootstrap_mac.sh` を実行し直す（別の CPU 用の仮想環境は自動で退避して作り直す） |
| ターミナルを開くと `[warn] このターミナルは Rosetta…` | 同上（授業用 `.zshrc` が警告している） | ターミナル.app を選んで「情報を見る」→「Rosetta を使用して開く」を外し，開き直す |
| Java がどうしても入らない | ネットワーク制限など | `uv pip install tomotopy` で LDA を代替し，**レポートに明記する** |

---

## 8. Step 1 の到達目標

- [ ] `python scripts/00_env_check.py` が `ALL OK` を返す
- [ ] 表示された**マシン名を控えた**（共用 iMac の場合）
- [ ] `my_work/notebooks/01_corpus_design.ipynb`（自分用のコピー）が最後まで実行できる
- [ ] `bash scripts/update.sh` が `[ OK ] 完了` で終わる
- [ ] `my_work/results/` に図が 1 枚（**SVG**）出力されている
- [ ] `setup_my_work.sh` で自分の GitHub（`jlit-work`）にバックアップを作り，その図を含めて push できた
- [ ] Zulip のチャネル **2026年度テクスト分析論B** が見え，トピック「Step 1」に課題を投稿した
- [ ] `save_fig()` が書いた SVG をブラウザで開き，日本語が読めることを確認した
- [ ] `docs/glossary.md` の **Step 0・Step 1** に目を通し，確認問題を解いた

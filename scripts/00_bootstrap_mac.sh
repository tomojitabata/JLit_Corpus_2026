#!/bin/bash
# =============================================================================
# 00_bootstrap_mac.sh — macOS のセットアップ（共用 iMac／自分の Mac 共通）
# -----------------------------------------------------------------------------
# sudo を一度も使わない。Homebrew も使わない（導入自体に admin が要るため）。
# JDK は Temurin の tar.gz を展開するだけで，インストーラを実行しない。
# したがって，共用機でも自分の Mac でも同じ手順でセットアップできる。
#
# 使い方
# 置き方（受講生・教員の動作確認とも）
#   mkdir -p ~/Documents/dh_project && cd ~/Documents/dh_project
#   git clone https://github.com/tomojitabata/JLit_Corpus_2026.git
#   cd JLit_Corpus_2026
#
#   bash scripts/00_bootstrap_mac.sh             # DH Lab 共用 iMac
#   bash scripts/00_bootstrap_mac.sh --personal  # 自分の Mac
#   bash scripts/00_bootstrap_mac.sh --check     # 何もせず現状だけ表示
#   bash scripts/00_bootstrap_mac.sh --with-bungo # 検算用の近代文語辞書も入れる
#   --zshrc=append（既定）|replace|skip        # 授業用 .zshrc の入れ方（下記）
#
# **CPU（Apple Silicon／Intel）は自動で判定する。** 持ち込みの Mac には両方ある。
#   * JDK は機種に合った版を取得する（aarch64／x64）
#   * Intel では umap-learn が使う numba・llvmlite を Intel 用の最後の版に固定する
#     （llvmlite の Intel 用 wheel は 0.45.1 が最後。固定しないとソースからの
#      ビルドに切り替わって失敗する）
#   * Apple Silicon なのにターミナルが Rosetta（Intel 互換）で動いているときは，
#     ネイティブで実行し直す（Intel 用の Python が入り，動作が遅く不安定になるため）
#   * 既存の仮想環境・JDK が別の CPU 用なら（移行アシスタントで引き継いだ場合など）
#     バックアップに退避して作り直す
#
# 授業用 .zshrc（config/zshrc_jlit）
#   append   ~/.zshrc_jlit に置き，~/.zshrc の末尾に読み込む1行を足す（自分の設定は残る）
#   replace  ~/.zshrc を置き換える（元の ~/.zshrc は日付つきのバックアップに残す）
#   skip     何もしない
#
# 二つのモードの違いは**重い共有物をどこに置くか**だけである。
#
#   共用 iMac (--なし)      /Users/Shared/jlit
#       macOS の /Users/Shared は admin 権限なしに全ユーザーが読み書きできる。
#       DH Lab の iMac は XCreds 認証でホームがマシンごとに別々なので，
#       JDK・MALLET・UniDic・取得済みテクストをここに置いておくと，
#       **同じマシンなら次回も，別のユーザーでも**使い回せる。
#
#   自分の Mac (--personal)  ~/.jlit
#       共有する相手がいないので自分のホームに置く。マシンが1台なので，
#       一度入れれば以後は何もしなくてよい。
#
# どちらでも仮想環境は**リポジトリの親（作業フォルダ）**に作る。
#
#   ~/Documents/dh_project/        作業フォルダ（uv のプロジェクト）
#   ├── pyproject.toml             uv add の行き先をここに固定する
#   ├── .venv/                     仮想環境（Jupyter カーネル「Python (JLit)」）
#   └── JLit_Corpus_2026/          git clone したリポジトリ（このスクリプト）
#
# リポジトリの中で `uv add umap-learn` などを実行すると，uv は親へ遡って
# dh_project/pyproject.toml を見つけ，カーネルと同じ .venv に入れる。
# リポジトリを Dropbox など同期フォルダの中に置いた場合（教員のマスター）は
# .venv が同期で壊れるので，代わりに ~/Documents/dh_project を使う。
# 作業フォルダは環境変数 JLIT_PROJECT_DIR で明示することもできる。
# 何度実行してもよい（冪等）。入っているものは飛ばす。
# =============================================================================
# ⚠ 編集するときの注意：日本語の直前の変数は必ず ${VAR} と書くこと。
#   macOS 標準の bash 3.2 は "$VAR（…）" の全角文字を変数名に取り込み，
#   set -u のもとで "VAR?: unbound variable" で止まる。
set -uo pipefail

# ---- CPU の判定 -------------------------------------------------------------
# uname -m は「いま動いているプロセスの」CPU を返す。Apple Silicon でも
# ターミナルが Rosetta で動いていると x86_64 と答えるので，マシン本体の CPU は
# hw.optional.arm64 で判定する。
RUN_ARCH="$(uname -m)"
HW_ARCH="$RUN_ARCH"
[ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ] && HW_ARCH="arm64"
if [ "$HW_ARCH" = arm64 ] && [ "$RUN_ARCH" != arm64 ]; then
  if [ -z "${JLIT_REEXEC:-}" ] && command -v arch >/dev/null 2>&1; then
    echo "  [note] ターミナルが Rosetta（Intel 互換）で動いている。Apple Silicon ネイティブで実行し直す"
    JLIT_REEXEC=1 exec arch -arm64 /bin/bash "$0" "$@"
  fi
  echo "  [ERR ] Apple Silicon の Mac で，Rosetta（Intel 互換）のまま動いている。"
  echo "         ターミナル.app の「情報を見る」で「Rosetta を使用して開く」のチェックを外し，ターミナルを開き直すこと。"
  exit 1
fi
case "$HW_ARCH" in
  arm64|aarch64) HW_ARCH="arm64";  JDK_ARCH="aarch64"; ARCH_LABEL="Apple Silicon（arm64）" ;;
  x86_64)        JDK_ARCH="x64";    ARCH_LABEL="Intel（x86_64）" ;;
  *)             JDK_ARCH="";       ARCH_LABEL="不明（${HW_ARCH}）" ;;
esac
# Intel で固定する版（llvmlite の Intel 用 wheel は 0.45.1 が最後。numba 0.62.1 と組）
INTEL_PINS=("numba==0.62.1" "llvmlite==0.45.1" "numpy<2.4")
# ファイル名を固定すると版が上がったとたんに 404 になるので，
# Adoptium の API に最新の GA 版を返させる。
JDK_URL="https://api.adoptium.net/v3/binary/latest/21/ga/mac/${JDK_ARCH}/jdk/hotspot/normal/eclipse"
# 旧 mimno.github.io/Mallet/dist/ は 404。GitHub Releases の配布物を使う。
MALLET_URL="https://github.com/mimno/Mallet/releases/download/v202108/Mallet-202108-bin.tar.gz"

# -----------------------------------------------------------------------------
# 辞書。2026-09-22 の比較実験（4 辞書・111 点）で本番用の辞書を決めた。
# 経緯と数値は docs/dictionary_comparison.md §10。
#
#   本番   unidic-novel v202512（近現代口語小説UniDic）未知語率 0.17%
#   検算用 unidic-kindai-bungo v202512（近代文語）A_文語体の助動詞率の確認用
#
# 現代書き言葉（cwj）はこのプロジェクトでは**使わない**。同じ 111 点で
# 未知語率が novel の 3.9 倍（0.66%）であり，平均語長も短い。
# NINJAL の配布は日付版（v202512）に移っている。3.1.x を指す URL を
# 残しておくと，版が更新されたとたんに 404 になる。
# -----------------------------------------------------------------------------
UNIDIC_DIR_NAME="unidic-novel-v202512"
UNIDIC_URL="https://clrd.ninjal.ac.jp/unidic_archive/2512/unidic-novel-v202512.zip"
BUNGO_DIR_NAME="unidic-kindai-bungo-v202512"
BUNGO_URL="https://clrd.ninjal.ac.jp/unidic_archive/2512/unidic-kindai-bungo-v202512.zip"

MODE="run"
KIND="lab"                     # lab = 共用 iMac ／ personal = 自分の Mac
SHARED="/Users/Shared/jlit"
WITH_BUNGO="no"                # 検算用の近代文語UniDic も入れるか
ZSHRC_MODE="append"            # 授業用 .zshrc の入れ方
for a in "$@"; do
  case "$a" in
    --zshrc=append|--zshrc=replace|--zshrc=skip) ZSHRC_MODE="${a#--zshrc=}" ;;
    --personal|--home) KIND="personal"; SHARED="$HOME/.jlit" ;;
    --check) MODE="check" ;;
    --with-bungo) WITH_BUNGO="yes" ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "不明な引数: $a"; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVFILE="$SHARED/env.sh"

# 作業フォルダ（仮想環境の置き場）を決める
PROJECT_DEFAULT="$HOME/Documents/dh_project"
case "$ROOT" in
  */Dropbox/*|*/CloudStorage/*|*"/Google Drive/"*|*/OneDrive*|*"/Mobile Documents/"*)
    IN_CLOUD="yes" ;;
  *) IN_CLOUD="no" ;;
esac
if [ -n "${JLIT_PROJECT_DIR:-}" ]; then
  PROJECT="${JLIT_PROJECT_DIR%/}"
elif [ "$IN_CLOUD" = yes ]; then
  PROJECT="$PROJECT_DEFAULT"
else
  PROJECT="$(dirname "$ROOT")"
fi
VENV="$PROJECT/.venv"
PY="$VENV/bin/python"

say()  { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf '  [ok  ] %s\n' "$1"; }
skip() { printf '  [have] %s\n' "$1"; }
warn() { printf '  [warn] %s\n' "$1"; }
die()  { printf '  [ERR ] %s\n' "$1"; exit 1; }

# ---- 共有ディレクトリ（同じマシンを複数のユーザーが順に使う） -------------------
# /Users/Shared/jlit は，最初にセットアップした人の持ち物として作られる。
# ふつうの権限（755）のままだと2人目以降が書けずに止まるので，/Users/Shared
# そのものと同じ 1777（誰でも書けるが，他人のファイルは消せない）にしておく。
# 辞書・JDK・MALLET は，他の人が書き換えられないよう読み取り専用（a+rX）で置く。
share_dir() {   # $1: ディレクトリ。無ければ作り，自分の持ち物なら 1777 にする
  mkdir -p "$1" 2>/dev/null || return 1
  if [ "$KIND" = lab ] && [ -O "$1" ]; then chmod 1777 "$1" 2>/dev/null; fi
  return 0
}
# 取得中はロックを掛ける。前の人がファストユーザースイッチで離れ，その人の
# セットアップが裏で動き続けていると，同じ辞書を2人が同時に展開してしまう。
LOCKS=""
release_locks() { local d; for d in $LOCKS; do rmdir "$d" 2>/dev/null; done; }
trap release_locks EXIT
take_lock() {   # $1: 名前。取れたら 0，別のユーザーが取得中なら 1
  local d="$SHARED/.lock.$1"
  if mkdir "$d" 2>/dev/null; then LOCKS="$LOCKS $d"; return 0; fi
  # 2時間を超えたロックは，途中で止まった取得の残りとみなして外す
  # （別のユーザーのロックは 1777 の下では消せないので，そのときは無視して進む）
  if [ -n "$(find "$d" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    if rmdir "$d" 2>/dev/null && mkdir "$d" 2>/dev/null; then
      LOCKS="$LOCKS $d"
    else
      warn "2時間以上前のロック ${d} が残っている（途中で止まった取得の残り）。無視して進む"
    fi
    return 0
  fi
  return 1
}
need_write() {  # $1: 何を入れるか。共有ディレクトリに書けなければ止める
  [ -w "$SHARED" ] && return 0
  die "$1 が ${SHARED} に無く，そこに書く権限も無い。このマシンで ${SHARED} を作った人に 00_bootstrap_mac.sh を再実行してもらう（これで権限が直る）か，--personal を付けて実行すること"
}
busy() {        # $1: 何を, $2: ロックの名前
  die "$1 は，このマシンの別のユーザーが取得中である（${SHARED}/.lock.${2}）。数分待ってから再実行すること（前の人がログアウトせずに離れ，その人のセットアップが裏で動いている可能性がある）"
}

# -----------------------------------------------------------------------------
say "0. 環境の確認"
printf '  マシン名        %s\n' "$(scutil --get ComputerName 2>/dev/null || hostname)"
printf '  ホスト名        %s\n' "$(hostname -s)"
# set -u で実行しているので，USER が無い環境（cron・一部の CI）で
# ここが未定義変数エラーになる。その場合は id から取得する。
printf '  ユーザー        %s\n' "${USER:-$(id -un)}"
printf '  ホーム          %s\n' "$HOME"
printf '  macOS           %s\n' "$(sw_vers -productVersion)"
printf '  CPU             %s\n' "$ARCH_LABEL"
printf '  空き容量        %s\n' "$(df -h / | awk 'NR==2{print $4}')"
printf '  共有ディレクトリ %s\n' "$SHARED"
printf '  リポジトリ      %s\n' "$ROOT"
printf '  作業フォルダ    %s（仮想環境 %s）\n' "$PROJECT" "$VENV"
printf '  想定            %s\n' \
  "$([ "$KIND" = lab ] && echo 'DH Lab 共用 iMac' || echo '自分の Mac')"

if [ "$IN_CLOUD" = yes ] && [ -z "${JLIT_PROJECT_DIR:-}" ]; then
cat <<NOTE

  このリポジトリは同期フォルダ（Dropbox など）の中にある。
  仮想環境は同期で壊れるので，ここではなく ${PROJECT} に作る。
  動作確認は受講生と同じく ~/Documents/dh_project に clone したもので行うこと。
NOTE
elif [ "$(basename "$PROJECT")" != "dh_project" ] && [ -z "${JLIT_PROJECT_DIR:-}" ]; then
cat <<NOTE

  [ERR ] リポジトリが作業フォルダ dh_project の中にない（今は ${ROOT}）。
  仮想環境はリポジトリの親に作るので，このままでは ${PROJECT} に作ってしまう。
  次の手順で置き直すこと。

    mkdir -p ~/Documents/dh_project && cd ~/Documents/dh_project
    git clone https://github.com/tomojitabata/JLit_Corpus_2026.git
    cd JLit_Corpus_2026 && bash scripts/00_bootstrap_mac.sh $*

  （別の場所を使いたいときは JLIT_PROJECT_DIR=<作業フォルダ> を付けて実行する）
NOTE
  exit 1
fi

if [ "$KIND" = lab ]; then
cat <<'NOTE'

  注意：DH Lab の iMac はホームが**マシンごとに別々**（XCreds 認証）。
  このマシンのホームは次回も残っているが，別の iMac にログインすると，
  そのマシンの新しいホームから始まる（仮想環境も成果物もそこには無い）。
    * 重い共有物（JDK・MALLET・UniDic・取得済みテクスト）はマシンごとに
      /Users/Shared/jlit に置く。同じマシンなら次回も，別の人でも使える
    * 自分の成果物は Git で持ち運ぶ（別のマシンで続きをするため）
    * どのマシンを使ったかを控えておくこと（上の「マシン名」）
NOTE
else
cat <<'NOTE'

  自分の Mac として設定する。共有物は ~/.jlit に入る。
  ホームは消えないので，設定はこの一度だけでよい。
  ただし成果物は Git で持ち運ぶこと（授業では共用 iMac も使うため）。
NOTE
fi

if [ "$MODE" = check ]; then
  say "現状"
  [ -d "$SHARED/jdk" ]     && skip "JDK      $SHARED/jdk"     || warn "JDK なし"
  [ -d "$SHARED/mallet" ]  && skip "MALLET   $SHARED/mallet"  || warn "MALLET なし"
  [ -d "$SHARED/$UNIDIC_DIR_NAME" ] \
    && skip "UniDic   $SHARED/${UNIDIC_DIR_NAME}（本番）" \
    || warn "本番の辞書なし（${UNIDIC_DIR_NAME}）"
  [ -d "$SHARED/$BUNGO_DIR_NAME" ] \
    && skip "検算用   $SHARED/$BUNGO_DIR_NAME" \
    || warn "検算用の辞書なし（${BUNGO_DIR_NAME}。--with-bungo で入る）"
  [ -d "$SHARED/unidic" ] \
    && warn "以前の辞書（cwj）が $SHARED/unidic に残っている（本番では使わない。消してよい）"
  [ -d "$VENV" ]           && skip "仮想環境 $VENV"           || warn "仮想環境なし（${VENV}）"
  [ -f "$ENVFILE" ]        && skip "env.sh   $ENVFILE"        || warn "env.sh なし"
  exit 0
fi

share_dir "$SHARED" || die "$SHARED を作れない。--personal を付けて実行すること"
# 書けなくても止めない。JDK・MALLET・辞書がそろっていれば読むだけで足りる
# （足りないものがあれば，それを入れる段で need_write が止める）。
[ -w "$SHARED" ] || warn "${SHARED} には書けない（別のユーザーの持ち物）。そろっているものを読んで使う"

# -----------------------------------------------------------------------------
say "1. uv（Python の管理）"
if command -v uv >/dev/null 2>&1; then
  skip "uv $(uv --version 2>/dev/null)"
else
  # uv は ~/.local/bin に入る。admin 権限は要らない
  curl -LsSf https://astral.sh/uv/install.sh | sh || die "uv の導入に失敗"
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || die "uv が PATH に入らない。端末を開き直すこと"
  ok "uv を $HOME/.local/bin に導入した"
fi
export PATH="$HOME/.local/bin:$PATH"

# -----------------------------------------------------------------------------
say "2. 仮想環境とパッケージ（作業フォルダ ${PROJECT}）"
mkdir -p "$PROJECT" || die "$PROJECT を作れない"
cd "$ROOT"
# 作業フォルダを uv のプロジェクトにする（pyproject.toml だけを置く）。
# これで，リポジトリの中で uv add を実行しても行き先がこの .venv に定まる。
if [ -f "$PROJECT/pyproject.toml" ]; then
  skip "pyproject.toml は既にある（${PROJECT}）"
else
  (cd "$PROJECT" && uv init --bare --name dh-project --python 3.12 >/dev/null 2>&1) \
    && ok "$PROJECT を uv のプロジェクトにした（pyproject.toml）" \
    || warn "pyproject.toml を作れなかった（uv add は使えない。uv pip install --python を使う）"
fi
# 既存の仮想環境が別の CPU 用なら退避して作り直す（移行アシスタントで
# Intel 機から Apple Silicon 機へ引き継いだ場合など。消さずに名前を変える）
if [ -x "$PY" ]; then
  VENV_ARCH="$("$PY" -c 'import platform; print(platform.machine())' 2>/dev/null)"
  if [ -n "$VENV_ARCH" ] && [ "$VENV_ARCH" != "$HW_ARCH" ]; then
    bak="${VENV}.${VENV_ARCH}.$(date +%Y%m%d%H%M)"
    mv "$VENV" "$bak" \
      && warn "既存の仮想環境は ${VENV_ARCH} 用だった。${bak} に退避して作り直す（不要なら消してよい）"
  fi
fi
if [ -x "$PY" ]; then
  skip "仮想環境は既にある（${VENV}，${HW_ARCH}）"
else
  uv venv --python 3.12 "$VENV" || die "仮想環境を作れない"
  ok "仮想環境を作った（${VENV}，Python 3.12）"
fi
# 旧い置き方（リポジトリ/.venv）が残っていれば知らせる
[ -d "$ROOT/.venv" ] && [ "$ROOT/.venv" != "$VENV" ] \
  && warn "旧い仮想環境 $ROOT/.venv が残っている。使わないので消してよい"
if [ "$HW_ARCH" = x86_64 ]; then
  # Intel：新しい llvmlite は Intel 用 wheel が無く，ソースからのビルドに切り替わって
  # 失敗する。使える最後の組み合わせを先に入れ，requirements.txt にも同じ制約を掛ける。
  echo "  Intel Mac なので numba・llvmlite・numpy の版を固定する: ${INTEL_PINS[*]}"
  CONSTRAINTS="$(mktemp)"
  printf '%s\n' "${INTEL_PINS[@]}" > "$CONSTRAINTS"
  uv pip install --python "$PY" --only-binary :all: "${INTEL_PINS[@]}" \
    || die "Intel 用の numba・llvmlite を入れられない（wheel が無い）。出力をそのまま教員に見せること"
  uv pip install --python "$PY" -c "$CONSTRAINTS" -r requirements.txt \
    || die "パッケージの導入に失敗。requirements.txt を確認すること"
  rm -f "$CONSTRAINTS"
  ok "Intel 用に固定した版で入れた（numpy は 2.3 系）"
else
  uv pip install --python "$PY" -r requirements.txt \
    || die "パッケージの導入に失敗。requirements.txt を確認すること"
fi
uv pip install --python "$PY" ipykernel >/dev/null 2>&1
"$PY" -m ipykernel install --user --name jlit \
  --display-name "Python (JLit)" >/dev/null 2>&1 \
  && ok "Jupyter カーネル 'Python (JLit)' を登録した"

# -----------------------------------------------------------------------------
say "3. JDK（MALLET には Java が要る。admin 権限は使わない）"
# 既存の JDK が別の CPU 用なら退避して取り直す
JAVA_BIN="$SHARED/jdk/Contents/Home/bin/java"
if [ -x "$JAVA_BIN" ]; then
  JDK_FILE="$(file -b "$JAVA_BIN" 2>/dev/null)"
  case "$HW_ARCH:$JDK_FILE" in
    arm64:*arm64*|x86_64:*x86_64*) : ;;
    *) if [ -w "$SHARED" ]; then
         bak="$SHARED/jdk.old.$(date +%Y%m%d%H%M)"
         mv "$SHARED/jdk" "$bak" && warn "既存の JDK はこの CPU 用ではなかった（${JDK_FILE}）。${bak} に退避して取り直す"
       fi ;;
  esac
fi
if [ -d "$SHARED/jdk/Contents/Home" ]; then
  skip "JDK は既にある（${JDK_ARCH}）"
else
  need_write "JDK"
  take_lock jdk || busy "JDK" jdk
  tmp="$(mktemp -d)"
  echo "  Temurin 21 (${JDK_ARCH}) を取得中（約 190 MB）…"
  if curl -L --fail -o "$tmp/jdk.tar.gz" "$JDK_URL"; then
    tar xzf "$tmp/jdk.tar.gz" -C "$tmp"
    src="$(find "$tmp" -maxdepth 2 -name 'Contents' -type d | head -1)"
    # 仮の名前で置いてから名前を変える（途中の状態を他の人に見せない）
    part="$SHARED/.jdk.part.$$"
    if [ -n "$src" ] && mkdir -p "$part" && cp -R "$(dirname "$src")"/* "$part/"; then
      rm -rf "$SHARED/jdk" 2>/dev/null
      if [ -e "$SHARED/jdk" ]; then
        warn "壊れた ${SHARED}/jdk を消せない（別のユーザーの持ち物）。その人に消してもらうこと"
      else
        mv "$part" "$SHARED/jdk" && chmod -R a+rX "$SHARED/jdk" && ok "JDK を展開した"
      fi
    fi
    rm -rf "$part" 2>/dev/null
  else
    warn "JDK を取得できなかった。Step 8 の MALLET が使えない。"
    warn "  代替：uv pip install tomotopy（Java 不要の LDA）を使い，リポートに明記する"
  fi
  rm -rf "$tmp"
fi
[ -d "$SHARED/jdk/Contents/Home" ] && export JAVA_HOME="$SHARED/jdk/Contents/Home"

# -----------------------------------------------------------------------------
say "4. MALLET"
if [ -x "$SHARED/mallet/bin/mallet" ]; then
  skip "MALLET は既にある"
else
  need_write "MALLET"
  take_lock mallet || busy "MALLET" mallet
  tmp="$(mktemp -d)"
  if curl -L --fail -o "$tmp/mallet.tgz" "$MALLET_URL"; then
    tar xzf "$tmp/mallet.tgz" -C "$tmp"
    src="$(find "$tmp" -maxdepth 1 -type d -name 'Mallet-*' | head -1)"
    # 既定のヒープ 1 GB では本コーパス（約 600 万語）で足りない。
    # 搭載メモリの 1/4 を目安にする（16 GB なら 4 GB）。
    if [ -n "$src" ] && [ -f "$src/bin/mallet" ]; then
      ram_gb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 8589934592) / 1073741824 ))
      heap=$(( ram_gb / 4 )); [ "$heap" -lt 2 ] && heap=2
      /usr/bin/sed -i '' -E "s/^MEMORY=.*$/MEMORY=\"\\\${MALLET_MEMORY:-${heap}g}\"/" "$src/bin/mallet" \
        && ok "MALLET のヒープを ${heap} GB にした（搭載 ${ram_gb} GB の 1/4。既定 1 GB では足りない）"
      chmod +x "$src/bin/mallet"
      # 仮の名前で置いてから名前を変える（途中の状態を他の人に見せない）
      part="$SHARED/.mallet.part.$$"
      rm -rf "$part" 2>/dev/null
      if mv "$src" "$part"; then
        rm -rf "$SHARED/mallet" 2>/dev/null
        if [ -e "$SHARED/mallet" ]; then
          warn "壊れた ${SHARED}/mallet を消せない（別のユーザーの持ち物）。その人に消してもらうこと"
        else
          mv "$part" "$SHARED/mallet" && chmod -R a+rX "$SHARED/mallet" && ok "MALLET を展開した"
        fi
      fi
      rm -rf "$part" 2>/dev/null
    fi
  else
    warn "MALLET を取得できなかった"
  fi
  rm -rf "$tmp"
fi

# -----------------------------------------------------------------------------
# 辞書を1つ入れる。$1 = 展開先のディレクトリ名，$2 = URL，$3 = 説明
#
# 展開は失敗しても**それらしいディレクトリを残す**ことがある
# （macOS のアーカイブユーティリティで起きる）。一見すると成功して
# いるように見えて，解析を始めた段階で「./dicrc が無い」というエラーで止まる。
# そこで /usr/bin/unzip を明示して使い，展開後に必ず検査する。
fetch_dict() {
  local name="$1" url="$2" label="$3" tmp src
  if [ -f "$SHARED/$name/dicrc" ] || [ -f "$SHARED/$name/sys.dic" ]; then
    skip "$label は既にある（$SHARED/${name}）"
    return 0
  fi
  need_write "$label"
  take_lock "$name" || busy "$label" "$name"
  # ロックを取るまでの間に別の人が入れ終えていれば，それを使う
  if [ -f "$SHARED/$name/dicrc" ] || [ -f "$SHARED/$name/sys.dic" ]; then
    skip "$label は既にある（$SHARED/${name}）"
    return 0
  fi
  tmp="$(mktemp -d)"
  echo "  $label を取得中（約 1.7 GB。数分かかる）…"
  if curl -L --fail -o "$tmp/d.zip" "$url"; then
    # Dropbox 内に展開すると同期と競合して壊れることがあるので，
    # 一時ディレクトリで展開してから移す（docs/dictionary_comparison.md §9）。
    /usr/bin/unzip -q "$tmp/d.zip" -d "$tmp/x"
    src="$(find "$tmp/x" -maxdepth 3 -name 'sys.dic' | head -1)"
    if [ -n "$src" ]; then
      # 仮の名前で置いてから名前を変える。展開の途中で別の人が見ても
      # 「dicrc はあるが sys.dic が無い」といった半端な辞書を読み込むことがない
      local part="$SHARED/.$name.part.$$"
      if mkdir -p "$part" && cp -R "$(dirname "$src")"/* "$part/"; then
        rm -rf "$SHARED/$name" 2>/dev/null
        if [ -e "$SHARED/$name" ]; then
          warn "壊れた ${SHARED}/${name} を消せない（別のユーザーの持ち物）。その人に消してもらうこと"
        else
          mv "$part" "$SHARED/$name" && chmod -R a+rX "$SHARED/$name" \
            && ok "$label を $SHARED/$name に展開した（このマシンの全員が使える）"
        fi
      fi
      rm -rf "$part" "$tmp" 2>/dev/null
      [ -f "$SHARED/$name/sys.dic" ] && return 0
      warn "$label を $SHARED/$name に置けなかった"
      return 1
    fi
    warn "$label の中身が想定と違う（sys.dic が見つからない）"
  else
    warn "$label を取得できなかった（${url}）"
  fi
  rm -rf "$tmp"
  return 1
}

say "5. UniDic（共有辞書。本番は ${UNIDIC_DIR_NAME}・zip で約 1.7 GB）"
echo "  本番の辞書は 2026-09-22 の比較実験で決めた"
echo "  （近現代口語小説UniDic。未知語率 0.17%。docs/dictionary_comparison.md §10）"
if ! fetch_dict "$UNIDIC_DIR_NAME" "$UNIDIC_URL" "近現代口語小説UniDic v202512"; then
  warn "本番の辞書が入らなかった。unidic-lite で代替する"
  warn "**代替の辞書で出した数値は本番の結果と比べられない。報告に明記すること**"
  uv pip install --python "$PY" unidic-lite >/dev/null 2>&1
fi
if [ "$WITH_BUNGO" = yes ]; then
  fetch_dict "$BUNGO_DIR_NAME" "$BUNGO_URL" "近代文語UniDic v202512（検算用）" || true
else
  echo "  検算用の近代文語UniDic は入れない（--with-bungo で入る）"
fi
# 展開できたかを実際に読み込んで確かめる
if [ -x "$PY" ]; then
  for d in "$SHARED/$UNIDIC_DIR_NAME" "$SHARED/$BUNGO_DIR_NAME"; do
    [ -d "$d" ] && "$PY" "$ROOT/scripts/check_unidic_dir.py" "$d" \
      | tail -5
  done
fi

# -----------------------------------------------------------------------------
say "6. 環境変数のファイルを書く"
ENVNEW="$(mktemp)"
cat > "$ENVNEW" <<EOF
# JLit — このマシンの共有環境。作業前に source すること
#   source $ENVFILE
export JLIT_SHARED="$SHARED"
export JLIT_UNIDIC_DIR="$SHARED/$UNIDIC_DIR_NAME"
export JLIT_UNIDIC_BUNGO="$SHARED/$BUNGO_DIR_NAME"   # 検算用。本番では使わない
export JLIT_AOZORA_CACHE="$SHARED/aozora-cache"
export JAVA_HOME="$SHARED/jdk/Contents/Home"
export MALLET="$SHARED/mallet/bin/mallet"
export PATH="\$JAVA_HOME/bin:\$HOME/.local/bin:\$PATH"
EOF
# env.sh の中身は誰が書いても同じ。同じなら書き直さない。別のユーザーの
# 持ち物で書けなければ，自分用（~/.jlit/env.sh）に書く（授業用 .zshrc は両方を読む）
if [ -f "$ENVFILE" ] && cmp -s "$ENVNEW" "$ENVFILE"; then
  skip "${ENVFILE}（中身は同じ）"
elif { [ -f "$ENVFILE" ] && [ -w "$ENVFILE" ]; } || { [ ! -f "$ENVFILE" ] && [ -w "$SHARED" ]; }; then
  cp "$ENVNEW" "$ENVFILE" && chmod a+r "$ENVFILE" && ok "$ENVFILE を書いた"
else
  mkdir -p "$HOME/.jlit" && cp "$ENVNEW" "$HOME/.jlit/env.sh" \
    && warn "$ENVFILE は別のユーザーの持ち物で書けないので，$HOME/.jlit/env.sh に書いた"
  ENVFILE="$HOME/.jlit/env.sh"
fi
rm -f "$ENVNEW"
# 青空文庫の XHTML の共有キャッシュは，誰が取得しても次の人が使えるよう 1777 にする
share_dir "$SHARED/aozora-cache"
chmod -R a+rX "$SHARED" 2>/dev/null

# Jupyter を env.sh 抜きで起動しても辞書・Java・MALLET が見えるように，
# 同じ環境変数を 'Python (JLit)' カーネルの kernel.json に書き込む。
"$PY" - "$ENVFILE" "$VENV" <<'PY' \
  && ok "カーネル 'Python (JLit)' に環境変数を持たせた（env.sh を読まずに Jupyter を起動してもよい）" \
  || warn "カーネルに環境変数を書き込めなかった。Jupyter の前に env.sh を source すること"
import json, os, subprocess, sys
from jupyter_client.kernelspec import KernelSpecManager
envfile, venv = sys.argv[1], sys.argv[2]
out = subprocess.run(['/bin/bash', '-c', f'. "{envfile}" >/dev/null 2>&1; env -0'],
                     capture_output=True, text=True, check=True).stdout
env = {}
for item in out.split('\0'):
    k, _, v = item.partition('=')
    if k.startswith('JLIT_') or k in ('JAVA_HOME', 'MALLET', 'PATH'):
        env[k] = v
env['PATH'] = os.path.join(venv, 'bin') + os.pathsep + env.get('PATH', '')
f = os.path.join(KernelSpecManager().get_kernel_spec('jlit').resource_dir, 'kernel.json')
spec = json.load(open(f, encoding='utf-8'))
spec['env'] = env
json.dump(spec, open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
PY

# -----------------------------------------------------------------------------
say "6b. 授業用のシェル設定（.zshrc。--zshrc=${ZSHRC_MODE}）"
ZSRC="$ROOT/config/zshrc_jlit"
MARK='# JLit 授業用（00_bootstrap_mac.sh が追加）'
if [ ! -f "$ZSRC" ]; then
  warn "$ZSRC が無い。git pull してから実行し直すこと"
elif [ "$ZSHRC_MODE" = skip ]; then
  skip ".zshrc は変更しない（--zshrc=skip）"
elif [ "$ZSHRC_MODE" = replace ]; then
  if [ -f "$HOME/.zshrc" ] && cmp -s "$ZSRC" "$HOME/.zshrc"; then
    skip "~/.zshrc は既に授業用"
  else
    [ -f "$HOME/.zshrc" ] && cp "$HOME/.zshrc" "$HOME/.zshrc.bak.$(date +%Y%m%d%H%M)" \
      && ok "元の ~/.zshrc を ~/.zshrc.bak.$(date +%Y%m%d%H%M) にバックアップした"
    cp "$ZSRC" "$HOME/.zshrc" && ok "~/.zshrc を授業用に置き換えた"
  fi
else
  # append：授業用の設定は ~/.zshrc_jlit に置き，~/.zshrc からは1行で読む。
  # 自分の設定（anaconda・pyenv など）は残る。授業用が後に読まれるので PATH と
  # JAVA_HOME は授業用が優先される。
  cp "$ZSRC" "$HOME/.zshrc_jlit" && ok "~/.zshrc_jlit を置いた（${ARCH_LABEL} 用の Homebrew の場所も自動で判定する）"
  if grep -qF '.zshrc_jlit' "$HOME/.zshrc" 2>/dev/null; then
    skip "~/.zshrc は既に ~/.zshrc_jlit を読み込んでいる"
  else
    [ -f "$HOME/.zshrc" ] && cp "$HOME/.zshrc" "$HOME/.zshrc.bak.$(date +%Y%m%d%H%M)"
    printf '\n%s\n[ -f ~/.zshrc_jlit ] && source ~/.zshrc_jlit\n' "$MARK" >> "$HOME/.zshrc" \
      && ok "~/.zshrc の末尾に読み込みの1行を足した（元は ~/.zshrc.bak.* にバックアップした）"
  fi
fi

# -----------------------------------------------------------------------------
say "7. 確認"
# shellcheck disable=SC1090
. "$ENVFILE"
"$PY" "$ROOT/scripts/00_env_check.py"

cat <<EOF

--------------------------------------------------------------------------
このターミナルで一度だけ  exec zsh  を実行する（新しい設定を読み込む）。
次からは，ターミナルを開いて

    jlit          ← 仮想環境を有効にしてリポジトリへ移動する
    jl            ← JupyterLab を起動する（カーネルは Python (JLit)）

（--zshrc=skip のときは，代わりに次の3行を打つ）

    source $ENVFILE
    source $VENV/bin/activate
    cd $ROOT

パッケージを足すときはリポジトリの中で uv add <名前>（$PROJECT の .venv に入る）。
**uv sync は使わないこと**（requirements.txt で入れたものが消える）。

$([ "$KIND" = lab ] && cat <<'L'
**初めての iMac に移ったら，このスクリプトをもう一度実行すること。**
ホームはマシンごとに別々なので，そのマシンでは新しいホームから始まる
（このマシンの作業はこのマシンに残る）。成果物は Git で持ち運ぶこと。
L
)
--------------------------------------------------------------------------
EOF

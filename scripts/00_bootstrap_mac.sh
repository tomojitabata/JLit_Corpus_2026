#!/bin/bash
# =============================================================================
# 00_bootstrap_mac.sh — macOS のセットアップ（共用 iMac／自分の Mac 共通）
# -----------------------------------------------------------------------------
# sudo を一度も使わない。Homebrew も使わない（導入自体に admin が要るため）。
# JDK は Temurin の tar.gz を展開するだけで，インストーラを走らせない。
# したがって共用機でも自分の機械でも同じ手順で通る。
#
# 使い方
#   bash scripts/00_bootstrap_mac.sh             # DH Lab 共用 iMac
#   bash scripts/00_bootstrap_mac.sh --personal  # 自分の Mac
#   bash scripts/00_bootstrap_mac.sh --check     # 何もせず現状だけ表示
#   bash scripts/00_bootstrap_mac.sh --with-bungo # 検算用の近代文語辞書も入れる
#
# 二つのモードの違いは**重い共有物をどこに置くか**だけである。
#
#   共用 iMac (--なし)      /Users/Shared/jlit
#       macOS の /Users/Shared は admin 権限なしに全ユーザが読み書きできる。
#       DH Lab の iMac は XCreds 認証でホームがマシンをまたがないので，
#       JDK・MALLET・UniDic・取得済みテクストをここに置いておくと，
#       **同じマシンなら次回も，別のユーザでも**使い回せる。
#
#   自分の Mac (--personal)  ~/.jlit
#       共有する相手がいないので自分のホームに置く。ホームは消えないから，
#       一度入れれば以後は何もしなくてよい。
#
# どちらでも仮想環境は リポジトリ/.venv に作る（軽いので作り直せる）。
# 何度実行してもよい（冪等）。入っているものは飛ばす。
# =============================================================================
set -uo pipefail

# CPU に応じて JDK を選ぶ。Apple Silicon は aarch64，Intel Mac は x64。
# 持ち込みの MacBook には Intel 機も残っているので決め打ちにしない。
case "$(uname -m)" in
  arm64|aarch64) JDK_ARCH="aarch64" ;;
  x86_64)        JDK_ARCH="x64" ;;
  *)             JDK_ARCH="" ;;
esac
# ファイル名を固定すると版が上がったとたんに 404 になるので，
# Adoptium の API に最新の GA 版を返させる。
JDK_URL="https://api.adoptium.net/v3/binary/latest/21/ga/mac/${JDK_ARCH}/jdk/hotspot/normal/eclipse"
# 旧 mimno.github.io/Mallet/dist/ は 404。GitHub Releases の配布物を使う。
MALLET_URL="https://github.com/mimno/Mallet/releases/download/v202108/Mallet-202108-bin.tar.gz"

# -----------------------------------------------------------------------------
# 辞書。2026-09-22 の比較実験（4 辞書・111 点）で本番を決めた。
# 経緯と数値は docs/dictionary_comparison.md §10。
#
#   本番   unidic-novel v202512（近現代口語小説UniDic）未知語率 0.17%
#   検算用 unidic-kindai-bungo v202512（近代文語）A_文語体の助動詞率の確認用
#
# 現代書き言葉（cwj）はこのプロジェクトでは**使わない**。同じ 111 点で
# 未知語率が novel の 3.9 倍（0.66%）であり，平均語長も短い。
# NINJAL の配布は日付版（v202512）に移っている。3.1.x を指す URL を
# 残しておくと，版が動いたとたんに 404 になる。
# -----------------------------------------------------------------------------
UNIDIC_DIR_NAME="unidic-novel-v202512"
UNIDIC_URL="https://clrd.ninjal.ac.jp/unidic_archive/2512/unidic-novel-v202512.zip"
BUNGO_DIR_NAME="unidic-kindai-bungo-v202512"
BUNGO_URL="https://clrd.ninjal.ac.jp/unidic_archive/2512/unidic-kindai-bungo-v202512.zip"

MODE="run"
KIND="lab"                     # lab = 共用 iMac ／ personal = 自分の Mac
SHARED="/Users/Shared/jlit"
WITH_BUNGO="no"                # 検算用の近代文語UniDic も入れるか
for a in "$@"; do
  case "$a" in
    --personal|--home) KIND="personal"; SHARED="$HOME/.jlit" ;;
    --check) MODE="check" ;;
    --with-bungo) WITH_BUNGO="yes" ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "不明な引数: $a"; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVFILE="$SHARED/env.sh"

say()  { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf '  [ok  ] %s\n' "$1"; }
skip() { printf '  [have] %s\n' "$1"; }
warn() { printf '  [warn] %s\n' "$1"; }
die()  { printf '  [ERR ] %s\n' "$1"; exit 1; }

# -----------------------------------------------------------------------------
say "0. 環境の確認"
printf '  マシン名        %s\n' "$(scutil --get ComputerName 2>/dev/null || hostname)"
printf '  ホスト名        %s\n' "$(hostname -s)"
# set -u で走らせているので，USER が無い環境（cron・一部の CI）で
# ここが未定義変数エラーになる。id にも聞けるようにしておく。
printf '  ユーザ          %s\n' "${USER:-$(id -un)}"
printf '  ホーム          %s\n' "$HOME"
printf '  macOS           %s (%s)\n' "$(sw_vers -productVersion)" "$(uname -m)"
printf '  空き容量        %s\n' "$(df -h / | awk 'NR==2{print $4}')"
printf '  共有ディレクトリ %s\n' "$SHARED"
printf '  想定            %s\n' \
  "$([ "$KIND" = lab ] && echo 'DH Lab 共用 iMac' || echo '自分の Mac')"

if [ "$KIND" = lab ]; then
cat <<'NOTE'

  注意：DH Lab の iMac はホームが**そのマシンにしか残らない**（XCreds 認証）。
  別の iMac にログインすると，仮想環境も成果物も無い状態から始まる。
    * 重い共有物（JDK・MALLET・UniDic・取得済みテクスト）はマシンごとに
      /Users/Shared/jlit に置く。同じマシンなら次回も，別の人でも使える
    * 自分の成果物は Git で持ち運ぶ。ホームに置いたままにしない
    * どの機体を使ったかを控えておくこと（上の「マシン名」）
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
    && warn "旧 cwj が $SHARED/unidic に残っている（本番ではない。消してよい）"
  [ -d "$ROOT/.venv" ]     && skip "仮想環境 $ROOT/.venv"     || warn "仮想環境なし"
  [ -f "$ENVFILE" ]        && skip "env.sh   $ENVFILE"        || warn "env.sh なし"
  exit 0
fi

mkdir -p "$SHARED" 2>/dev/null || die "$SHARED を作れない。--personal を付けて実行すること"
[ -w "$SHARED" ] || die "$SHARED に書けない。--personal を付けて実行すること"

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
say "2. 仮想環境とパッケージ"
cd "$ROOT"
if [ -d .venv ]; then
  skip ".venv は既にある"
else
  uv venv --python 3.12 .venv || die "仮想環境を作れない"
  ok ".venv を作った（Python 3.12）"
fi
uv pip install --python .venv/bin/python -r requirements.txt \
  || die "パッケージの導入に失敗。requirements.txt を確認すること"
uv pip install --python .venv/bin/python ipykernel >/dev/null 2>&1
.venv/bin/python -m ipykernel install --user --name jlit \
  --display-name "Python (JLit)" >/dev/null 2>&1 \
  && ok "Jupyter カーネル 'Python (JLit)' を登録した"

# -----------------------------------------------------------------------------
say "3. JDK（MALLET が Java を要る。admin 権限は使わない）"
if [ -d "$SHARED/jdk/Contents/Home" ]; then
  skip "JDK は既にある"
else
  tmp="$(mktemp -d)"
  echo "  Temurin 21 (${JDK_ARCH}) を取得中（約 190 MB）…"
  if curl -L --fail -o "$tmp/jdk.tar.gz" "$JDK_URL"; then
    mkdir -p "$SHARED/jdk"
    tar xzf "$tmp/jdk.tar.gz" -C "$tmp"
    src="$(find "$tmp" -maxdepth 2 -name 'Contents' -type d | head -1)"
    [ -n "$src" ] && cp -R "$(dirname "$src")"/* "$SHARED/jdk/" && ok "JDK を展開した"
  else
    warn "JDK を取得できなかった。Step 8 の MALLET が使えない。"
    warn "  代替：uv pip install tomotopy（Java 不要の LDA）を使い，レポートに明記する"
  fi
  rm -rf "$tmp"
fi
[ -d "$SHARED/jdk/Contents/Home" ] && export JAVA_HOME="$SHARED/jdk/Contents/Home"

# -----------------------------------------------------------------------------
say "4. MALLET"
if [ -x "$SHARED/mallet/bin/mallet" ]; then
  skip "MALLET は既にある"
else
  tmp="$(mktemp -d)"
  if curl -L --fail -o "$tmp/mallet.tgz" "$MALLET_URL"; then
    tar xzf "$tmp/mallet.tgz" -C "$tmp"
    src="$(find "$tmp" -maxdepth 1 -type d -name 'Mallet-*' | head -1)"
    [ -n "$src" ] && mv "$src" "$SHARED/mallet" && ok "MALLET を展開した"
    # 既定のヒープ 1 GB では本コーパス（約 600 万語）で足りない。
    # 搭載メモリの 1/4 を目安にする（16 GB なら 4 GB）。
    if [ -f "$SHARED/mallet/bin/mallet" ]; then
      ram_gb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 8589934592) / 1073741824 ))
      heap=$(( ram_gb / 4 )); [ "$heap" -lt 2 ] && heap=2
      /usr/bin/sed -i '' -E "s/^MEMORY=.*$/MEMORY=\"\\\${MALLET_MEMORY:-${heap}g}\"/" "$SHARED/mallet/bin/mallet" \
        && ok "MALLET のヒープを ${heap} GB にした（搭載 ${ram_gb} GB の 1/4。既定 1 GB では足りない）"
      chmod +x "$SHARED/mallet/bin/mallet"
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
# いるように見えて，解析を始めた段階で「./dicrc が無い」と落ちる。
# そこで /usr/bin/unzip を明示して使い，展開後に必ず検査する。
fetch_dict() {
  local name="$1" url="$2" label="$3" tmp src
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
      mkdir -p "$SHARED/$name"
      cp -R "$(dirname "$src")"/* "$SHARED/$name/"
      ok "$label を $SHARED/$name に展開した（このマシンの全員が使える）"
      rm -rf "$tmp"
      return 0
    fi
    warn "$label の中身が想定と違う（sys.dic が見つからない）"
  else
    warn "$label を取得できなかった（${url}）"
  fi
  rm -rf "$tmp"
  return 1
}

say "5. UniDic（共有辞書。本番は ${UNIDIC_DIR_NAME}・約 1 GB）"
echo "  本番の辞書は 2026-09-22 の比較実験で決めた"
echo "  （近現代口語小説UniDic。未知語率 0.17%。docs/dictionary_comparison.md §10）"
if ! fetch_dict "$UNIDIC_DIR_NAME" "$UNIDIC_URL" "近現代口語小説UniDic v202512"; then
  warn "本番の辞書が入らなかった。unidic-lite で代替する"
  warn "**代替の辞書で出した数値は本番の結果と比べられない。報告に明記すること**"
  uv pip install --python .venv/bin/python unidic-lite >/dev/null 2>&1
fi
if [ "$WITH_BUNGO" = yes ]; then
  fetch_dict "$BUNGO_DIR_NAME" "$BUNGO_URL" "近代文語UniDic v202512（検算用）" || true
else
  echo "  検算用の近代文語UniDic は入れない（--with-bungo で入る）"
fi
# 展開できたかを実際に読み込んで確かめる
if [ -x "$ROOT/.venv/bin/python" ]; then
  for d in "$SHARED/$UNIDIC_DIR_NAME" "$SHARED/$BUNGO_DIR_NAME"; do
    [ -d "$d" ] && "$ROOT/.venv/bin/python" "$ROOT/scripts/check_unidic_dir.py" "$d" \
      | tail -5
  done
fi

# -----------------------------------------------------------------------------
say "6. 環境変数のファイルを書く"
cat > "$ENVFILE" <<EOF
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
mkdir -p "$SHARED/aozora-cache"
chmod -R a+rX "$SHARED" 2>/dev/null
ok "$ENVFILE を書いた"

# Jupyter を env.sh 抜きで起動しても辞書・Java・MALLET が見えるように，
# 同じ環境変数を 'Python (JLit)' カーネルの kernel.json に書き込む。
"$ROOT/.venv/bin/python" - "$ENVFILE" "$ROOT" <<'PY' \
  && ok "カーネル 'Python (JLit)' に環境変数を持たせた（env.sh を読まずに Jupyter を起動してもよい）" \
  || warn "カーネルに環境変数を書き込めなかった。Jupyter の前に env.sh を source すること"
import json, os, subprocess, sys
from jupyter_client.kernelspec import KernelSpecManager
envfile, root = sys.argv[1], sys.argv[2]
out = subprocess.run(['/bin/bash', '-c', f'. "{envfile}" >/dev/null 2>&1; env -0'],
                     capture_output=True, text=True, check=True).stdout
env = {}
for item in out.split('\0'):
    k, _, v = item.partition('=')
    if k.startswith('JLIT_') or k in ('JAVA_HOME', 'MALLET', 'PATH'):
        env[k] = v
env['PATH'] = os.path.join(root, '.venv', 'bin') + os.pathsep + env.get('PATH', '')
f = os.path.join(KernelSpecManager().get_kernel_spec('jlit').resource_dir, 'kernel.json')
spec = json.load(open(f, encoding='utf-8'))
spec['env'] = env
json.dump(spec, open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
PY

# -----------------------------------------------------------------------------
say "7. 確認"
# shellcheck disable=SC1090
. "$ENVFILE"
"$ROOT/.venv/bin/python" "$ROOT/scripts/00_env_check.py"

cat <<EOF

--------------------------------------------------------------------------
次からは，作業の前にこの2行を実行する。

    source $ENVFILE
    source $ROOT/.venv/bin/activate

毎回打つのが面倒なら ~/.zshrc の末尾に次を足してもよい（このマシンだけ）。

    [ -f $ENVFILE ] && . $ENVFILE

$([ "$KIND" = lab ] && cat <<'L'
**別の iMac に移ったら，このスクリプトをもう一度実行すること。**
ホームはマシンをまたがないので，何も無い状態から始まる。
成果物は Git で持ち運ぶこと。
L
)
--------------------------------------------------------------------------
EOF

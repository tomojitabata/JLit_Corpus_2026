#!/usr/bin/env bash
# publish_site.sh — 組み立てた Web ページ（site_build/）を sftp でサーバに置く
# ============================================================================
# 使い方（リポジトリの最上位で）:
#   python3 scripts/20_build_site.py                    # まず組み立てる
#   bash scripts/publish_site.sh <ユーザー名@サーバ名> <公開ディレクトリ>
# 例:
#   bash scripts/publish_site.sh tabata@www.example.ac.jp public_html
#
# 置くもの: site_build/ の jlit/・teaching/・assets/ の3つだけ。
# ⚠ 公開ディレクトリのほかのファイル（トップページなど）には触れない。
# ⚠ sftp はサーバの側の古いファイルを消さない。ファイル名は毎回同じなので
#   上書きされるが，不要になったファイルは手で消すこと。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE="$ROOT/site_build"
if [ $# -ne 2 ]; then
  sed -n '2,13p' "$0"; exit 2
fi
DEST="$1"; DIR="${2%/}"
for d in jlit teaching assets; do
  [ -d "$SITE/$d" ] || { echo "[FATAL] $SITE/$d が無い。先に python3 scripts/20_build_site.py を実行すること。" >&2; exit 1; }
done
# Web サーバが読めるように：ファイルは 644，ディレクトリは 755
find "$SITE" -type d -exec chmod 755 {} +
find "$SITE" -type f -exec chmod 644 {} +
echo "[sftp] $SITE → $DEST:$DIR/"
sftp -b - "$DEST" <<EOF
-mkdir $DIR/jlit
-mkdir $DIR/teaching
-mkdir $DIR/assets
put -rp $SITE/jlit $DIR/
put -rp $SITE/teaching $DIR/
put -rp $SITE/assets $DIR/
EOF
echo "[ok  ] 置いた。ブラウザで jlit/ と teaching/dh-text-analytics/ を開いて確かめること。"

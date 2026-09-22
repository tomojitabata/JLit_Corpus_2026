# 実行記録

再構築のたびに，次をこのフォルダに日付つきで残すこと。

1. config/pipeline.yaml のコピー
2. data/aozora/fetch_log.csv
3. data/xml/conversion_report.csv
4. data/plain/normalise_report.csv
5. data/tokens/tokenise_report.csv と unknown_words.csv
6. 99_validate.py の出力

## validation_v1.csv

2026-09-20 に v1 コーパス（64ファイル）を検査した結果。
FATAL 16 件 / WARN 18 件。再構築の対象である。

# Openverse音素材検索（AIによるショート動画制作向け）

2026-10-17追加。JIZURA本体とは独立した補助ツールです。ブラウザ版やAEパネルの出力方式は変更しません。

## できること・現時点でできないこと

- Openverse公式APIで、効果音（sound_effect）・BGM（music）を検索。匿名利用は無料でAPIキー不要。ただし回数制限があります。
- 商用制作向けに、ライセンスを CC0 / CC BY に限定して検索。非商用限定（NC）を除外します。
- 候補をJSONに記録。選んだ1件だけ、明示操作でダウンロードし、作品名・作者・ライセンス・出典を隣に保存します。
- 現時点では音の選択／ダウンロードまで。JIZURAのMP4へ複数音源を自動配置・ミックスする機能や、投稿自動化は追加していません。
- Openverseの表示は権利の保証ではありません。公開・収益化の前に、元配布ページ、ライセンス、作者表記と実際の権利関係を確認してください。CC BYのときは表示条件を守る必要があります。

## 使い方（Python標準ライブラリのみ）

リポジトリ直下から実行します。

~~~bash
# 1. 効果音候補を検索する（ダウンロードなし）
python tools/openverse_audio.py search --query "keyboard typing" --category sound_effect --limit 5 --manifest output/openverse_candidates.json

# BGM候補の検索
python tools/openverse_audio.py search --query "soft piano ambient" --category music --limit 5 --manifest output/openverse_music.json

# 2. JSONの情報で出典を確認してから、候補の1番を取得する
python tools/openverse_audio.py download --manifest output/openverse_candidates.json --number 1 --out-dir output/audio
~~~

ダウンロードすると音声ファイルと、同じIDの「.json」「.attribution.txt」が保存されます。音源そのものをGitにコミットしない運用を推奨します。

- JIZURAの文字PVでBGMとして使いたい場合は、曲の読み込み欄へ音声ファイルを手動で指定します。
- ショート動画に複数の効果音を置く場合は、JIZURAの映像書き出し後、動画編集工程（例：FFmpegや動画編集ソフト）で合成します。音量・タイミング・完成品質は別途確認します。
- Codex / Claude CodeはこのCLIを呼び出して候補の検索・選定とライセンスメモ作成を補助できます。ただし、自動公開や、権利未確認の音声の本番組み込みはしないでください。

## 安全設定

- 匿名APIのレート制限を超えたときは終了します。短時間の無制限リトライはしません。
- 通信はHTTPSのみ。リクエスト時間とファイルサイズ（25MiB）に制限があります。
- CC BY は商用利用可能でも作者表示が必要です。CC0でも元ページの権利状態を確認します。BY-SA等はこの補助ツールでは現時点で対象外です。
- このツールは検索結果を機械的に正しいと判断しません。ライセンスの最終確認は制作・公開担当者が行います。

公式仕様:
- Openverse API: https://api.openverse.org/v1/
- Openverse: https://openverse.org/

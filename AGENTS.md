# PicoRuby ESP32 最適化エージェント運用

## 目的

このプロジェクトの目的は、自前の ESP32 上で PicoRuby/FemtoRuby を shell/development 用途として動かし、cache / RAM / ROM / CPU 実行速度を実測ベースで最適化することです。

最適化は推測だけで進めず、`tools/ai_debug_cli.py` と実機 probe で得た情報を LLM / sub-agent に渡し、実験結果の信頼度を見て採用可否を決めます。

## 最適化ループ

このループの弱点は、AI が「実験」「採用」「検索」「破棄」の境界を曖昧にしやすい点です。以下を守り、1 回の実験で 1 つの仮説だけを扱います。

1. 現状の基準値を取得する
   - 静的診断: `tools/ai_debug_cli.py --format json --output /tmp/before.json`
   - 実機計測: `tools/ai_debug_cli.py --run-probe --output /tmp/before_runtime.json`
   - size: `idf.py -C tools/ruby_debug_ide/esp_project size`
2. sub-agent に 1 テーマだけ実験させる
   - 例: Wi-Fi 無効化、mbedTLS 削減、flash QIO/80MHz、Ruby heap 配置、不要 gem 削減
   - 実験は小さくし、build / flash / runtime probe まで行う
3. 結果を信頼度で判定する
   - high: build 成功、実機 boot 成功、probe 成功、before/after が改善、副作用が説明できる
   - medium: 改善はあるが probe 回数不足、または副作用の範囲が未確定
   - low: build だけ成功、実機未確認、または差分理由が不明
4. 採用する場合
   - main worktree に取り込む
   - `UPDATE.md` に `- [ ] 実験名 (git hash)` 形式で登録する
   - 根拠として before/after の JSON、size、runtime 指標を残す
5. 採用しない場合
   - 実験 branch/worktree の git 差分を破棄する
   - 失敗理由を記録し、条件を変えて sub-agent に再依頼する

## 厳格ルール

- 実験は必ず専用 branch/worktree で行う。main worktree に直接混ぜない。
- 1 実験 = 1 仮説。複数変更を同時に入れない。
- 採用判定前に必ず `git diff` を確認し、意図しないファイル・生成物・秘密情報がないか見る。
- submodule 変更は root repo 変更とは別扱いにする。PicoRuby submodule 内の差分は、採用理由が明確な場合だけ残す。
- `before` と `after` は同じ条件で取る。probe 回数、USB port、sdkconfig、flash 済み firmware を揃える。
- 数値改善は最低 2 回以上再測定する。1 回だけの改善は high にしない。
- size 改善だけで runtime 悪化する変更、runtime 改善だけで安定性を落とす変更は medium 以下にする。
- 不採用時は、破棄前に `UPDATE.md` の「不採用・再実験」に理由を書く。
- `rm -rf` など広範囲削除で破棄しない。原則は `git status` と `git diff` を確認してから git で戻す。
- build 成功だけでは採用しない。ESP32 実機 boot と `--run-probe` 成功を必須にする。

## 実験 branch の退避運用

採用前でも、後で比較・再利用できる実験は退避用 branch に commit / push してよいです。ただし、main worktree には混ぜず、採用判定は別途行います。

- root repo と submodule は別 branch / 別 commit / 別 push で扱う。
- submodule を変更した場合は、先に submodule 側を commit / push し、その commit hash を root repo 側で参照する。
- 退避 branch 名は実験内容が分かる名前にする。
  - 例: root repo `experiment/ruby-heap-80k`
  - 例: submodule `experiment/ai-debug-probe-heap80`
- commit / push 前に必ず `git status`、`git diff --cached`、必要なら submodule 内の `git status` / `git diff --cached` を確認し、意図しない生成物や秘密情報がないか見る。
- 退避済み実験でも、high confidence でなければ採用済みとは扱わない。

## Ruby heap 実験メモ

Ruby heap sizing は 1 回で決めず、`100KB` baseline、`90KB`、`80KB` のように段階的に比較します。

- `80KB` は boot / shell / probe が通っても、文字列 allocation 悪化や長時間利用時の heap 不足リスクがあるため採用保留になりやすい。
- `90KB` は `100KB` と `80KB` の中間候補として、同じ条件で build / flash / boot / runtime probe を測る。
- 判定時は high / medium / low に加えて、メリット・デメリットを表で整理する。

## Web 検索ルール

AI が迷わないように、Web 検索は以下の条件に当てはまる時だけ行います。

### 検索する

- ESP-IDF / ESP32 / mbedTLS / lwIP / FreeRTOS の設定意味が不明な時
- 実機で panic、reset、heap corruption、watchdog、flash boot failure が出た時
- sdkconfig の項目名は分かるが、効果・副作用・推奨値が分からない時
- PicoRuby / mruby/c / mruby の既知 issue や仕様確認が必要な時
- 自分の知識と実機結果が矛盾した時
- 「面白い発見」を最適化ルールとして残したい時

### 検索しない

- repo 内のコードを読めば分かること
- build error の直接原因がローカル diff で明らかな時
- 一般的な C/Ruby/Python 文法
- すでに `UPDATE.md` に根拠 URL がある同じ話題
- 検索しても採用判断に使わない雑学

### 検索結果の保存形式

検索したら必ず `UPDATE.md` の「調査メモ」に以下を残します。

```md
- YYYY-MM-DD: topic
  - trigger: なぜ検索したか
  - source: URL
  - finding: 分かったこと
  - impact: 採用判断にどう影響するか
  - confidence: high/medium/low
```

検索結果を残さない Web 検索は禁止です。

## 採用判定チェックリスト

採用する前に全て確認します。

- [ ] 変更対象が 1 テーマに収まっている
- [ ] `git diff` を確認した
- [ ] build が成功した
- [ ] flash が成功した
- [ ] boot が成功した
- [ ] runtime probe が成功した
- [ ] before/after の比較がある
- [ ] 改善量と副作用を説明できる
- [ ] `UPDATE.md` に `- [ ] 実験名 (git hash)` を追加した
- [ ] submodule 差分の扱いを明記した

このループは良い方針ですが、何もしないと AI は「面白そうな検索」「まとめて最適化」「build だけで採用」に流れます。上の制約で、実験を小さく・再現可能・破棄可能に保ちます。

## AI Debug CLI 使い方

## 前提

- ESP32 を USB シリアルで接続しておく
- 必要なら ESP-IDF 環境を有効化する

```sh
. .esp-idf/export.sh
```

USB ポート例:

```sh
ls /dev/cu.usbserial-* /dev/tty.usbserial-*
```

## 静的診断を取る

JSON で保存:

```sh
tools/ai_debug_cli.py --format json --output /tmp/picoruby_ai_debug.json
```

人間向け表示:

```sh
tools/ai_debug_cli.py --format text
```

追加で `idf.py size` 系も実行する:

```sh
tools/ai_debug_cli.py --run-idf-size --format json --output /tmp/picoruby_ai_debug_size.json
```

収集される主な情報:

- host / Python / ESP-IDF toolchain
- USB serial device
- git branch / status / submodule
- `sdkconfig` の関連設定
- firmware artifact size
- `.a` archive size ranking
- map file hotspot
- PicoRuby CMake / build_config / gems / defines
- sdkconfig リスク診断

## 実機ランタイム計測

現在の firmware には `/bin/ai_probe` が入っているため、CLI から shell command として実行できます。

```sh
tools/ai_debug_cli.py \
  --run-probe \
  --port /dev/cu.usbserial-10 \
  --probe-timeout 60 \
  --output /tmp/picoruby_runtime_probe.json
```

`--port` を省略すると、USB らしい serial port を自動選択します。

```sh
tools/ai_debug_cli.py --run-probe --probe-timeout 60
```

取得される主な runtime 指標:

- `AI_BOOT`: boot stage timing
- `AI_MEM`: internal heap / PSRAM / largest block / minimum free heap
- `AI_CPU`: CPU frequency / FreeRTOS tick / task stack free
- `AI_PROBE`: Ruby operation overhead
  - empty loop
  - integer add
  - method call
  - small string allocation
  - small array allocation
  - `GC.start`

## firmware build / flash

Debug IDE project をビルド:

```sh
idf.py -C tools/ruby_debug_ide/esp_project build
```

USB 接続した ESP32 へ flash:

```sh
idf.py -C tools/ruby_debug_ide/esp_project -p /dev/cu.usbserial-10 flash
```

size summary:

```sh
idf.py -C tools/ruby_debug_ide/esp_project size
```

## runtime probe script を表示する

shell/IRB へ手動投入したい場合は Ruby probe script を出力できます。

```sh
tools/ai_debug_cli.py --emit-runtime-probe > /tmp/ai_runtime_probe.rb
```

通常は `/bin/ai_probe` 経由の `--run-probe` を使ってください。IRB へ長い script を流す方法は ESP32 側のメモリや line editor の影響を受けやすく、安定しません。

## report 比較

最適化前後の JSON report を比較:

```sh
tools/ai_debug_cli.py \
  --compare /tmp/before.json /tmp/after.json \
  --output /tmp/picoruby_compare.json
```

## LLM に渡すと便利なファイル

最適化相談時は、最低限この 2 つを渡します。

```sh
/tmp/picoruby_ai_debug.json
/tmp/picoruby_runtime_probe.json
```

必要に応じて以下も追加します。

```sh
tools/ruby_debug_ide/esp_project/build/picoruby_esp32_debug_ide.map
tools/ruby_debug_ide/esp_project/sdkconfig
build_config/xtensa-esp-femtoruby.rb
CMakeLists.txt
```

## 注意点

- `/bin/ai_probe` を追加・変更した後は clean rebuild が必要になる場合があります。
- 初回 boot 時は system executable の書き込みや filesystem 初期化で時間が伸びます。
- shell 起動直後に probe を送ると取りこぼすことがあるため、`--run-probe` は shell prompt 表示後に実行するのが安定します。
- 現在の shell/development profile は Wi-Fi / mbedTLS / WPA supplicant が大きな ROM/RAM コストになりやすいです。

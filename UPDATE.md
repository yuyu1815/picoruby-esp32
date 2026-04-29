# Optimization Update Log

採用候補・採用済みの最適化実験をここで管理します。

## 採用候補

- [ ] 例: Wi-Fi buffer 削減 (git hash)

## 採用済み

- [ ] 例: `/bin/ai_probe` runtime 計測 command 追加 (git hash)
- [x] Wi-Fi binding-off shell profile (`ac2870a`, submodule `8a5172b5`)
  - build: `idf.py -C tools/ruby_debug_ide/esp_project -DPICORUBY_DISABLE_ESP_WIFI=ON build`
  - flash: `/dev/cu.usbserial-10`
  - boot log: `/tmp/adopt_wifi_boot.log`
  - runtime probe: `/tmp/adopt_wifi_runtime_3.json`, `/tmp/adopt_wifi_runtime_4.json`
  - size: total image `1,165,697 bytes`
  - effect: `esp_wifi` binding/link を shell profile から外し、free heap は約 `170KB` 台まで改善

## 不採用・再実験

- 例: 実験名
  - 理由:
  - 次の条件:

## 調査メモ

未知の挙動や面白い発見があった場合は、Web 検索で一次情報を確認してここに残します。

- YYYY-MM-DD: topic
  - trigger: なぜ検索したか
  - source: URL
  - finding: 分かったこと
  - impact: 採用判断にどう影響するか
  - confidence: high/medium/low

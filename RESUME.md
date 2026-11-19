# RESUME（続きの作業メモ）

## いまの状態（2026-11-19）

- render_parity_cpp（7461c80）を複製し、その後の 502 修正 3 件（C++ のスレッドごとの DB 接続、
  Python のスレッドプール、接続プールの拡大）も入れた。
- 順番待ちの MVP ができた（Python 版と C++ 版の両方）。
  - API: `python/testsv_api_junban.py` / `cpp/main/testsv_api_junban.h`（11 本）
  - モデル: `python/model_junban.py` / `cpp/models/junban.ddl` → `model_junban.h`（ddl2cpp で生成）
  - ページ: `take.html`（受付）、`ticket.html`（待ち状況）、`staff.html`（店員）、`index.html` から店員画面へリンク
- ローカル（SQLite）で確かめたこと
  - `tools/junban_check.py` の 54 手順＋同時受付 12 件の応答が Python と C++ で完全一致。
  - 3 つのページを Chrome ヘッドレスで描画し、表示を確認（音・振動とボタン操作は未確認）。
- **Render にはまだ出していない。**

## 次にやること

1. **Render に Blueprint で 2 サービスを作る**（ブラウザ操作が要るのでユーザー）。
   New + → Blueprint → yomei-o/junban_cpp。サービス名は `junban-python` / `junban-cpp`。
2. **TiDB は render_parity_cpp と別の DB にする**のがよい（account・tenant の表が同じ名前なので、
   同じ `test` DB だとログインやテナントが共有される）。TiDB で `CREATE DATABASE junban;` を 1 回実行し、
   Render の env `DB_NAME=junban`。他の env は render_parity と同じ（password リポの README 参照）。
3. 実機（スマホ）で受付 → 呼出 → 音と振動が鳴るかを確かめる。
4. Render を東京に近づけたいならシンガポール（`region: singapore`）で作る（作成後は変えられない）。

## あとでやるかもしれないこと（MVP の外）

- 店内の表示板（呼出中の番号を大きく出す画面）
- 同じ端末からの連続受付の制限
- 呼んでから◯分で自動的に不在にする
- 席の並び替え

## ハマりどころ（render_parity_cpp から引き継ぎ）

- ポートは env `POST`（PORT ではない）。Render の PORT を橋渡ししている。
- C++ は TiDB のときスレッドごとに接続、SQLite のときは全体 mutex。
- ページの定期更新は前の回が終わってから（`junban.js` の `poll`）。重ねると遠い DB で詰まって 502。
- `cpp/html` と `python/html` は同じ中身。片方を直したらもう片方へコピーする。

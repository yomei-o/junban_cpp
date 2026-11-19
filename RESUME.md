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
- **Render に出した（2026-11-19）**: junban-cpp / junban-python（オレゴン）、TiDB は junban 用の別クラスタの
  DB `junban`。環境変数は Render API で設定（API キーは ~/.claude/keys/render_api_key、控えは password リポ）。
- 本番で tools/junban_check.py（mysql）が両実装一致。最初は 12 人同時受付で busy が出た →
  採番を INSERT … SELECT MAX+1 の 1 文＋待って最大 10 回振り直しにして解消。

## 次にやること

1. 実機で通す: パスキーでログイン（最初の人が全体の管理者）→ お店を作る → 自分で参加（最初の人はお店の管理者）
   → staff.html で席の種類を作る → スマホで受付 QR → 呼出 → 音と振動が鳴るか。
2. Render を東京に近づけたいならシンガポール（`region: singapore`）で作り直す（作成後は変えられない）。

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

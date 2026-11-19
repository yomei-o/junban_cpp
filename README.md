# junban_cpp — お店の順番待ち（整理券）

お店の順番待ちをブラウザだけで回す Web アプリ。**同じ API を FastAPI(Python) と cpp-httplib(C++) の
両方**で実装し、**Render.com**（無料枠）＋ **TiDB Cloud** で動かす。
土台は [yomei-o/render_parity_cpp](https://github.com/yomei-o/render_parity_cpp)（パスキーのログインとテナント）。
Python/C++ の**機能パリティ**を保つのが方針（速度は別）。

## 動いているサーバ（Render 無料枠＋TiDB Cloud の DB `junban`）

| 実装 | ログイン・お店の管理 |
|---|---|
| C++（cpp-httplib, Docker） | https://junban-cpp.onrender.com/html/index.html |
| Python（FastAPI） | https://junban-python.onrender.com/html/index.html |

2 つは同じ TiDB を使うので、どちらから操作しても同じお店・同じ整理券が見える。
無料枠なので 15 分アクセスが無いと止まり、次の最初のアクセスは起動待ちになる（C++ が速い）。

## 使い方

| 誰が | ページ | やること |
|---|---|---|
| お店の人 | `/html/index.html` | パスキーでログイン。全体の管理者がお店（テナント）を作り、URL を店員に渡す。店員はその URL から参加申請 |
| 店員 | `/html/staff.html?tenant=<guid>` | 受付の開始・停止、席の種類ごとに「次を呼ぶ」、案内済み・不在・待ちに戻す、受付の QR コード |
| お客さん | `/html/take.html?tenant=<guid>` | 店頭の QR を読む → 席の種類と人数を選んで並ぶ（ログイン不要） |
| お客さん | `/html/ticket.html?t=<token>` | 自分の番号、「あと◯組」、呼出中の番号。開いたままにすると呼ばれたときに音と振動 |

- 番号は店ごと・営業日ごと（日本時間 4 時で切り替え）に 1 から、席の種類をまたいだ通し番号。
- 同時に受付しても番号は重ならない（`(tenant_id, biz_day, num)` の一意制約＋振り直し）。
- 席の種類が 1 つもない店は全員 1 列。
- 呼ばれたことの通知は、待ち画面が数秒おきに問い合わせる方式（Web Push・LINE・SMS は使わない）。

## API（`/api/junban/v0/`、すべて POST・JSON）

| API | 誰が | 引数 |
|---|---|---|
| `store` | 誰でも | `tenant` |
| `take` | 誰でも | `tenant, seat, people` → `token` |
| `ticket` / `cancel` | 誰でも（token を持つ人） | `token` |
| `queue` / `call` / `set_status` / `accepting` | そのお店の承認済みメンバー | `tenant` ＋ `seat` / `number, status` / `on` |
| `seat_add` / `seat_rename` / `seat_delete` | そのお店の管理者 | `tenant` ＋ `name` / `seat` |

詳しくは `python/testsv_api_junban.py` と `cpp/main/testsv_api_junban.h` の先頭。ログイン・テナントの API
（`/api/passkey/v0/` `/api/account/v0/` `/api/tenant/v0/`）は render_parity_cpp のまま。

### 両実装が同じ応答を返すかの検査

`tools/junban_check.py` は SQLite で動かしたサーバに同じ手順（54 手順＋同時受付 12 件）を流し、
応答を正規化して出す。Python 版と C++ 版の出力を `diff` して一致を確かめる。
DB に `mysql` を渡すと本番の TiDB に検査用の店を作って流し、最後に消す。3 つ目の URL を渡すと
2 つのサーバに同時に受付して番号が重ならないかも見る。
2026-11-19: SQLite・本番 TiDB とも一致、本番で 12 人同時（片方のサーバ／両方のサーバ）も全員受付・重複なし。

## 構成

```
render.yaml       Render Blueprint（junban-python と junban-cpp の2サービス）
cpp/              cpp-httplib 実装（httplib + 自作mbedtls + sqlpp11 + sqlite3 同梱）
  main/           lambda_server.cpp（エントリ）, API(testsv_api_*), httplib/json
  include/        mbedtls（mbedtlspp 経由でユニティ include）
  sqlite/ sqlpp11/ mysql_8_4/ postgre_17/ models/ gen/ date/
  html/           index.html（ログイン）, take/ticket/staff.html（順番待ち）, junban.js/css, qr.js/qr.wasm
                  （python/html と同じ中身。片方を直したらコピーする）
  Dockerfile      Render 用（既定 SQLite ビルド。TiDB/MySQL への切替はコメント参照）
python/           FastAPI 実装
  lambda_server.py  app（passkey/tenant/junban ルータを /api 配下、/html 配信、/ は redirect）
  database.py       既定 SQLite / env で MySQL(TiDB) / DATABASE_URL 優先
  requirements.txt
```

## 共通の約束

- **ポートは環境変数 `POST`**（PORT ではない。既定 8080）。Render は `PORT` を注入するので、
  Python は `uvicorn ... --port $PORT`、C++ は Docker CMD で `POST=$PORT` に橋渡しする。
- **DB は既定 SQLite**（`test_db.db`、テーブルは起動時に自動作成）。
  永続化は **TiDB Cloud**：env `RDS_PROXY_HOST / USER_NAME / PASSWORD / DB_NAME`（Python は `DATABASE_URL` も可）。
- CORS 全許可。`/html/index.html` は 200（Render のヘルスチェックに使用）。`/` は `/html/index.html` へ redirect。

## ローカルで動かす

**Python（FastAPI）**
```sh
cd python
python -m venv .venv && . .venv/Scripts/activate      # mac/Linux は .venv/bin/activate
pip install -r requirements.txt
POST=8080 python lambda_server.py                      # http://localhost:8080/html/index.html
```

**C++（cpp-httplib）— Windows ローカル**

既定は **SQLite（依存ゼロ）**。`cpp/sqlpp11_cust.sln` を VS2022 で Release/x64 ビルド →
`cpp/` ディレクトリ（`html/` がある場所）から起動する：
```sh
cd cpp
POST=8080 ./x64/Release/sqlpp11_cust.exe        # http://localhost:8080/html/index.html
# DB はカレントに test_db.db を自動作成。DLL 不要。
```

**C++ を TiDB/MySQL で動かす（Windows・任意）**
1. `USE_MYSQL` を定義してビルド（`database.h` 冒頭の `#define USE_MYSQL 1` を有効化、
   もしくは MSBuild を `CL=-DUSE_MYSQL`（`/D` は Git Bash がパス変換して壊すのでダッシュ）で実行）。
2. 実行時に `libmysql.dll` を dlopen する。**`sh cpp/setup_local_dll.sh` を一度実行**すると、
   ローカルの MySQL 8.4 インストール（既定 `C:\prog\mysql-8.4.4-winx64`、`MYSQL_DIR` で変更可）から
   `libmysql.dll`＋openssl DLL を `x64/Release`・`x64/Debug`（exe の隣）にコピーする。
   → 以後 **PATH 設定不要**で exe が DLL を隣から読む。これらの DLL は `.gitignore` 済み（ローカル専用・非コミット）。
3. 接続情報を env で渡して起動：
   ```sh
   export PATH="/c/prog/mysql-8.4.4-winx64/lib:/c/prog/mysql-8.4.4-winx64/bin:$PATH"
   RDS_PROXY_HOST=... TIDB_PORT=4000 USER_NAME=... PASSWORD=... DB_NAME=test \
   POST=8080 ./x64/Release/sqlpp11_cust.exe
   ```

**C++ — Linux/Docker**
```sh
cd cpp && docker build -t junban-cpp . && docker run -e PORT=8080 -p 8080:8080 \
  -e RDS_PROXY_HOST=... -e TIDB_PORT=4000 -e USER_NAME=... -e PASSWORD=... -e DB_NAME=test junban-cpp
```

## Render へのデプロイ

1. この repo を push（`render.yaml` はリポ直下）。
2. Render → **New + → Blueprint** → この repo を選択 → 2サービス作成。
   - python … ネイティブ Python（`pip install` → `uvicorn`）
   - cpp … `cpp/Dockerfile` をビルド
3. `https://<service>.onrender.com/html/index.html` で確認。

無料枠は 15 分無アクセスでスピンダウン・起動約1分・**ディスク揮発**（永続化は TiDB へ）。

## TiDB Cloud

- Serverless（MySQL 互換・**TLS 必須**・port 4000）。接続情報は **env で注入し、リポジトリに秘密を置かない**（`secrets/` は gitignore 済み）。
- Python: `PyMySQL`（TLS 引数が要る）。C++: `USE_MYSQL` ビルド＋`libmysqlclient`（`cpp/Dockerfile` 末尾コメント）。

## ライセンス / 出典

元リポジトリ（passkey_server_cpp）に準じる。cpp-httplib(MIT), nlohmann/json(MIT), SQLite(public domain),
FastAPI/Uvicorn/SQLAlchemy(MIT 等), mbedtls 由来コード(Apache-2.0)。

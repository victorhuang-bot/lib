# MovingMatch V20：交付與隔離驗證

實作對應 Issue #41、`docs/V20-empty-box-dispatch.md`（原規格 commit 92dca051）。開發分支 `feature/v20-empty-box-dispatch`，基準 main d107436。原工作目錄的 V19.4 未提交變更未帶入本分支。

## 實作

- 盤點分開保存三項數量、版本、時間、人員與備註；目前庫存及歷史流水獨立。前端總箱數計算，盤點寫入以版本檢查避免蓋過新交接。
- 補箱申請按有效未補足量計算缺箱，部分補足仍有效；有進行中調度不可直接取消申請。
- 多來源分別安排數量與收取日，需求館另定送達日。來源保留量不先扣庫存，交回確認才出庫；收到確認才入庫。所有寫入共用既有班表交易鎖，SQLite BEGIN IMMEDIATE／PostgreSQL advisory transaction lock。
- 建議日期沿用固定配送日、ADD／STOP 與全館休館。改線、停送、休館或計畫逾期在看板標記待重新安排；日期不會自動改寫或默認完成。總館調整須填原因，稽核保留調整前後計畫。
- 在途／倉庫暫存按已確認來源數量計算，異線、異日不會提前增加需求館庫存。來源可填差異量及原因；超收需同時符合庫存、其他調度保留量與需求剩餘額度。需求館收到量須與來源已交回量一致；與原計畫不同時必填原因，避免憑空增加或遺失箱數。
- 需求館可提前確認，保留原預定送達日。actual_date 與 confirmed_at 各自儲存，跨日補簽仍填原實際日。
- 原分館簽收可附加空箱確認，共用當次人員與簽名並原子提交；空箱失敗會回滾圖書簽收。沒有原配送時，首頁待交接區使用本站人員與手寫簽名確認，不建立假配送。
- 司機沒有新增頁面、按鈕、登入或空箱操作權限。既有 today 回應保留 note_final，新增衍生 note_display，原備註及四項數量不被調度覆寫。
- 管理者、總館、秘書室可讀調度看板與排程；分館只能讀寫自身。提醒針對未盤點或最新盤點超過 30 日的分館，記錄時間與操作人。

## Schema 與啟用

原有 26 張表不變；新增 9 張的逐表用途、約束與獨立結構契約見 [資料表核對](V20-schema-review.md)。應用啟動不執行 V20 migration。功能預設關閉：`MOVINGMATCH_V20_ENABLED=0`；啟用時會唯讀驗證 V20 表存在。

新建隔離 SQLite 測試庫：

```bash
python -m venv /tmp/movingmatch-v20-venv
/tmp/movingmatch-v20-venv/bin/pip install -r requirements-dev.txt
DATA_DIR=/tmp/movingmatch-v20-local APP_ENV=test DATABASE_URL= \
  /tmp/movingmatch-v20-venv/bin/python -m app.schema_init --sqlite /tmp/movingmatch-v20-local/app.db
```

既有隔離庫只加 V20 表、不跑舊 migration：

```bash
python -m app.empty_boxes_migrate --sqlite /tmp/movingmatch-v20-local/app.db
# 可重跑；只 CREATE TABLE / INDEX IF NOT EXISTS，單一交易。
```

本機 PostgreSQL 的獨立 migration 指令只接受 loopback，dbname 必須以 movingmatch_v20_test 開頭；忽略 DATABASE_URL，不接受 Neon host：

```bash
python -m app.empty_boxes_migrate --local-postgres 'host=127.0.0.1 dbname=movingmatch_v20_test user=tester'
```

遠端環境不受此 CLI 支援。將來若要正式升級，須另行審閱目標、備份、權限與部署批准；本次沒有對正式環境執行任何步驟。

## 自動化測試

```bash
python -B -m unittest discover -s tests -q
python -B tests/v20_postgres_runner.py --pg-bin /path/to/postgresql/bin
python -B tests/v195_postgres_runner.py --pg-bin /path/to/postgresql/bin
```

兩個 PostgreSQL runner 只啟動自行建立的暫存本機叢集，沒有 DSN 參數；關閉 TCP，核對 data_directory，逐案例建空庫與合成資料，結束即停機移除。原 V19.5 與配送／簽收測試案例未改。

瀏覽器測試（runner 每次自行建立、關閉預覽伺服器及合成資料）：

```bash
# 安裝測試用 Playwright；使用隔離瀏覽器設定，不讀個人登入狀態。
npm install --prefix /tmp/movingmatch-v20-browser playwright
PLAYWRIGHT_MODULE=file:///tmp/movingmatch-v20-browser/node_modules/playwright/index.mjs \
  python -B tests/v20_browser_runner.py
```

Chrome 預設 macOS 官方安裝路徑；其他平台可設定 CHROME_PATH。僅測固定 loopback 位址 127.0.0.1:58120。1440、768、390px 驗證操作、水平溢出、盤點總和、多來源／日期建議、原人員簽名確認空箱與 console errors；證據預設寫 /tmp/movingmatch-v20-browser-evidence。畫面數據全部為合成測試資料。

## 測試環境部署步驟（未執行）

1. 建立獨立測試服務與全新測試資料庫，不複用正式 DATABASE_URL、帳號、QR、PIN、簽名、Gmail 或任何正式秘密。
2. 使用本分支程式；正式 Render 設定保持不動。V20 flag 先關閉；測試環境依授權初始化獨立 schema。
3. 在測試環境執行 schema 契約檢查及 migration 重跑驗證，植入合成資料，再開啟 V20 flag。
4. 手機／桌機驗收正常簽收、稍後補簽、多來源、提前送達、停送與改線；核對實際日／確認時間、庫存及稽核。
5. 通過人工驗收後另行決定正式上線；本 PR 不合併、不部署正式 Render、不改正式 Neon。

## 回復方案

先停止 V20 新寫入／關閉 flag，讓原流程繼續使用原 26 張表；回復 V19.5 程式，保留 V20 九張表及稽核作為證據，不 DROP、不回寫舊配送或簽名。已確認交接代表實際物流，不能因程式回復而刪除流水；必要的庫存重盤由分館另建快照。沒有可直接對正式資料庫執行的破壞性 down migration。

## 尚待交付動作

GitHub CLI 在此環境尚未安裝；不使用自訂程式讀取或傳送 Token。認證完成後，使用官方 gh 流程推送本分支並建立 base=main 的 PR，不合併。執行結果見 `docs/V20-verification.md`。

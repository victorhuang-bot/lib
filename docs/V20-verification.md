# V20 驗證紀錄

本機分支 `feature/v20-empty-box-dispatch`，基準 `d107436`。尚未推送、尚未建立 PR，正式上線仍須人工驗收。所有資料庫均為自行建立的暫存庫，正式 Neon 連線數 0、正式 Render 部署數 0。

| 驗證 | 通過 | 失敗 | 錯誤 | 跳過 |
| --- | ---: | ---: | ---: | ---: |
| 完整 SQLite／離線套件 | 191 | 0 | 0 | 0 |
| V19.5 原案例，PostgreSQL 16.4 | 16 | 0 | 0 | 0 |
| V20，PostgreSQL 16.4 | 12 | 0 | 0 | 0 |

完整 SQLite 最後一輪 81.746 秒，V19.5 PostgreSQL 8.556 秒，最後 V20 PostgreSQL 5.772 秒。兩個 PostgreSQL runner 每案例均核對獨立結構契約及零筆業務基準，確認 Unix socket、關閉 TCP、自己的 data_directory；完成後停止並移除叢集。沒有使用既有測試庫。

命令（本次 Python 位於 `/tmp/movingmatch-v20-venv/bin/python`，PostgreSQL 位於 `/tmp/movingmatch-v20-pg/bin`）：

```bash
python -B -m unittest discover -s tests -v
python -B tests/v195_postgres_runner.py --pg-bin /tmp/movingmatch-v20-pg/bin
python -B tests/v20_postgres_runner.py --pg-bin /tmp/movingmatch-v20-pg/bin
```

原 26 張表與新增 9 張表逐表用途、約束、欄位契約來源見 [Schema Review](V20-schema-review.md)。原 V19.5、V19.4 配送與電子簽收測試案例未改；修改的是 PostgreSQL runner 的結構檢查、安全身分檢查及統計。

前幾輪實測曾找出 PostgreSQL 空參數型別推斷問題；修正為 CAST AS TEXT。結構檢查初版另曾將 BIGINT 誤判為文字，已修正映射。這些失敗均有修正後重跑，表中為最終結果。

## 介面驗證

Chrome／Playwright 使用新建本機合成三館資料與獨立瀏覽器設定，沒有讀取個人登入狀態。1440、768、390px 各驗證總館與分館畫面、無水平溢出、多來源調度表單與日期建議、盤點合計。390px 另操作本站既有人員選擇及簽名完成空箱交回，確認扣庫存一次，console errors 0。runner 結束即關閉預覽並清除暫存資料。

```bash
PLAYWRIGHT_MODULE=file:///tmp/movingmatch-v20-browser/node_modules/playwright/index.mjs \
  python -B tests/v20_browser_runner.py
```

[總館 1440](evidence/V20/office-1440.png)／[768](evidence/V20/office-768.png)／[390](evidence/V20/office-390.png)

[分館 1440](evidence/V20/branch-1440.png)／[768](evidence/V20/branch-768.png)／[390](evidence/V20/branch-390.png)／[交接完成](evidence/V20/branch-confirmed-390.png)

已逐張檢視最終七張截圖。沿用既有介面字體與顏色、加入空箱庫存及收取／送達資料，沒有另做視覺方向抽選（現有畫面的功能擴充）。改善盤點清單預設收合，避免長清單把待調度工作推到頁底。新增區塊的桌機／手機資料與操作一致；舊分館歷史表在桌機窄容器內仍沿用原版密度，未列為本次重設計。新增 CSS／JS 的技能 lint 為 0 FAIL、1 WARN；警示來自代表缺值的破折號，保留其資料語意。未宣稱完整既有頁面通過技能 inspector。

## 交付狀態與認證

實作、migration、回復與測試環境步驟見 [交付文件](V20-implementation.md)，PR 草稿內容見 [PR 說明](V20-pr.md)。GitHub Actions workflow 已加入，但尚未推送，遠端 CI 尚未執行。

本機找不到 GitHub CLI，因而無法執行 gh auth status；不以自訂程式或 git credential fill 取得憑證。請先安裝官方 GitHub CLI，於自己的終端機執行官方互動式登入：

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status
```

完成後即可透過 gh 既有登入推送本分支，建立 base=main、head=feature/v20-empty-box-dispatch 的 PR，不合併。未執行被禁止的 `/tmp/movingmatch-v20-github-auth.py` 後續授權動作，未自行讀取或傳送 GitHub Token。

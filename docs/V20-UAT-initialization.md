# V20 獨立 UAT 初始化

唯一允許的遠端目標：Neon `movingmatch-v20-uat`，project
`blue-paper-82274906`、branch `br-withered-cloud-b3jqn8nc`、直接端點
`ep-royal-night-b3ldf8au.c-4.ap-southeast-1.aws.neon.tech`、database
`movingmatch_v20_uat`、role `movingmatch_v20_uat_owner`。
這些識別由 Neon 控制平面核對，不能用正式專案或任意環境變數替換。

以安全秘密管理工具注入 `MOVINGMATCH_V20_UAT_DATABASE_URL`，不要在 shell
命令、終端輸出、CI、GitHub 或 PR 中貼上連線字串。一般 `DATABASE_URL`
不能選擇初始化目標；所有 `PG*` 變數都會被拒絕。僅接受直接端點，
固定使用 CA 驗證的 `verify-full` TLS，禁止自訂服務、hostaddr、options、passfile。

```sh
python -m app.uat_init --credentials-file /private/secure/new-uat-logins.json
```

密碼檔須在 repository 外，每次使用新的檔名。指令以 exclusive create、0600
建立檔案，拒絕覆寫既有檔案或符號連結；帳密不會印出。將首次產生的檔案移交
密碼管理器。重跑只會輸出新建立帳號的密碼，通常為空 JSON，既有密碼不輪替。
失敗時檔案可能保留，但交易會回滾；排除原因後用新檔名重試。

初始化在單一交易、advisory lock 下執行 schema migration、獨立契約驗證、
虛構資料與環境標記。空資料庫可首次初始化；既有資料庫必須已有本程序的
專案／分支標記，否則拒絕。不會因為資料庫名稱含 test 就放行。

驗證共 35 張表（原有 26 + V20 9），含欄位型別、NULL、預設值、主鍵、
既有唯一鍵、branch_signers 與 V20 外鍵、V20 CHECK，以及必要索引。
契約位於 `app/v20_schema_contract.json`，原始基準副本保留在 tests；
測試與遠端驗證共用 `app/v20_schema_checks.py`。

只建立 4 個「虛構星球測試分館」、2 條「虛構測試路線」、1 名虛構司機，
以及 `uat-head-office`、`uat-secretary`、`uat-secretariat` 帳號。
每個登入秘密各自使用 `secrets.token_urlsafe(24)`，只儲存 PBKDF2 hash。
分館與司機的登入秘密沿用既有 pin_hash 儲存欄位，非正式 PIN，且不共用。
沒有匯入 CSV、正式帳號、PIN、QR、簽名、配送紀錄，也不建立 QR 或簽名。
分館可使用桌面登入。司機裝置啟用依既有管理流程處理。

重跑沒有 DELETE、TRUNCATE、重置序列或覆寫資料；不會清除盤點、申請、
簽收、庫存流水、人工設定或修改。路線只補缺，帳號與司機只在不存在時新增。

資料庫初始化及驗證成功後，才在獨立 Render 服務啟用 V20：

- workspace `tea-dab81qjtqb8s73f3fvi0`，repository `victorhuang-bot/lib`
- branch `feature/v20-empty-box-dispatch`，auto deploy 關閉
- Docker start command：`python -m app.uat_serve`
- 僅將指定 UAT 連線放入 `MOVINGMATCH_V20_UAT_DATABASE_URL` 秘密變數
- `MOVINGMATCH_V20_ENABLED=1`，不複製正式服務的其他環境變數或郵件憑證

入口每次啟動都以唯讀交易重新檢查目標、TLS、環境標記與完整 schema，
通過才設定應用程式連線並啟動 uvicorn。失敗不開 HTTP port，亦不初始化或 seed。
正式服務仍使用原入口，沒有改變正式環境設定。

回歸指令：

```sh
python -B -m unittest discover -s tests -v
python -B tests/v195_postgres_runner.py --pg-bin /path/to/postgres/bin
python -B tests/v20_postgres_runner.py --pg-bin /path/to/postgres/bin
```

PostgreSQL runner 只建立全新本機 Unix socket 叢集，拒絕使用遠端連線。
涵蓋盤點、補箱申請、多來源調度、提前送達、稍後補簽、庫存流水、電子簽收，
以及 UAT 首次初始化、重跑保留測試結果與密碼、錯誤外鍵拒絕及未標記資料庫拒絕。

遠端 HTTPS 驗收：安全注入相同 UAT 專用連線環境變數後執行：

```sh
python -B tests/v20_remote_uat_acceptance.py --credentials-file /private/secure/new-uat-logins.json
```

此腳本只連固定的 UAT 網址與固定 Neon 目標。透過真實 HTTP API 驗證三種
管理角色登入、分館盤點、補箱申請、多來源收取、提前收貨、電子簽收及重送。
稍後補簽使用新建的虛構歷史配送／調度 fixture，再透過既有選擇任務與簽收 API
完成；未刪除或替換既有配送。以 SQL 核對庫存守恆、日期與簽名連結。
所有新增紀錄均保留，失敗也不自動清除，避免破壞人工驗收結果。
請在正常配送工作日執行；若測試資料已有待處理調度，先檢視其庫存保留量。

獨立服務的 `/health` 提供 `uat: true`，登入欄位才開放 128 字元的測試密碼；
一般環境仍維持 4 位 PIN 欄位。這不會產生 QR，也不改寫任何密碼。

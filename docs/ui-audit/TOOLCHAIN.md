# UI 檢查工具：專案級安裝與相容性

日期：2026-10-29。第一階段只安裝工具與保存稽核報告；後續司機UI試點另見PILOT_TEST_RESULTS.md，不更動配送資料或後端。應用程式基底為 PR #25 的 `05d05ca`；原 A／B1 功能與 16 項測試先完成，再建立 `feat/v194-ui-audit-tooling`。

## 安裝狀態

| 工具 | 固定版本／來源 | 實際狀態 |
| --- | --- | --- |
| Taste-Skill / design-taste-frontend | v2.1.5；Leonxlnx/taste-skill，12af471f627f866411b4bd785b19646d3672784c | 前一工作已透過 Codex skill-installer 安裝至使用者 Codex skills；本次沿用，未再安裝全域工具。手動讀取 SKILL.md、App Screen、critique 指引，執行 lint／inspect。 |
| Impeccable Codex skill | 4.8.0；pbakaus/impeccable，5e745ace7e151feb2a93d360d0281fe39448b857 | 僅安裝在本 MovingMatch 工作區 `.agents/skills/impeccable`；未使用 npm 全域安裝、未改使用者 Codex 設定。 |
| Impeccable engine | 0.1.18，Darwin x64 | 僅下載到本專案 `.impeccable/engine/bin/0.1.18/impeccable`。SHA-256 與官方固定 manifest 完全一致，見 tools/ui-review-toolchain.json。 |
| 自動 Hook | 不安裝 | 沒有建立 `.codex/hooks.json`，沒有寫入其他 harness／全域 Hook；`.impeccable/config.json` 設 enabled=false，執行時再設 IMPECCABLE_HOOK_DISABLED=1。 |

兩個 skill 本回合均為讀檔手動載入指引；不是宣稱已在目前 TeenBoss 根目錄自動啟用。開啟這個 MovingMatch 專案後，專案級 skill 可供後續會話載入。安裝目錄與引擎是本機工具／快取，gitignored；團隊可依以下來源及固定版本重建，PR 不攜帶未審查的二進位檔。

## 安裝前已檢視內容與採用路徑

先唯讀下載原始碼，檢視官方 README、package.json、Codex SKILL.md、shell launcher、VERSION、engine-checksums.txt、Codex／plugin Hook JSON、hook reference 與預設 config，才執行安裝。

官方 npm 安裝會偵測 harness 並可寫入 Hook；npm shim 與 launcher 預設也可能使用 `~/.impeccable`。本次**沒有執行 npx impeccable install、npm lifecycle、Rust build 或自動 Hook 安裝**。採官方支援的手動複製技能模式，實際由已讀過的 Codex skill-installer 以 Git 固定 commit 下載 `.agents/skills/impeccable` 到專案目的地。沒有複製上游的 AGENTS.md、PRODUCT.md、DESIGN.md 或上游工具設定到 MovingMatch。

首次引擎啟動只使用已檢查的 launcher；透過 IMPECCABLE_HOME 明確指定專案內路徑。下載檔先比對來源 manifest SHA-256 才執行。官方二進位未逐行審查或從源碼重建；SHA 驗證證明下載符合固定官方 digest，不是完整供應鏈安全認證。之後使用 `scripts/ui-review.sh` 直接執行本專案已驗證引擎，不再經過全域 fallback 或自動下載。

安裝重現所需的固定來源：

- `https://github.com/pbakaus/impeccable/tree/5e745ace7e151feb2a93d360d0281fe39448b857/.agents/skills/impeccable`
- 從已檢視的 Codex skill-installer，指定 `--repo pbakaus/impeccable --ref 5e745ace7e151feb2a93d360d0281fe39448b857 --path .agents/skills/impeccable --name impeccable --dest <本專案>/.agents/skills --method git`。
- 首次下載引擎前仍先檢查安裝內容，指定 `IMPECCABLE_HOME=<本專案>/.impeccable/engine`；勿使用未設定 scope／快取的全域命令。沒有安裝與明確校驗時，`scripts/ui-review.sh` 會拒絕執行。

本次 launcher engine-probe 成功，且 `scripts/ui-review.sh install` 被明確拒絕（預期 exit=2）。wrapper 只允許 context、detect、engine-probe、--version、--help，禁止 install、init、live、hooks 及其他可能寫回程式的操作；每次先重新驗證 engine hash。引擎仍可能在本專案寫檢查快取，但不能因此宣稱已進行瀏覽器操作功能驗收。

## 相容性結論

- **可搭配使用**：MovingMatch 是 FastAPI＋原生 HTML/CSS/JS；兩套工具不要求遷移 React、Tailwind 或重建元件，不需要更改登入、API、簽名或報表。
- Taste-Skill 使用 **App Screen** 指引；Impeccable 使用 **Operate** 模式。共同方向是保持既有資訊架構，優先可讀、可操作、狀態真實及低裝飾。
- Taste-Skill 決定視覺提案與版面準則；Impeccable 的 critique／audit／adapt／harden／polish 是 agent 指引，**不是五個可自動執行並得出通過分數的測試程式**。本輪依五份 playbook 人工分析，不執行其修改步驟；自動掃描器是 `detect`，另有 Taste lint／inspect。
- 字型、checkmark、陰影等 taste finding 不凌駕使用者熟悉的介面。保留原有系統字型、文字與角色分工，不因反模板規則擅自替換品牌或刪除按鈕。日期補零不是裝飾編號；工具 finding 必須人工分類，不照單全改。
- 避免雙重 Hook、不用 live overlay、不修改 CSP、不啟動應用後端。正式站檢查只用無登入狀態的隔離 Chrome，未輸入帳密或提交表單。

## 已執行檢查

- Node v22.23.1、已安裝 Google Chrome，符合 Taste-Skill 的 Node 22+／Chrome 要求。
- Impeccable `context --target app/static/driver.html` 成功；回報目前沒有 PRODUCT.md／DESIGN.md，但可依既有實作進行局部 refinement。這不是重新設計的授權。
- Impeccable `detect --json app/static`：exit 2，7 個 warning、1 個 advisory。沒有設定忽略 app 的任何 finding。
- Impeccable 對正式未登入首頁：390×844、1440×900 各一次，均 exit 0／0 finding；只代表該未登入頁在該掃描器規則下沒有 finding。
- Taste lint --app：8 個來源檔，9 FAIL／20 WARN；主要是 checkmark、placeholder 與 shadow 等規則。不是配送功能失敗。
- Taste inspect --app：正式未登入首頁於 1440／768／390；exit 0、0 FAIL／2 WARN。三個寬度 horizontalOverflow 均為 0；WARN 為桌機留白、手機 h1=32px。已實際查看桌機與手機截圖。
- Taste inspect --app：本機司機未登入頁於1440／768／390，exit 0、0 FAIL／2 WARN；僅代表登入畫面。初次因 /tmp 與 /private/tmp 路徑別名導致樣式未載入的結果已排除，修正檢查路徑後才保存有效證據。
- 原有功能回歸在此獨立工作區重跑：16／16 通過，暫存 SQLite、網路禁止、Gmail mock。未使用正式資料。

對四角色登入後的工作畫面只能做來源分析：沒有授權測試 session／司機裝置／分館 PIN，沒有繞過驗證或製造假配送狀態。工具未看過的畫面不得標記通過；真機 Safari、實際手寫簽名、已登入資料表及弱網操作都仍需驗收。

UI測試JPEG屬本機證據，不是正式網站資源。已依使用者要求從未推送提交排除，沒有提交至PR #25；不要重試Create Blob或將本機圖片混入後續PR。

# 司機 UI 試點測試結果（本機、未登入）

日期：2026-10-29。分支 feat/v194-ui-audit-tooling；應用基底為 PR #25 的 05d05ca。本輪不修改 PR #25、不合併 main、不部署 Render。

## 已完成的 UI 精修

只變更 driver.html 的 CSS 引用、body scope、PIN 關聯 label、錯誤區 role／aria-live，以及新增 driver-ui.css。四項數量、路線切換、所有操作按鈕及 inline JavaScript 均保留；共用 common.js、後端、其他角色頁面、權限、API、資料庫、簽名及報表完全不改。

Taste-Skill App Screen 選擇 framed-sheet、seed 1004166398，保留現有 palette／type。選擇其低裝飾、淺底白工作面的方向，依使用者「精修而非重設計」限制保留原有單欄及操作架構，不新增 sidebar。頁題由32px改為24px，按鈕至少48px、數量右對齊與tabular figures、數量輸入24px，統一焦點與錯誤文字樣式。

## 測試程式安全檢查

`tests/driver_ui_browser.mjs` 共112行，已完整檢視後才執行：

1. 只啟動127.0.0.1隨機port的靜態白名單伺服器及隔離Chrome；不啟動FastAPI。
2. Server僅提供5個既有靜態路徑，僅允許GET／HEAD；CDP Fetch再次限制origin、路徑及method，其他請求全部阻擋。
3. 不匯入後端、DB driver或環境檔，不連正式網站、正式API或正式DB。
4. 不使用正式帳號、密碼、Cookie或司機裝置key；PIN保持空白。Chrome是全新暫存profile，並停用背景網路、同步、擴充套件及元件更新。
5. 測試程式完全不使用child_process或execFileSync。已檢視的Taste-Skill CDP helper只負責啟動／關閉隔離Chrome；測試使用固定SHA-256檢查原程式，不需呼叫Git。
6. 截圖、JSON、log全寫入本機docs/ui-audit/evidence，該整個目錄已被.gitignore忽略；沒有提交截圖或暫存資料。

## 實際結果

瀏覽器程序正常結束、exit=0。**8個測項通過、0失敗**：2項來源指紋、4個尺寸情境、2項網路防護。這個數量不是登入後配送功能的通過數。

| 尺寸 | 水平溢出 | 頁題 | 按鈕高 | 輸入框高 | label／鍵盤Tab／既有錯誤提示 |
| --- | --- | --- | --- | --- | --- |
| 320×812 | 無 | 24px | 48px | 66px | 通過 |
| 390×844 | 無 | 24px | 48px | 66px | 通過 |
| 768×1024 | 無 | 24px | 48px | 66px | 通過 |
| 1440×900 | 無 | 24px | 48px | 66px | 通過 |

- 所有尺寸確認driver-ui.css載入，無JavaScript例外。
- 點擊label可focus PIN；Tab可到原有「進入今日路線」按鈕。
- 新profile無裝置key，測試原有「此手機尚未啟用」錯誤分支，確認在aria-live區顯示；不是模擬正式配送狀態。
- 原inline JS及common.js與05d05ca指紋相同。
- Server接收到的API請求為0，寫入請求為0；本次沒有嘗試外部請求，不把「未出現外部流量」說成已對所有惡意情境做過滲透驗證。
- 16項既有Python／API／報表／Gmail mock回歸全數通過，exit=0；使用新建暫存SQLite，沒有讀寫正式DB或寄信。

## 設計工具結果

- Taste-Skill來源lint（driver.html＋driver-ui.css）：**0 FAIL、5 WARN**。3個copy提示是原有unlock ID／函式名稱，1個是日期padStart，1個是掃描器對box-shadow:none的提示；不為消除提示而改動登入名稱或日期處理。沒有自動忽略規則。
- Impeccable來源detect：**1 warning、0 advisory**，為沿用現有Inter宣告；依保持既有介面要求不換字型。exit=2代表有finding，不代表執行失敗或通過所有無障礙檢查。
- 兩套工具的既有before inspect結果及四角色來源稽核見REPORT.md／TOOLCHAIN.md。此次UI操作與尺寸驗證由本機CDP測試執行，不將它冒稱為Taste完整inspect或四角色端到端。

## 前後比較及本機證據

| 項目 | before | after |
| --- | --- | --- |
| 手機頁題 | 32px | 24px |
| PIN說明 | p文字，無關聯label | label for=pin，點擊與鍵盤驗證通過 |
| 錯誤區 | 一般div | 保留原錯誤文字，增加role=status／aria-live |
| 四數量與流程 | 原有值、欄名與inline行為 | 原有inline與common指紋不變，僅追加司機專用樣式 |
| 命中區／溢出 | before未對全部操作逐一量測 | 未登入頁4尺寸無溢出，主要按鈕48px |

原有6張before截圖與新產生的4張after截圖、測試log及JSON**全部只留本機**。位於docs/ui-audit/evidence，沒有Git追蹤，不會上傳GitHub。after截圖只含空白PIN的未登入頁（部分含鍵盤focus樣式），不是已登入作業成果。沒有正式網站圖片資源新增。

## 失敗與仍需改善／驗收

本次範圍沒有失敗測項，但以下尚未實測，不可宣稱通過：

- 已登入司機的四數量、長分館名、實際資料表、路線切換與全部操作按鈕的響應式／鍵盤檢查。
- 真實PIN／裝置啟用／QR、分館補簽與更正、路線／總館簽核、手寫簽名與儲存。
- 真機iOS Safari／Android Chrome、觸控手勢、200%縮放與弱網恢復。
- 全站WCAG AA、其他三角色的互動驗收、正式PostgreSQL整合及效能量測。

後續應優先使用獲授權的隔離測試環境驗證上述登入後流程；不在正式站造資料或以虛構配送狀態截圖。無正式裝置key時不繞過驗證。共享表格隱藏欄及分館簽收人選用規則等仍按原稽核列為待處理，不在本司機樣式PR偷偷更改業務邏輯。

## 重現

```sh
node --check tests/driver_ui_browser.mjs
node tests/driver_ui_browser.mjs
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

瀏覽器測試需Node22+、本機Chrome及已檢視的Taste-Skill CDP helper。未安裝時應先依TOOLCHAIN.md處理，不能把無法執行標成通過。

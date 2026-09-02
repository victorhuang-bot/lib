# 圖書物流配送暨電子簽收管理系統 — GitHub Deploy Edition

這是可直接放入 GitHub Repository，並透過 Docker 部署到公開 HTTPS 網站的 MVP / UAT 版本。

## 已包含

- 管理者 / 總館秘書登入
- 分館新增、修改、停用 / 啟用
- 分館地址、電話、聯絡資訊、路線、配送星期 / 頻率、站點排序
- 固定分館 QR：查看、列印、重新產生、舊 QR 失效
- 分館 4 位 PIN
- 路線新增 / 修改與每日司機指派
- 司機新增、修改、停用 / 啟用
- 司機裝置管理、PIN 重設
- **司機 10 分鐘一次性 Activation QR**
- 秘書公文數量 → 司機送出 → 分館簽收 → 司機確認
- 每筆配送最多一次更正
- SSE 即時配送看板
- 配送 / 更正紀錄
- Audit Log
- Excel / PDF 報表
- Email 管理骨架

## 本機執行（Mac）

```bash
chmod +x start.sh
./start.sh
```

開啟：

`http://127.0.0.1:8000`

Development 模式在沒有設定 `.env` 時仍保留專案原先的本機測試初始值。

## GitHub / 公開部署

請閱讀：

`DEPLOY_GITHUB.md`

Production 版密碼不寫入 GitHub，必須由 Hosting Provider 的 Secret Environment Variables 注入。

## 公開部署後 QR 為什麼可以用手機掃？

本機 `127.0.0.1` 只代表目前這台電腦，所以手機無法連入。

部署完成後 QR 會使用類似：

`https://your-domain.example/branch/...`

與：

`https://your-domain.example/activate-driver/...`

因此不同手機可以直接連線測試。


## V4 mobile test changes

- Test deployment activates only 3 branches; the remaining seeded branches are inactive and can be reactivated later.
- Driver outbound quantity stays visible after save and can be edited until branch signing.
- Branch correction always exposes document / outbound / inbound quantities.
- Driver may request another correction after each correction until confirming the stop.
- Correction requests are stored as repeated history rows rather than a one-correction-only record.


## V6 更正規則
分館收到司機更正要求後，公文、圖書送出、圖書收回三個數量欄位可選擇性修改；留白即沿用原值。更正原因、更正人姓名與新簽名仍必填。


## V7 permission update
- ADMIN: route/driver assignment management; document quantities are view-only.
- SECRETARY: public-document quantity editing, branch PIN/QR/basic branch management; routes and daily driver assignments are view-only.
- Dashboard route cards can be clicked to filter branch realtime status by route.

## V8：排程例外與三方簽核

總館秘書可在「休館 / 停送 / 加送」頁面，針對指定日期與分館設定 `STOP`（休館/停送）或 `ADD`（臨時加送）。管理者可查看但不能異動。若該站配送已開始（已有公文、司機送出或分館簽收），系統不允許再直接設定停送。

配送完成後，司機手機的每條路線會顯示今日路線總表。所有站點均為完成狀態後，司機才能手寫簽名並送出。總館秘書在「日報 / 月報」可查看各路線簽名進度；全部有配送任務的路線都完成司機簽名後，才可進行第三方手寫簽名。秘書簽名後，今日日報鎖定。


## V9 更新
秘書公文支援批次一次確認；休館為全部分館停送；停送支援日期區間與拖曳月曆；「臨時加送」更名「非固定運送」；管理者/秘書登入改為頁籤獨立 Session。

## V10 日終完整結算
司機在路線簽名前會先核對各分館「公文 / 圖書送出 / 圖書收回」與路線合計。管理者及秘書可查看分館簽名與司機簽名。所有有任務路線完成司機簽名後，總館秘書才能簽署整份今日路線總表並鎖定日報。鎖定後才開放正式 Excel / PDF 下載。下一配送日的公文欄位由 0 重新填寫，歷史日報與簽名保留。


## V11
日報可隨時下載；新增逐路線秘書簽名與前端 PNG 完整簽收明細下載。


## V12 調整
司機今日路線總表於最終路線簽名前，只顯示各分館「公文」與「圖書收回」及兩項本線總計；圖書送出不在此畫面顯示。


## V15 hotfix
修正 Render 上新產生司機啟用 QR 立即被判定失效的時區錯誤。


## V16 正式子網域
正式網址：`https://lib.moving-match.com`。新產生的分館固定 QR 與司機裝置啟用 QR 均使用此網址。

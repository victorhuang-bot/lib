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

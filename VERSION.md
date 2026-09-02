# V18.3.11 PostgreSQL — 公文存檔 / 登入 502 穩定修正版

基底：V18.3.10 Neon Pooler 修正版

## 核心修正
- `login` 的 PostgreSQL 工作改用 `asyncio.to_thread()` 執行，避免 Neon 連線慢時阻塞 FastAPI 主事件迴圈。
- `prefill-save` 與 `prefill-batch` 同樣移到 worker thread。
- 因此單一公文存檔即使 Neon 短暫變慢，不應再把整個網站（包含登入）一起卡住。
- 公文「寫入」API 不再自動 POST 重送，避免第一次已成功但回應中斷時造成重複操作。
- 登入仍可在 502/503/504 時自動重試一次。
- `index.html` 設定 no-cache，且 `common.js?v=18.3.11` 強制載入新版。
- Render 502 HTML 繼續轉成簡短純文字錯誤，不再整段顯示。

## 保留
- 公文日期自由選擇。
- 選日期只讀清單，不大量建立未來 delivery。
- 按存檔才建立該日期/該分館 delivery。
- 今日即時看板不受未來公文預填影響。
- 預設司機：路線1許春芳、2陳錦隆、3彭運土、4林聖原、5張閔傑、6陳錦隆。
- 人工臨時改派、支援司機二次確認、V17 臨時交接。
- Neon pooler ConnectionPool。

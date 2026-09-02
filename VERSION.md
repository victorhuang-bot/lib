# V19.1 — 登入修正版

## 修正
- 管理者 moving 與秘書 lib 帳號改為分別檢查/建立，不再因 users 表已有其他資料而漏建帳號。
- V18 曾在 system_accounts 修改過的密碼可於首次成功登入時自動遷移回 V19 的 users 帳號表。
- 登入後仍統一使用 users.password_hash，後續後台改密碼與登入使用同一份資料。
- ADMIN_INITIAL_PASSWORD / SECRETARY_INITIAL_PASSWORD 只用於帳號不存在時建立，不會覆寫已存在帳號。
- 新增緊急管理者登入修復 API，但預設停用；只有 Render 暫時設定 ADMIN_LOGIN_RECOVERY=true 才能啟用。

## 保留
- V19 路線 1～6
- 74 個正式分館名冊
- Excel-only 報表
- Email 功能已移除

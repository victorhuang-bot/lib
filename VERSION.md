# V17 — 司機臨時交接 + SMTP 準備

- 管理者可從指定「尚未完成分館」起，把後續未完成站點臨時交給另一位司機。
- 已完成站點維持原司機，不覆寫責任歷史。
- 接手司機手機顯示路線、原司機、接手起點、原因、剩餘站數。
- 新增 route_handoffs、delivery_driver_assignments、route_segment_signatures。
- Audit Log 記錄 DRIVER_HANDOFF。
- Excel/PDF 報表新增「實際配送司機」。
- SMTP 支援 587 STARTTLS / 465 SSL，密碼由 Render Environment 注入，不寫入 GitHub。
- 正式網域：https://lib.moving-match.com

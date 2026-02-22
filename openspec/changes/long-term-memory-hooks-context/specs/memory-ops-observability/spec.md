## ADDED Requirements

### Requirement: 記憶決策觀測事件
系統 MUST 針對每次記憶召回與組裝產生結構化觀測事件，至少包含命中數、淘汰數、淘汰原因、與最終注入摘要大小。

#### Scenario: 完成一次前置記憶組裝
- **WHEN** 前置記憶組裝成功完成
- **THEN** 系統 MUST 寫入一筆結構化事件，記錄召回與裁剪決策

### Requirement: 回退與錯誤可觀測
系統 MUST 針對增強層失敗、hook 例外、與回退路徑留下可查詢事件，供後續告警與除錯。

#### Scenario: 增強層失敗觸發回退
- **WHEN** 增強層拋出錯誤並改走基礎層
- **THEN** 系統 MUST 記錄錯誤類型、回退原因、與本次是否成功完成回覆

### Requirement: AI 模組切換審計
系統 MUST 記錄所有 Telegram 端 AI 模組切換事件，包含操作者、切換前後狀態與生效範圍。

#### Scenario: 使用者切換 reranker
- **WHEN** 使用者在 Telegram 將 `reranker` 從開啟改為關閉
- **THEN** 系統 MUST 寫入一筆切換審計事件且可供後續查詢

### Requirement: Provider 降級事件可觀測
系統 SHALL 在 AI 增強配置無法完整映射到 provider runtime 時記錄降級事件，包含降級項目與替代策略。

#### Scenario: provider 不支援某增強參數
- **WHEN** 系統發現目前 provider 不支援某個 AI 增強執行參數
- **THEN** 系統 MUST 記錄降級事件並繼續使用可用參數執行

### Requirement: 記憶系統+ 回歸基線觀測
系統 MUST 針對 `memory_system_plus=off` 建立原流程基線指標，並可與開啟模式做差異比較。

#### Scenario: 比較開關前後行為
- **WHEN** `memory_system_plus` 開關狀態切換
- **THEN** 系統 MUST 記錄可比較的延遲、錯誤率與回退率指標

### Requirement: 優化迭代指標
系統 SHALL 輸出記憶系統優化所需的核心指標（召回命中率、組裝延遲、增強成功率），供持續調優使用。

#### Scenario: 週期性優化檢視
- **WHEN** 執行記憶系統優化檢視流程
- **THEN** 系統 MUST 提供最近期間的核心指標摘要供評估

### Requirement: 觀測資料敏感資訊保護
系統 SHALL 在觀測事件中避免落地敏感原文，必要欄位 MUST 使用脫敏或統計值替代。

#### Scenario: 記錄事件含使用者輸入內容
- **WHEN** 觀測欄位原本可能包含 prompt 或記憶原文
- **THEN** 系統 MUST 以脫敏後片段或統計資料寫入，不得直接落完整敏感內容

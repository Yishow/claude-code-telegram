## ADDED Requirements

### Requirement: AI 增強層預設啟用
系統 MUST 預設啟用 AI 增強層，並使用 `gpt-5-mini` 作為預設增強模型（在 provider 可用時）；但僅在 `memory_system_plus` 開啟時生效。

#### Scenario: 啟動時套用預設模型
- **WHEN** 系統初始化且未提供其他增強模型設定
- **THEN** 系統 MUST 以 `gpt-5-mini` 作為增強層預設模型

### Requirement: AI 增強模組可細分開關
系統 MUST 提供以下 AI 子模組的獨立開關：`extractor`、`reranker`、`conflict_detector`、`periodic_review`。

#### Scenario: 只停用 reranker
- **WHEN** 使用者將 `reranker` 切為關閉
- **THEN** 系統 MUST 停止執行 AI 重排，但仍可執行其他已啟用子模組

### Requirement: 總開關關閉時禁止執行增強模組
當 `memory_system_plus` 為關閉時，系統 MUST 忽略所有 AI 子模組開關並禁止增強模組執行。

#### Scenario: memory_system_plus 關閉但子模組為開啟
- **WHEN** `memory_system_plus` 關閉且 `extractor`/`reranker` 等子模組設定仍為開啟
- **THEN** 系統 MUST 不執行任何 AI 增強模組，並回覆目前為基礎流程模式

### Requirement: 增強層失敗自動回退
當 AI 增強層不可用、逾時或回傳異常時，系統 MUST 自動回退到 deterministic 基礎層，且不可中斷本次對話流程。

#### Scenario: 增強層逾時
- **WHEN** 增強層請求超過設定 timeout
- **THEN** 系統 MUST 立即改用基礎層結果繼續執行，並記錄 timeout fallback 事件

### Requirement: Telegram 可自由切換 AI 模組
系統 MUST 在 Telegram 提供可互動控制入口（指令或按鈕），讓使用者查詢目前 AI 模組狀態並即時切換。

#### Scenario: 使用者在 TG 切換 conflict_detector
- **WHEN** 使用者透過 Telegram 控制介面將 `conflict_detector` 切換為關閉
- **THEN** 系統 MUST 立即在後續請求採用新設定並回覆切換結果

### Requirement: AI Profile 快速切換
系統 SHALL 提供可切換的 AI 設定 profile（至少包含 `fast`、`balanced`、`quality`），以批次調整模組開關與執行參數。

#### Scenario: 使用者切換到 quality profile
- **WHEN** 使用者在 Telegram 選擇 `quality` profile
- **THEN** 系統 MUST 套用對應的模組組合與參數，並回覆目前生效配置

### Requirement: 切換設定需持久化
系統 SHALL 將 AI 模組開關與增強配置持久化，服務重啟後 MUST 保持最近生效設定。

#### Scenario: 重啟後維持開關狀態
- **WHEN** 使用者先前關閉 `periodic_review` 並發生服務重啟
- **THEN** 系統 MUST 在重啟後仍維持 `periodic_review` 關閉狀態

### Requirement: 應用層設定為執行真相
系統 MUST 以應用層持久化設定作為 AI 增強執行真相；provider runtime controls 只作為請求時映射參數，不得作為唯一設定來源。

#### Scenario: Provider 重建 session
- **WHEN** Copilot session 因重建或切換而變更
- **THEN** 系統 MUST 仍以持久化的 AI 模組設定執行，不得回退為 provider 預設值

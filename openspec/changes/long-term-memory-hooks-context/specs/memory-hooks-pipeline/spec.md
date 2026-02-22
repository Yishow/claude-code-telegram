## ADDED Requirements

### Requirement: 記憶系統+ 總開關閘門
系統 MUST 提供 `memory_system_plus` 總開關；當開關為關閉時，記憶 hooks 與 AI 增強流程 MUST 全部停用，並維持既有對話流程行為。

#### Scenario: memory_system_plus 關閉
- **WHEN** `memory_system_plus` 設為關閉
- **THEN** 系統 MUST 直接走原本 prompt/session 執行路徑，不得注入任何記憶上下文

### Requirement: 前置記憶組裝 Hook
系統 MUST 在送出使用者 prompt 前執行前置記憶組裝流程，從符合範圍的記憶中挑選可用內容並附加到本次上下文。

#### Scenario: 一般文字訊息觸發前置組裝
- **WHEN** 使用者在既有工作目錄送出一則新訊息
- **THEN** 系統 MUST 先完成記憶候選召回與組裝，再將組裝後上下文送入 provider 執行

### Requirement: 後置記憶萃取 Hook
系統 MUST 在每次回覆完成後執行後置記憶萃取，將新互動轉為可重用的結構化記憶並寫入儲存層。

#### Scenario: 回覆成功後寫入記憶
- **WHEN** provider 回傳最終回覆且本次互動有有效 session 與 prompt
- **THEN** 系統 MUST 產生記憶項目並保存來源訊息關聯

### Requirement: Hook 失敗不可中斷主流程
前置或後置 hook 任一階段失敗時，系統 MUST 記錄失敗事件並持續使用既有 session 對話流程，不得直接中斷本次請求。

#### Scenario: 前置 hook 失敗時回退
- **WHEN** 前置記憶組裝發生例外或逾時
- **THEN** 系統 MUST 以無記憶增補的原始 prompt 繼續執行，並記錄 fallback 原因

### Requirement: Hook 執行需遵循 AI 子模組開關
系統 MUST 依當前 AI 子模組開關決定是否執行對應增強步驟，關閉的模組不得被呼叫。

#### Scenario: extractor 關閉時的後置流程
- **WHEN** `extractor` 模組處於關閉狀態且回覆完成
- **THEN** 系統 MUST 跳過 AI 萃取增強步驟並使用 deterministic 萃取流程

# Bot 完整生命週期分析

> 分析範圍：從程序啟動、訊息處理、Session 管理到程序關閉的完整技術流程。

---

## A. 啟動流程

```
src/main.py → run() → main() → create_application() → run_application()
```

### 初始化順序（嚴格順序依賴）

```
1.  setup_logging()            → structlog JSON(prod) / console(dev)
2.  Storage.initialize()       → SQLite tables 建立
3.  AuthenticationManager      → WhitelistAuthProvider + TokenAuthProvider
4.  SecurityValidator          → 路徑白名單、pattern 過濾
5.  RateLimiter                → token bucket
6.  AuditLogger                → InMemoryAuditStorage
7.  SessionManager             → SQLiteSessionStorage
8.  ToolMonitor                → tool allowlist/denylist
9.  ClaudeSDKManager           → Claude SDK 連線
10. ClaudeIntegration          → facade，包 SDK + Copilot SDK + Copilot CLI
11. EventBus                   → pub/sub
12. ClaudeCodeBot              → Telegram Application 建立
13. FeatureRegistry            → 功能旗標
14. MessageOrchestrator        → handlers 注冊
15. Middleware 綁定            → group -3 / -2 / -1
16. NotificationService        → 啟動
17. asyncio.create_task(bot.start()) → 開始 polling
```

---

## B. 訊息到回應的完整路徑

```
Telegram 訊息
    │
    ▼
[group -3] SecurityValidator                  src/security/validators.py
    • validate_path() — 路徑遍歷、危險字元檢查
    • DANGEROUS_PATTERNS: ["..", "$(", "&&", ";", "|", ...]
    │ 失敗 → raise ApplicationHandlerStop
    ▼
[group -2] AuthMiddleware                     src/bot/middleware/auth.py:11
    • auth_manager.is_authenticated(user_id)
    • 失敗 → authenticate_user()
      → 成功：送歡迎訊息
      → 失敗：送拒絕訊息，return
    • 記錄 audit_logger.log_auth_attempt()
    │
    ▼
[group -1] RateLimitMiddleware                src/bot/middleware/rate_limit.py:10
    • estimate_message_cost(event)
        基礎 0.01 + 長度×0.0001
        文件/圖片 +0.05 ｜ 指令 +0.02
    • rate_limiter.check_rate_limit(user_id, cost)
    │ 超限 → 回覆「⏱️ Rate limit exceeded」，return
    ▼
[group 10] agentic_text()                     src/bot/orchestrator.py:689
    • 取得 user_id, message_text, current_dir, session_id, force_new
    • 送 typing 動作 + 「Working...」訊息
    • 啟動 typing heartbeat（每 2 秒重送 typing action，獨立 task）
    │
    ▼
ClaudeIntegration.run_command()               src/claude/facade.py:46
    │
    ├─ 自動 Resume 查找 _find_resumable_session()
    │      查同 user + directory 的 session
    │      條件：有 session_id（非空）且未過期
    │      選擇：max(matching_sessions, key=last_used)
    │
    ├─ SessionManager.get_or_create_session()  src/claude/session.py:167
    │      1. 查 active_sessions cache（記憶體）
    │      2. 查 SQLiteSessionStorage
    │      3. 均無 → 建新 session：session_id = "", is_new_session = True
    │      4. 超過 max_sessions_per_user → 刪最舊的（by last_used）
    │
    ├─ 建立 stream_handler（工具驗證 + 轉發）
    │      每個 tool_call → ToolMonitor.validate_tool_call()
    │      關鍵工具失敗 → raise ClaudeToolValidationError
    │      只有 StreamUpdate 才轉發 on_stream（CopilotStreamUpdate 不轉）
    │
    ├─ _execute(provider="copilot")           src/claude/facade.py:270
    │      │
    │      └─ _execute_copilot()              src/claude/facade.py:301
    │             │
    │             ├─ [主要] CopilotSDKManager.execute_command()
    │             │         src/claude/copilot_sdk_integration.py:71
    │             │
    │             │    _get_client() → 長連線 CopilotClient（只啟動一次）
    │             │
    │             │    continue_session=True
    │             │      → client.resume_session(session_id)
    │             │      → 失敗 → client.create_session()（自動降級）
    │             │
    │             │    continue_session=False
    │             │      → client.create_session(SessionConfig(model, workspace_path))
    │             │
    │             │    send_and_wait({"prompt": ...}, timeout=300)
    │             │    儲存 _session_map["user:dir"] = session.session_id
    │             │
    │             └─ [失敗降級] CopilotProcessManager.execute_command()
    │                           src/claude/copilot_integration.py
    │                           (CLI subprocess fallback)
    │
    ├─ SessionManager.update_session()         src/claude/session.py:229
    │      is_new_session=True → session.session_id = response.session_id
    │      更新 last_used, total_cost, message_count
    │      storage.save_session() → SQLite 持久化
    │
    └─ 回傳 ClaudeResponse（含 session_id, content, cost, duration_ms）
    │
    ▼
agentic_text() 繼續
    • context.user_data["claude_session_id"] = response.session_id
    • storage.save_claude_interaction()
        → messages, tool_usage, cost_tracking, audit_log 各表
    • ResponseFormatter.format_claude_response() → HTML / plain text
    • 刪除「Working...」訊息，送出最終回應
    • audit_logger.log_command(success=True)
```

---

## C. Session 生命週期

### 狀態轉移

```
[建立]
  session_id = ""
  is_new_session = True
  未寫入 SQLite
       │
       │ Claude / Copilot 回應後
       ▼
[已建立]
  session_id = "abc-123"（來自 response）
  is_new_session = False
  已寫入 SQLite
       │
       │ 再次使用同 user + directory
       ▼
[Resume]
  auto_find → get_or_create_session(session_id="abc-123")
  continue_session = True
  Copilot SDK → client.resume_session("abc-123")
  last_used 更新
       │
       │ 超過 session_timeout_hours
       ▼
[過期]
  is_expired() = True
  _find_resumable_session() 不會選到此 session
  cleanup_expired_sessions() → 從 cache 和 SQLite 移除
```

### Session 儲存層

| 儲存位置 | 類型 | 說明 |
|---------|------|------|
| `SessionManager.active_sessions` | `Dict[session_id, ClaudeSession]` | 記憶體 cache |
| SQLite `sessions` table | `SQLiteSessionStorage` | 跨重啟持久化 |
| `CopilotSDKManager._session_map` | `Dict["user:dir", copilot_sid]` | Copilot SDK 專用 |

### Resume 失敗處理

```python
# src/claude/facade.py:169
try:
    response = await self._execute(session_id=claude_session_id, continue_session=True)
except Exception as resume_error:
    if "no conversation found" in str(resume_error).lower():
        await session_manager.remove_session(session.session_id)
        session = await session_manager.get_or_create_session(user_id, directory)
        response = await self._execute(session_id=None, continue_session=False)
```

---

## D. 關閉流程

```
SIGINT / SIGTERM
    │
    ▼
shutdown_event.set()
    │
    ▼
有序關閉（src/main.py:353，finally block）

1. scheduler.stop()               停止 APScheduler cron jobs
2. notification_service.stop()    停止 EventBus 通知推送
3. event_bus.stop()               停止 pub/sub
4. bot.stop()                     is_running=False → 停止 polling
5. claude_integration.shutdown()  cleanup_expired_sessions()
6. storage.close()                關閉 SQLite connection pool

logger: "Application shutdown complete"
```

---

## 關鍵設計要點

| 面向 | 設計決策 | 位置 |
|------|---------|------|
| Session ID | 空白 `""` 開始，回應後才有真實 ID，避免送假 ID 給 API | `session.py:34` |
| Middleware 順序 | -3安全 → -2認證 → -1限速，任一層拒絕即中止 | `core.py` |
| Copilot Client | 單一長連線，所有用戶共用，session 用 `user:dir` key 區分 | `copilot_sdk_integration.py:57` |
| Stream Handler | 只處理 `StreamUpdate`（Claude），不處理 `CopilotStreamUpdate` | `facade.py:143` |
| Resume 失敗 | 偵測 "no conversation found" 自動降級為新 session | `facade.py:169` |
| Typing Indicator | 獨立 heartbeat task 每 2 秒送一次，保持 UI 有反應感 | `orchestrator.py:617` |
| Copilot 降級 | SDK 失敗自動 fallback 到 CLI subprocess | `facade.py:316` |

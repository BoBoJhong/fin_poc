# 專案完成狀態與正式上線缺口

- 專案：Verified Financial RAG MCP
- 產品版本：`1.0.0`
- 公開 MCP Schema：`1.1`
- 最後更新：`2026-07-21`

## 結論

本專案的核心 RAG、前後端、公開 MCP、公司與期間解析、引用驗證、資料來源隔離及拒答流程
已完成，可用於本機完整展示、Evidence-only MCP 串接與小型內網試運行。

目前尚不能宣稱全面 Production Ready。主要缺口不是重做 RAG，而是接入最終正式 LLM／
DB／API，以及完成正式安全部署、容量驗收、監控與 Release 基線。

## 已完成

### 產品與 Runtime

- React 聊天前端。
- FastAPI 後端及 SSE 回應。
- Financial RAG 公開 MCP。
- Earnings Call 公開 MCP。
- 前端與外部 Agent 共用公開 MCP 流程。
- 統一 MCP Runtime Schema `1.1`。
- Answer 與 Evidence-only 兩種使用模式。

### RAG 與資料正確性

- Qwen `qwen3-embedding:0.6b` Embedding。
- Company Resolver：從正式名稱、Alias、股票代碼解析 `co_code`。
- Company Master 限制：模型不能自行建立股票代碼。
- Period Resolver 與 Fiscal Calendar。
- 支援明確季度及「最近一季／上一季／去年同期」。
- 財務與法說會分開檢索、分開驗證、分開引用。
- Evidence、Citation、Source Preview、Content Hash、Data Version。
- 錯公司、錯期間、未來期間、缺少證據時拒答。
- Answer citation、數值及 claim/evidence 驗證。

### 財務資料能力

- Financial Schema v2。
- Raw Payload 保存。
- Metric Dictionary。
- Provider Metric Mapping。
- 精確 Decimal 財務數值。
- Unit、statement、duration、scope、revision 等資料維度。
- 支援大量動態財務 Key。
- SQLite Repository。
- 核准的外部 SQL DB Adapter。
- 核准的 JSON REST API Adapter。
- 未知或未核准指標不會直接成為可回答 Evidence。

### 可靠度與併發控制

- API bounded concurrency。
- LLM 與 Embedding 獨立 concurrency limit。
- HTTP／SQL 連線池。
- Queue timeout 與受控 HTTP `503`。
- 超載後服務可恢復，不會產生未驗證答案。

### 規格與文件

本專案正式維護三份主要規格：

1. [專案規格](docs/PROJECT_SPEC.md)
2. [提供同事的 MCP 規格](docs/MCP_PROVIDER_HANDOFF_SPEC.md)
3. [新增其他 MCP 規格](docs/ADDING_EXTERNAL_MCP.md)

機器規格：

- [OpenAPI](docs/openapi.json)
- [MCP Tool Schema](docs/mcp-tools.json)

## 已完成的真實資料驗證

目前並非全部使用虛構資料，已包含：

- AAPL、MSFT、NVDA 的真實 SEC 財務／申報資料測試。
- Microsoft 官方 Investor Relations 法說會逐字稿測試。
- 跨語言查詢。
- 跨公司隔離。
- 缺少期間與未來期間拒答。
- Citation 來源定位與內容追溯。

最近一次驗證結果：

| 項目 | 結果 |
|---|---:|
| 自動測試 | 67 passed |
| 真實 SEC Golden Set | 6/6 passed |
| 真實法說會 Golden Set | 6/6 passed |
| 前端 Production Build | passed |
| 財務 50 concurrent／100 requests | 100% HTTP 200、100% verified |
| 法說會 20 concurrent／50 requests | 100% HTTP 200、100% verified |
| 財務 100 concurrent／200 requests | 145 成功、55 個受控 503 |

上述結果證明目前測試範圍可重現，不代表所有公司、未知資料格式或任意正式硬體均能達到相同
結果。

## 目前 Readiness

目前本機狀態：

```text
status: evidence_only_ready
data_mode: local
answer_llm_ready: false
answer_mode: mock
```

代表：

- `retrieve_financial_evidence` 可提供已驗證財務 Evidence。
- `retrieve_earnings_call_evidence` 可提供已驗證逐字稿 Evidence。
- 本機 Mock Answer 可測試完整 UI、引用與拒答流程。
- 正式 `ask_*` 尚未經目標 LLM API 驗收。

## 正式交付前仍需完成

### 1. 正式 LLM API

- 提供 OpenAI-compatible Base URL、API Key 與 Model Name。
- 驗證 Timeout、Rate Limit、連線池與 concurrency。
- 重跑 Live LLM Golden Set、unsupported claim、prompt injection 與修復流程。

### 2. 正式 DB／API Mapping

- 取得實際 Schema 或 API Response 範例。
- 確認公司、期間、財務指標、值、單位、口徑、來源及版本欄位。
- 使用唯讀 Credential。
- 人工核准 Mapping 後才設定 `approved: true`。
- 重跑公司隔離、期間隔離、衝突、拒答與來源回查測試。

### 3. 正式安全部署

- HTTPS／TLS。
- OAuth/OIDC 或公司 IAM Gateway。
- Secret Manager 與 Token Rotation。
- Firewall／Network Allowlist。
- 內部 ports `8001`、`8002`、Neo4j、Ollama、DB 不對外開放。
- 正式 Tenant Authorization。

### 4. 正式容量驗收

- 定義 sustained RPS。
- 定義 burst concurrency。
- 定義 p95／p99 SLA。
- 定義可接受的 `503`、Timeout 與 Retry Policy。
- 使用正式 Server、LLM、DB、Neo4j、Ollama 與真實 Query Mix 壓測。
- 執行 30～60 分鐘以上 Soak Test。
- 高流量需求時加入多 Worker、水平擴展、Load Balancer 與集中 Rate Limit。

### 5. 監控與營運

- HTTP／MCP status 與 latency metrics。
- Queue 503、Provider timeout、refusal rate 告警。
- 集中式 Log 與 Trace ID 查詢。
- Backup／Restore 演練。
- Incident、Rollback、資料更新與版本發布流程。

### 6. Release 基線

- 整理並審核 Git 變更。
- 建立正式 Commit 與版本 Tag。
- 建立 CI 測試與 Build Gate。
- 保存部署 Artifact、OpenAPI、MCP Schema 與 Golden Set 結果。
- 建立 Change Log 與 Schema Deprecation Policy。

### 7. 本機一鍵啟停

預計提供：

- Neo4j Docker 啟動與停止。
- 本機 Ollama 健康檢查／必要時啟動。
- Embedding Model 存在性檢查。
- 前端、API、Internal MCP、Public MCP 背景啟動。
- PID、Log、Port 與健康狀態管理。
- 只停止本專案建立的程序，不誤停其他本機服務。

此腳本目前尚未完成，不能列為已交付項目。

## 可使用範圍判定

| 使用情境 | 目前判定 |
|---|---|
| 本機 UI 與完整流程展示 | 可以 |
| Evidence-only MCP 提供 Agent 使用 | 可以 |
| 小型內網試運行 | 可以，但需啟用認證並限制網路 |
| 接入正式 DB／API | Adapter 已預留，仍需實際 Schema Mapping 與驗收 |
| 正式 AI Answer MCP | 需接正式 LLM 並完成 Live LLM 驗收 |
| 大規模 Production | 需完成安全、監控、多副本與正式容量驗收 |

## 發布判定原則

只有在以下條件全部成立時，才能將狀態從 `evidence_only_ready` 提升為正式 `ready`：

1. 正式 LLM 或明確採用 Evidence-only 產品模式。
2. 正式資料來源 Mapping 與 Coverage 已核准。
3. 真實資料與 Live LLM Golden Set 通過。
4. HTTPS、IAM、Secret 與 Network Boundary 完成。
5. 正式環境負載與 Soak Test 達 SLA。
6. 監控、告警、Backup、Rollback 與 Incident 流程可操作。
7. Release Commit、Tag、Schema Snapshot 與測試報告可追溯。

核心 RAG 架構不需要重新設計；後續工作應集中在正式來源、正式模型與 Production
Hardening。

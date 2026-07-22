# Verified RAG 產品交付與就緒說明

> 文件層級：內部驗收附錄。正式產品範圍與發布基線請見 [PROJECT_SPEC.md](PROJECT_SPEC.md)。

- 文件版本：`1.3`
- 日期：`2026-07-23`
- 產品定位：可交付、可驗證、來源隔離的 Financial／Earnings Call RAG MCP

## 1. 已落地能力

| 能力 | 狀態 | 驗證方式 |
|---|---|---|
| MCP Runtime Output Schema | 已完成 | FastMCP output schema＋Pydantic runtime validation |
| 法說會 display | 已完成 | title、speaker、content、source content 一致性測試 |
| Evidence-only MCP | 已完成 | 不經答案 LLM，直接回傳 validated evidence |
| Company Entity Index | 已完成第一版 | exact、alias、ticker boundary、fuzzy candidate、Top-N LLM constraint |
| Period Resolver | 已完成第一版 | 明確季度、最近一季、上一季、去年同期 |
| Fiscal Calendar contract | 已完成 | 公司主檔表與 MCP 查詢契約 |
| 前端共用公開 MCP 路徑 | 已完成 | UI API 實際呼叫 8003／8004 |
| 財務／逐字稿隔離 | 已完成 | source-type allowlist 與 Golden Set |
| API backpressure | 已完成 | semaphore、queue timeout、HTTP 503 |
| LLM connection pooling | 已完成 | 共用 AsyncClient、連線上限、併發上限 |
| Embedding concurrency bound | 已完成 | bounded semaphore |
| 外部 SQL pool | 已完成 | pool size、overflow、timeout、pre-ping |
| 外部 JSON REST API Adapter | 已完成 | approval gate、固定 GET/mapping、scope refilter、provenance test |
| MCP private-server auth | 已完成 | optional static Bearer token、default-secret rejection |
| HTTP API／OpenAPI 文件 | 已完成 | hand-written guide＋generated `openapi.json` |
| Server deployment runbook | 已完成 | bind/client host、systemd、gateway、smoke test、rollback |
| Financial Data Schema v2 | 已完成 | raw payload、metric dictionary、provider mapping、exact facts、revision history |
| Dynamic metric-key normalization | 已完成 | nested keys、approval gate、unknown-key quarantine、REST adapter tests |
| Large metric-set selection | 已完成 | metric code、display name、Alias、provider key deterministic ranking |
| Backend regression | 已完成 | `85 passed`（2026-07-23） |
| 精簡法說會圖譜 | 已完成 | 講者與當時職稱保存在 SpeakerTurn，不建立全域 Speaker 節點 |

## 2. 公司解析保證

使用者不需在 UI 選擇公司。問題先對 Company Master 執行：

```text
正式名稱／Alias／股票代碼 exact match
  -> 高信心 fuzzy candidate
  -> Top-N candidates
  -> optional constrained LLM selection
  -> ambiguous／unknown 時追問
```

LLM 永遠只能從候選 Company Master 選擇，不得創造 `co_code`。短英文股票代碼使用 token boundary，避免 `AI` 誤命中 `said`。

正式大規模部署時，可替換 Company Entity Index 的底層實作為 PostgreSQL trigram、OpenSearch 或專用向量索引，而不改 MCP Tool contract。

## 3. 期間解析保證

支援：

- `2026 Q1`、`FY2026 Q1`
- `Q1 2026`
- `2026 年第一季`
- `最近一季`／`最新季度`
- `上一季`
- `去年同期`

相對期間不依系統日期猜測，而是從該公司「已驗證且實際存在」的 period catalog 解析。無法解析上一季或去年同期時不會退回無期間檢索，而是拒答。

Fiscal Calendar 欄位：

```text
co_code
fiscal_year_end_month
timezone
source
```

## 4. LLM 使用邊界

不需要正式 LLM API 的工具：

```text
retrieve_financial_evidence
retrieve_earnings_call_evidence
```

需要正式 LLM 才能完成生產驗收的工具：

```text
ask_financial_rag
ask_earnings_call
```

Mock 模式可驗證檢索、引用、格式、拒答與隔離，但不能代表正式模型的自然語言生成品質。正式發布 `ask_*` 前仍須用目標 LLM 重跑 Golden Set、prompt injection、unsupported-claim、timeout 與 rate-limit 測試。

目前 deterministic／Mock guardrail behavior set 為 `5/5`，包含最近一季、未來期間拒答、使用者 prompt injection、逐字稿來源邊界與多公司歧義。正式 LLM 到位後執行：

```bash
cd backend
../.venv/bin/python -m scripts.evaluate_llm_behavior --live-llm
```

## 5. 並行測試基準

本機單一 API worker、Mock answer LLM、公開 MCP HTTP 路徑的實測：

| 情境 | 結果 |
|---|---|
| 財務 20 concurrent／100 requests | 100% HTTP 200、100% verified、p95 `2118.03 ms` |
| 財務 50 concurrent／100 requests | 100% HTTP 200、100% verified、p95 `4307.70 ms` |
| 法說 10 concurrent／20 requests | 100% HTTP 200、100% verified、p95 `891.85 ms` |
| 財務 100 concurrent／200 requests | 135 成功、65 個受控 503；服務未失控 |

100 concurrent 測試證明 backpressure 正常，不代表目前單機已符合高流量 SLA。正式容量需要依部署硬體、Neo4j、Ollama、外部 DB/API 與正式 LLM 重新測量並設定目標。

執行壓測：

```bash
cd backend
../.venv/bin/python -m scripts.load_test \
  --concurrency 50 \
  --requests 100 \
  --query 'Microsoft 2026 Q1 revenue and gross margin?'
```

## 6. 前端資料路徑

目前前端流程：

```text
Frontend
  -> FastAPI /api/v1/chat/stream
  -> PublicMCPChatService
  -> ask_financial_rag (8003) 或 ask_earnings_call (8004)
  -> internal Knowledge／Finance MCP
```

因此 UI 人工測試與外部 Agent 使用相同的公開 MCP Tool。測試按鈕文字仍屬前端靜態設定；回答、display、citations、verification、period resolution 與 data versions 均來自 MCP 回應。

## 7. 外部來源與部署

Financial MCP 可選 SQLite、MariaDB-only 或合併已核准 SQL DB／JSON REST API 的 hybrid 模式。正式內部 DB 可選擇同步 schema/table/column/PK/index/FK catalog 至 Neo4j，但資料列不做 embedding；只有法說會原文切塊並 embedding。結構化財務數值以參數化 SQL 精確取得。Financial Data Schema v2 保存原始 Payload，透過 Metric Dictionary 與 Provider Mapping 將大量動態 Key 轉成具有 statement、duration、scope、unit、精確 decimal 與版本的 Facts。未知指標不會由模型猜測或直接回答。所有來源再轉成穩定 Evidence；供應商欄位不會滲入公開 MCP Schema。真實 MariaDB 上線仍需部署方提供唯讀連線、審核 mapping 並重跑 golden set。

Server 可以分開設定 `MCP_SERVER_HOST`（本機 Client 連線位址）與 `MCP_BIND_HOST`（監聽位址）。私人部署可啟用 `MCP_AUTH_MODE=static`；Internet-facing 部署仍須使用組織 OAuth/OIDC Gateway。完整操作見 `docs/DEPLOYMENT.md`。

## 8. 發布前仍需由部署方提供

- 正式 LLM model name 與 OpenAI-compatible API。
- 預期 sustained RPS、burst concurrency、p95／p99 SLA。
- 正式 IAM／tenant claim 規格。
- 需要支援的市場、交易所與 Company Master 規模。
- 多公司比較與多期間範圍查詢是否列入首版產品範圍。

在上述項目未確定前，可以交付 Evidence-only MCP 與目前資料範圍；不可宣稱 `ask_*` 已完成正式 LLM 或任意負載環境的生產驗收。

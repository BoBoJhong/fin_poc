# Verified Financial RAG MCP 專案規格

- 產品版本：`1.0.0`
- 公開 MCP Schema：`1.1`
- 最後更新：`2026-07-23`

這是本專案唯一的主要產品與工程規格。產品範圍、架構、啟動、資料接入、部署、可靠度、
併發與驗收都以本文件為入口。對外 MCP 串接與新增 MCP 分別由兩份專用規格管理；只有修改
底層欄位或排查實作時，才閱讀文末的內部技術附錄。

## 1. 產品是什麼

本專案提供來源可追溯、公司與期間受控的金融 RAG：

```text
使用者／外部 Agent
  -> HTTP API 或公開 MCP
  -> Company Resolver
  -> Period Resolver
  -> 財務或法說會隔離檢索
  -> Evidence 驗證
  -> 答案與 Citation 驗證
  -> answered／refused／needs_clarification
```

公開服務分成：

| 領域 | Endpoint | Tools |
|---|---|---|
| 財務與申報文件 | `http://<server>:8003/mcp` | `ask_financial_rag`、`retrieve_financial_evidence` |
| 法說會逐字稿 | `http://<server>:8004/mcp` | `ask_earnings_call`、`list_earnings_calls`、`retrieve_multi_period_earnings_call_evidence`、`get_earnings_call_transcript`、`retrieve_earnings_call_evidence`、`retrieve_earnings_call_blocks` |

ports `8001`、`8002` 是內部 Knowledge／Finance MCP，不提供給外部 Client。

## 2. 目前可交付範圍

已完成：

- 自然語言公司解析到 Company Master 的 `co_code`。
- 明確季度與「最近一季／上一季／去年同期」解析。
- 法說會問題使用 verified RAG；完整／最近一場內容使用依 event date 與 speaker turn 的確定性 reader。
- 財務與法說會來源隔離、引用、拒答與 Source Preview。
- Financial Schema v2、動態財務 Key、精確 Decimal、口徑與資料版本。
- 可切換 SQLite／MariaDB-only／hybrid 財務 repository、Neo4j、核准 SQL DB 與 JSON REST API Adapter。
- 內部 DB schema catalog 圖譜與公司主檔 mapping；只有法說會原文切塊並 embedding。
- Qwen `qwen3-embedding:0.6b` 檢索。
- 公開 MCP Runtime Schema `1.1`。
- Evidence-only 模式、併發限制與受控 `503`。

目前本機 Readiness 是 `evidence_only_ready`：Evidence MCP 可用，但正式 `ask_*` 上線前仍需
接入目標 LLM API 並重跑 Live LLM 評估。正式部署另需 HTTPS、IAM、監控、SLA 與正式資料
涵蓋範圍。

## 3. 本機啟動與人工測試

首次安裝：

```bash
cp .env.example .env
make setup
make run
```

服務：

| 服務 | URL |
|---|---|
| UI | <http://127.0.0.1:5173> |
| Swagger | <http://127.0.0.1:8000/docs> |
| Financial MCP | `http://127.0.0.1:8003/mcp` |
| Earnings Call MCP | `http://127.0.0.1:8004/mcp` |

建議人工案例：

```text
Microsoft 最近一季 revenue?
Microsoft 2026 Q1 法說會提到哪些需求？
取得微軟最近的法說會對話內容
Apple 2035 Q4 revenue?
```

前兩題應解析公司與期間並提供引用；不存在的未來期間應 `refused`，不得補造數據。

健康檢查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/readiness
```

## 4. 提供 MCP 給同事

### Server 最低設定

```dotenv
APP_ENV=production
MCP_SERVER_HOST=127.0.0.1
MCP_BIND_HOST=0.0.0.0
MCP_AUTH_MODE=static
MCP_SHARED_TOKEN=<long-random-secret>
```

只啟動後端服務：

```bash
make run-api
```

同事的概念設定：

```json
{
  "mcpServers": {
    "verified-financial-rag": {
      "transport": "http",
      "url": "https://<host>/<financial-mcp-path>",
      "headers": {"Authorization": "Bearer ${VERIFIED_RAG_MCP_TOKEN}"}
    },
    "verified-earnings-call": {
      "transport": "http",
      "url": "https://<host>/<transcript-mcp-path>",
      "headers": {"Authorization": "Bearer ${VERIFIED_RAG_MCP_TOKEN}"}
    }
  }
}
```

交付包只需要：

1. [MCP 對外交付規格](MCP_PROVIDER_HANDOFF_SPEC.md)。
2. [mcp-tools.json](mcp-tools.json)。
3. 正式 Financial／Earnings Call HTTPS URL。
4. 資料 Coverage、Rate Limit、SLA 與支援窗口。
5. 透過 Secret Manager 等獨立管道提供 Token／OAuth 資訊。

## 5. Agent 調用規則

| 問題 | 使用 Tool |
|---|---|
| 營收、EPS、毛利、現金流、財報 | `ask_financial_rag` |
| 管理層發言、展望、法說會 Q&A | `ask_earnings_call` |
| Agent 自行合成答案 | 對應的 `retrieve_*_evidence` |
| 財務數字＋管理層解釋 | 分別呼叫兩個 MCP，保留各自引用 |

輸入：

```json
{
  "query": "Microsoft 最近一季 revenue?",
  "co_code": null
}
```

一般不需傳 `co_code`；服務會從問題解析。Agent 使用答案前必須確認：

```text
status == answered
verified == true
verification.passed == true
citations.length > 0
```

收到 `refused` 時不得用模型記憶補答；收到 `needs_clarification` 時將
`clarification_question` 顯示給使用者後重試。

## 6. 接入正式 LLM、DB 或 API

### OpenAI-compatible LLM

```dotenv
COMPANY_LLM_MODE=openai_compatible
COMPANY_LLM_BASE_URL=https://llm.example.com/v1
COMPANY_LLM_API_KEY=<secret>
COMPANY_LLM_MODEL=<model-name>
```

正式 `ask_*` 發布前必須執行：

```bash
cd backend
../.venv/bin/python -m scripts.evaluate_llm_behavior --live-llm
```

### SQL DB

使用唯讀帳號，先探索 Schema、人工核准 Mapping，再設定 `approved: true`。Runtime 只允許
參數化 SELECT；不接受使用者或模型提供 SQL。完整操作見
[外部整合技術附錄](EXTERNAL_INTEGRATION_GUIDE.md)。

### JSON REST API

只允許設定檔核准的 HTTPS host、GET path 與參數。動態財務 Key 必須經過 Metric Dictionary
與 Provider Mapping；未知 Key 可保存，但不能直接成為可回答 Evidence。

所有外部資料最終都要轉成穩定的 `CompanySummary`、`FiscalCalendar`、`Evidence` 與
`SourcePreview`，不能讓供應商欄位改變公開 MCP Schema。

## 7. 加入其他 MCP

先選擇以下一種模式：

| 情境 | 作法 |
|---|---|
| Agent 只是同時使用另一個 MCP | 在 Agent Host 並列註冊，本專案不用修改 |
| 新 MCP 的資料要進入目前 RAG | 建立 Typed Adapter，轉成 Evidence 後再驗證 |
| 新增新聞、法規、ESG 等不同領域 | 建立獨立 Public Domain MCP |

本專案不是任意 MCP URL Proxy。要進入 Verified RAG 的外部 MCP 必須：

1. 固定 Server URL、認證與 Tool allowlist。
2. 保存 Input／Output JSON Schema。
3. 將回應轉成 `Evidence`／`SourcePreview`。
4. 正規化 `co_code`、period、source type、locator、hash、version。
5. 通過錯公司、錯期間、未來期間、來源衝突及 Timeout 測試。
6. 重新產生 `mcp-tools.json` 並執行 Golden Set。

詳細程式位置、命名衝突與驗收清單見 [新增外部 MCP 技術附錄](ADDING_EXTERNAL_MCP.md)。

## 8. 部署與安全

正式拓撲建議：

```text
MCP Client／Browser
  -> HTTPS Gateway／OAuth／Rate Limit
  -> FastAPI + Public MCP replicas
  -> private Internal MCP
  -> private DB／Neo4j／Ollama／external providers
```

最低要求：

- 外部只開放 API、8003 與 8004 的 Gateway Route。
- 8001／8002、DB、Neo4j、Ollama 留在私有網路。
- Internet-facing 使用組織 OAuth/OIDC；Static Token 只作私人部署基線。
- Secret 不寫入 Git、Mapping、Prompt 或文件。
- 設定 timeout、connection pool、bounded concurrency、retry＋jitter。
- 監控 HTTP status、domain status、p95/p99、503、provider timeout、refusal rate 與 trace ID。

systemd、Nginx、Firewall、Rollback 與發布驗收見 [部署技術附錄](DEPLOYMENT.md)。

## 9. 併發與容量邊界

目前本機單 Worker、Mock LLM 實測可支援 50 burst concurrency／100 requests 全部成功，
但 p95 約數秒；100 burst concurrency 會受控回部分 `503`。這證明 backpressure 有效，
不代表正式環境已符合任意高流量 SLA。

正式容量必須用實際 Server、正式 LLM、正式 DB/API 與真實 query mix 重跑，並先定義：

- sustained RPS；
- burst concurrency；
- p95／p99；
- 可接受的 503／timeout；
- Provider Rate Limit。

需要更高容量時，API 與各 Public MCP 分別水平擴展，並由 Gateway 執行集中 Rate Limit。

## 10. 測試與發布

基本驗證：

```bash
make test
make export-api
make export-mcp
npm --prefix frontend run build
```

真實資料評估：

```bash
cd backend
../.venv/bin/python -m scripts.evaluate_sec
../.venv/bin/python -m scripts.evaluate_transcripts
```

負載測試：

```bash
cd backend
../.venv/bin/python -m scripts.load_test \
  --concurrency 50 --requests 100 \
  --query 'Microsoft 2026 Q1 revenue?'
```

公開 Tool／Schema、資料 Mapping、模型或 Prompt 變更都必須重跑對應 Golden Set。測試通過
只代表覆蓋案例可重現，不代表所有公司與未知資料格式永遠零錯誤。

## 11. 技術附錄

一般使用者不需要全部閱讀。只有遇到對應工作時再開啟：

| 附錄 | 何時需要 |
|---|---|
| [完整文件地圖](README.md) | 查找所有文件或確認同步關係 |
| [Architecture](../ARCHITECTURE.md) | 修改元件、資料流或安全邊界 |
| [MCP 對外交付規格](MCP_PROVIDER_HANDOFF_SPEC.md) | 提供 MCP 給串接團隊 |
| [完整 MCP Output Spec](VERIFIED_RAG_MCP_OUTPUT_SPEC.md) | 修改或驗證 Response 欄位 |
| [MCP Schema 變更指南](MCP_API_DESIGN_AND_CHANGE_GUIDE.md) | 修改 Tool／Schema／版本 |
| [Financial Schema v2](FINANCIAL_DATA_SPEC.md) | 接入或映射財務指標 |
| [內部資料庫快速接入](INTERNAL_DATABASE_QUICKSTART.md) | 重現專案、串接 MariaDB、匯入法說會與使用小模型 prompts |
| [外部整合](EXTERNAL_INTEGRATION_GUIDE.md) | 接 LLM、SQL DB、REST API |
| [新增外部 MCP](ADDING_EXTERNAL_MCP.md) | 接入第三方 MCP |
| [Deployment](DEPLOYMENT.md) | 正式部署與維運 |
| [Configuration](CONFIGURATION.md) | 查環境變數 |
| [Product Readiness](PRODUCT_READINESS.md) | 判斷發布限制與測試基準 |
| [HTTP API Reference](API_REFERENCE.md) | 串接 REST／SSE |
| [openapi.json](openapi.json) | HTTP Client／Gateway 機器 Schema |
| [mcp-tools.json](mcp-tools.json) | MCP Client 機器 Schema |

# Verified Financial RAG MCP

可交付、可驗證、來源隔離的金融與法說會 RAG 產品。React UI 與外部 Agent 共用公開
Financial／Earnings Call MCP；所有回答都必須通過公司、期間、來源、引用與內容驗證。

本專案包含：

- 自然語言 Company Entity Resolver，輸出資料庫使用的股票代碼 `co_code`。
- Fiscal Calendar 與「最近一季／上一季／去年同期」Period Resolver。
- 可切換 SQLite／MariaDB-only／hybrid 財務 Repository，以及 Neo4j 法說會 GraphRAG。
- Financial Data Schema v2：Raw Payload、Metric Dictionary、Provider Mapping、精確 Financial Facts。
- Ollama `qwen3-embedding:0.6b` 向量檢索。
- OpenAI-compatible LLM 答案生成，或不需 LLM 的 Evidence-only 模式。
- 財務與法說會兩個隔離的公開 MCP，以及統一 Runtime Schema `1.1`。
- FastAPI、SSE、來源回查、併發限制、連線池與 backpressure。

本專案的產品、架構、啟動、資料接入、部署與驗收已統整在
[專案規格](docs/PROJECT_SPEC.md)。提供 MCP 給同事或新增其他 MCP 時，使用
[三份正式規格總覽](docs/README.md)。

目前完成項目、驗證基準與正式上線缺口請見
[產品就緒說明](docs/PRODUCT_READINESS.md)。

## 快速開始

需求：Python 3.11+、Node.js 20+。`DATA_MODE=local` 另需 Neo4j、Ollama 及資料庫。

```bash
cp .env.example .env
make setup
make run
```

預設 `.env.example` 是 Mock／本機開發設定。

| 服務 | URL |
|---|---|
| Chat UI | <http://127.0.0.1:5173> |
| Swagger API | <http://127.0.0.1:8000/docs> |
| OpenAPI JSON | <http://127.0.0.1:8000/openapi.json> |
| Internal Knowledge MCP | `http://127.0.0.1:8001/mcp` |
| Internal Finance MCP | `http://127.0.0.1:8002/mcp` |
| Public Financial MCP | `http://127.0.0.1:8003/mcp` |
| Public Earnings Call MCP | `http://127.0.0.1:8004/mcp` |

`Ctrl+C` 會停止整套本機服務。只啟動後端可執行：

```bash
make run-api
```

## 使用方式

HTTP API：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"Microsoft 最近一季 revenue?"}'
```

使用者不需先選公司。系統會從 Company Master 的正式名稱、Alias 與股票代碼解析
`co_code`；未知或多義公司會要求補充。相對期間依該公司實際存在且已驗證的期間解析，
不以當天日期猜測。

公開 MCP：

| Endpoint | Tools | Data boundary |
|---|---|---|
| port 8003 | `ask_financial_rag`, `retrieve_financial_evidence` | 財務 DB/API、SEC、官方財務文件 |
| port 8004 | `ask_earnings_call`, `list_earnings_calls`, `retrieve_multi_period_earnings_call_evidence`, `get_earnings_call_transcript`, `retrieve_earnings_call_evidence`, `retrieve_earnings_call_blocks` | 法說會逐字稿 only |

單一 Agent 已足夠。純財務或逐字稿問題呼叫對應工具；混合問題分別呼叫兩個工具後，
保留各自引用再合併。收到 `refused` 時不得用模型記憶補答。
「最近幾季」先呼叫 `list_earnings_calls`，再以
`retrieve_multi_period_earnings_call_evidence` 取得逐季隔離的證據。重點型問題會涵蓋營運、
策略、展望／風險及 Q&A 四個面向；此輸出是可引用的廣度檢索，不宣稱等同完整逐字稿摘要。
若要多季完整逐字稿，Agent 先列出季度，再對每季分別呼叫
`get_earnings_call_transcript`，並讀取至各自的 `next_cursor` 為 `null`；不把多季數十萬字
塞進單一 MCP response。

## 資料模式

### Mock Demo

```dotenv
DATA_MODE=mock
COMPANY_LLM_MODE=mock
MCP_AUTH_MODE=none
```

用來驗證 UI、API、MCP 契約與拒答流程，不代表正式模型品質。

### Local／Production-like

```dotenv
DATA_MODE=local
SQLITE_PATH=data/local/financial.sqlite3
SQLITE_READ_ONLY=true
ALLOWED_CO_CODES=*
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_EMBEDDING_MODEL=qwen3-embedding:0.6b
```

```bash
ollama pull qwen3-embedding:0.6b
cd backend
../.venv/bin/python -m scripts.init_sqlite              # 既有 DB：建立/遷移 v2 tables
# 全新 Demo DB 才使用：../.venv/bin/python -m scripts.init_sqlite --seed-demo
../.venv/bin/python -m scripts.init_data
```

正式答案 LLM：

```dotenv
COMPANY_LLM_MODE=openai_compatible
COMPANY_LLM_BASE_URL=https://llm.example.com/v1
COMPANY_LLM_API_KEY=replace-me
COMPANY_LLM_MODEL=deployed-model-name
```

沒有正式 LLM 時，`retrieve_financial_evidence` 與
`retrieve_earnings_call_evidence`、簡潔逐字稿 reader `get_earnings_call_transcript` 與詳細巢狀
JSON `retrieve_earnings_call_blocks` 仍可交付外部 Agent 使用；`/health/readiness` 會誠實回報
`evidence_only_ready`。

## 外部資料接入

### 內部／外部 SQL DB

```bash
export INTERNAL_FINANCE_DATABASE_URL='mariadb+pymysql://readonly:secret@host/db?charset=utf8mb4'
cd backend
../.venv/bin/python -m scripts.discover_database \
  --url-env INTERNAL_FINANCE_DATABASE_URL \
  --database-id internal_finance_db \
  --output ../data/local/db-schema.json \
  --config-output ../config/external_databases.local.json
```

人工確認 Mapping 後才能將 dataset 設為 `approved: true`。
正式 DB-only 財務模式設 `FINANCE_REPOSITORY_MODE=external`；完整 schema 圖、法說會 embedding、
MCP 流程與小模型 prompt 見 [內部資料庫快速接入](docs/INTERNAL_DATABASE_QUICKSTART.md)。

### 外部 JSON REST API

```bash
cp config/external_apis.example.json config/external_apis.local.json
```

```dotenv
EXTERNAL_API_CONFIG_PATH=config/external_apis.local.json
EXTERNAL_API_STRICT=false
VENDOR_FINANCE_API_KEY=replace-me
```

API Provider 必須為已核准的唯讀 GET JSON 端點。Adapter 會限制 host/path、timeout、連線數、
回應大小與 `co_code`/period，並支援 row-based 及巢狀動態 Key 財報。未知 Key 會被保留及回報，
但未經 Metric Dictionary/Provider Mapping 核准前不會成為可回答 Evidence。
完整欄位 Mapping 與驗收方式見
[外部整合指南](docs/EXTERNAL_INTEGRATION_GUIDE.md)。

## Server 部署

私人 Server 最低建議：

```dotenv
APP_ENV=production
API_HOST=0.0.0.0
MCP_SERVER_HOST=127.0.0.1
MCP_BIND_HOST=0.0.0.0
MCP_AUTH_MODE=static
MCP_SHARED_TOKEN=<long-random-secret>
CORS_ORIGINS=https://rag.example.com
```

```bash
.venv/bin/python backend/scripts/run_local.py --no-frontend
```

ports 8001／8002 僅供內部使用。公開 MCP 必須放在私人網路、VPN 或驗證 Gateway 後方；
Static token 是私人部署基線，Internet-facing 正式環境應使用組織 OAuth/OIDC。
systemd、Nginx、Firewall、Smoke test、監控與 Rollback 步驟見
[Server Deployment Guide](docs/DEPLOYMENT.md)。

## 測試與驗收

```bash
make verify

cd backend
../.venv/bin/python -m scripts.evaluate_sec
../.venv/bin/python -m scripts.evaluate_transcripts
../.venv/bin/python -m scripts.evaluate_llm_behavior
```

正式 LLM 上線前另執行：

```bash
cd backend
../.venv/bin/python -m scripts.evaluate_llm_behavior --live-llm
```

負載測試：

```bash
cd backend
../.venv/bin/python -m scripts.load_test \
  --concurrency 50 --requests 100 \
  --query 'Microsoft 2026 Q1 revenue?'
```

所有資料更新、模型變更、Mapping 修改或 MCP Schema 修改，都必須重新跑完整測試與 Golden
Sets。測試通過只代表涵蓋案例可重現，不等於所有公司或未知資料格式百分之百正確。

## 文件索引

| 文件 | 用途 |
|---|---|
| [專案規格](docs/PROJECT_SPEC.md) | 產品、架構、啟動、資料接入、部署、可靠度與驗收 |
| [三份正式規格總覽](docs/README.md) | 專案規格、同事 MCP 規格、新增 MCP 規格 |
| [MCP 對外交付規格](docs/MCP_PROVIDER_HANDOFF_SPEC.md) | 直接提供給同事／外部 Agent |

## 主要程式位置

| Responsibility | Path |
|---|---|
| HTTP API | `backend/app/main.py` |
| Public MCP contracts | `backend/app/mcp_contracts.py` |
| Public MCP tools | `backend/mcp_servers/rag.py`, `transcript.py` |
| Company/period resolution | `backend/app/company_resolver.py`, `period_resolver.py` |
| SQLite/Neo4j repositories | `backend/app/repositories.py` |
| Financial Schema/normalizer | `backend/app/financial_data.py` |
| External SQL adapters | `backend/app/database_connectors.py` |
| External REST adapter | `backend/app/external_api_connectors.py` |
| LLM adapter | `backend/app/llm.py` |
| Runtime configuration | `backend/app/config.py`, `.env.example` |

## 安全原則

- `.env`、外部 DB/API local registry 與公司私密規格不可提交版控。
- 不接受使用者或模型提供任意 SQL、Cypher、URL 或 API parameter。
- 公司與期間必須在檢索前確定；Evidence 回來後再次驗證。
- Financial MCP 與 Transcript MCP 的來源型別白名單不可互換。
- Answer、Citation、Source Preview 與 data version 必須能互相回查。
- 正式環境以 IAM claim 決定 tenant/company scope，不能只信任 `X-User-Id`。

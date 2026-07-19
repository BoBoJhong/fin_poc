# Financial GraphRAG MCP PoC（純本機版）

這是一套可直接在本機執行的金融問答 PoC：React Chatbot 經 FastAPI／SSE 呼叫 LangGraph，Main Agent 只會路由到 Knowledge／GraphRAG 與 Finance DB 兩個受控 Subagent；工具透過本機 MCP 呼叫 Neo4j GraphRAG 與 SQLite，Ollama Qwen 只負責 Embedding，公司 LLM API 負責路由、Evidence 驗證與答案生成。所有答案均帶 Citation、來源定位與 Trace ID。

本專案不需要 Docker，也不會在 Chat Runtime 連線 MariaDB。既有 MariaDB 資料請先放在本機 SQLite；公司 API／DB 規格文件只作為開發 Mapping 參考，不進入 Runtime RAG。

## 1. 系統需求

- Python 3.11+
- Node.js 20+
- 本機 Neo4j（Live 模式需要，Bolt 預設 `127.0.0.1:7687`）
- 本機 Ollama 與 Qwen Embedding（Live 模式需要，預設 `127.0.0.1:11434`）
- 已存在的本機 SQLite；若只跑 Demo 可用初始化指令建立

## 2. 第一次安裝

在專案根目錄執行：

```bash
cp .env.example .env
python -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e './backend[dev]'
npm --prefix frontend ci
```

Windows PowerShell 請將 `.venv/bin/python` 換成 `.venv/Scripts/python.exe`。

## 3. 立即執行 Demo

`.env` 保持 `DATA_MODE=mock`、`COMPANY_LLM_MODE=mock` 與
`ALLOWED_CO_CODES=*`，然後：

```bash
.venv/bin/python backend/scripts/run_local.py
```

- Chat UI：<http://127.0.0.1:5173>
- FastAPI Swagger：<http://127.0.0.1:8000/docs>
- Knowledge MCP：<http://127.0.0.1:8001/mcp>
- Finance MCP：<http://127.0.0.1:8002/mcp>

`Ctrl+C` 會一起停止四個本機 Process。

## 4. 使用既有 SQLite 與本機 GraphRAG

1. 將 `.env` 改為 `DATA_MODE=local`。
2. `SQLITE_PATH` 指向你已經抓好的 SQLite 檔案；相對路徑以專案根目錄為準。
3. 若公司範圍是整份公司主檔，設定 `ALLOWED_CO_CODES=*`；若需限制授權範圍，則填入逗號分隔的股票代碼。另需設定 Neo4j、Ollama 與公司 LLM API。
4. 確認 SQLite 至少有 `companies`、`data_sources`、`financial_metrics` 三張表；若實際 Schema 不同，只需修改 `SQLiteFinanceRepository` Adapter。
5. 若 Neo4j 尚未建立 Demo Graph，可在 Ollama 與 Neo4j 已啟動後執行初始化。

```bash
ollama pull qwen3-embedding:0.6b
.venv/bin/python -m scripts.init_data
.venv/bin/python backend/scripts/run_local.py
```

執行 `python -m scripts.init_data` 時目前目錄需為 `backend/`；或使用：

```bash
cd backend
../.venv/bin/python -m scripts.init_data
```

公司 LLM 若相容 OpenAI Chat Completions，設定：

```dotenv
COMPANY_LLM_MODE=openai_compatible
COMPANY_LLM_BASE_URL=https://your-company-api.example/v1
COMPANY_LLM_API_KEY=replace-me
COMPANY_LLM_MODEL=your-model
```

## 5. 建立 Demo SQLite（不會覆蓋既有資料）

只有在沒有 SQLite 時才需要：

```bash
cd backend
../.venv/bin/python -m scripts.init_sqlite --seed-demo
```

若你已有資料，請不要執行這一步，直接設定 `SQLITE_PATH`。

## 6. API

```bash
curl http://127.0.0.1:8000/api/v1/companies

curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'X-User-Id: poc-user' \
  -d '{"query":"範例科技 2026 Q2 營收和主要風險？"}'
```

來源回查：

```bash
curl 'http://127.0.0.1:8000/api/v1/sources/demo01-financial-metrics-2026q2?co_code=DEMO01' \
  -H 'X-User-Id: poc-user'
```

公司範圍由問題中的正式名稱、簡稱、Alias 或股票代碼解析。模型只能從本機
Company Master 選擇既有 `co_code`，不可自行創造代碼；未提及公司或同時命中
多家公司時，API 會回傳 `422` 要求使用者補充。舊版 `X-Co-Code` 與 body
`co_code` 仍可作為相容用的預設值，新版前端不再傳送。

## 7. 驗證

```bash
cd backend
../.venv/bin/pytest -q
../.venv/bin/python -m scripts.evaluate
../.venv/bin/python -m scripts.evaluate --data-mode local
cd ../frontend
npm run build
```

`--data-mode local` 會直接使用本機 SQLite、Neo4j 與設定的 Ollama Embedding
執行同一份 Golden Set，可用來確認正式 repository 的檢索、公司隔離、引用與拒答流程。

### 可靠度門檻

正式模式會先以 `co_code` 限縮向量候選，再用全文檢索訊號重新排序；全文結果
不會繞過公司範圍或向量相關度門檻。以下設定控制拒答政策：

```dotenv
DOCUMENT_MIN_RELEVANCE_SCORE=0.60
GRAPH_MIN_RELEVANCE_SCORE=0.70
MAX_EVIDENCE_ITEMS=8
REQUIRE_DOCUMENT_PROVENANCE=true
HYBRID_VECTOR_WEIGHT=0.75
```

系統只接受具有來源定位、內容雜湊、正確公司與期間的文件 Evidence。回答需通過
引註編號、數字、逐主張文字支持與模型語意驗證；任一必要關卡失敗即修復或拒答。
API 回應的 `verification.reliability_policy` 會列出各項 Gate。這些防線能大幅降低
錯誤回答，但仍須使用公司的真實 Golden Set 驗證，不能視為零風險保證。

## 8. 主要替換點

| 需求 | 檔案 |
| --- | --- |
| SQLite Schema／欄位 Mapping | `backend/app/repositories.py` |
| 公司 LLM API Contract | `backend/app/llm.py` |
| Neo4j Label／Relationship／Cypher | `backend/app/repositories.py`、`backend/scripts/init_data.py` |
| Agent Route 與驗證流程 | `backend/app/agents.py` |
| API／IAM Header | `backend/app/main.py` |
| MCP Tool Contract | `backend/mcp_servers/` |

完整設計、初版差異與開發銜接請見 [ARCHITECTURE.md](ARCHITECTURE.md)。

公司機密規格不需要放入本專案。請複製
`docs/PRIVATE_SPEC_REFERENCES.example.md` 為 `docs/PRIVATE_SPEC_REFERENCES.md`，只填公司內部文件編號、版本與程式替換點；實際私密引用檔已加入 `.gitignore`。

## 9. 已知邊界

- `X-User-Id`／`X-Co-Code` 是 PoC Scope，正式環境需換成公司 IAM Claim。
- `ALLOWED_CO_CODES` 必須與 SQLite／Neo4j 的 `co_code` 一致。
- 不接受模型生成的任意 SQL 或 Cypher。
- Live URL 可能禁止 iframe；Source Inspector 仍以快照、段落、Graph Path 或 SQLite Record 為稽核依據。
- Forecast 與 PowerPoint 未納入核心 PoC。

# 公司內部資料庫快速接入

## 目標架構

```text
財務數字 → MariaDB 即時精確查詢
法說會原文 → Neo4j 圖譜與向量檢索
自然語言入口 → Financial / Earnings Call MCP
```

本專案的正式原則是：**只有法說會原文做 embedding**。MariaDB 的公司主檔與財務數值保留在
MariaDB，不複製成向量。

## 1. 設定唯讀連線

請先建立只有必要資料表 `SELECT` 權限的 MariaDB 帳號，再將連線資訊放入專案根目錄 `.env`：

```dotenv
DATA_MODE=local
FINANCE_REPOSITORY_MODE=external
EXTERNAL_DATABASE_STRICT=true
EXTERNAL_DATABASE_CONFIG_PATH=config/external_databases.local.json

INTERNAL_FINANCE_DATABASE_URL=mariadb+pymysql://readonly_user:password@db-host:3306/finance?charset=utf8mb4
```

`.env` 只提供連線能力，不會立即掃描或 embedding 整個資料庫。

## 2. 探索資料庫欄位

```bash
cd backend

../.venv/bin/python -m scripts.discover_database \
  --url-env INTERNAL_FINANCE_DATABASE_URL \
  --database-id internal_finance_db \
  --output ../data/local/internal-db-schema.json \
  --config-output ../config/external_databases.local.json
```

輸出內容：

- `data/local/internal-db-schema.json`：Schema、Table、Column、PK、FK、Index。
- `config/external_databases.local.json`：系統建議的欄位 mapping。

## 3. 確認結構化欄位 Mapping

開啟 `config/external_databases.local.json`，確認：

- `datasets`：公司、季度、財務指標、數值與單位。
- `company_datasets`：公司代碼、名稱、別名、產業與會計年度。

確認正確的項目才改成：

```json
"approved": true
```

這一步不是挑選 embedding 欄位，而是告訴唯讀 SQL Adapter「公司代碼、季度、指標與數值在
哪個欄位」。不同公司的資料庫命名可能不同，因此第一次仍需確認。若公司能提供固定的標準
View，mapping 只需設定一次。

若內部 DB 將年度與季度分成兩個欄位，不需修改 DB；由 Adapter 組成標準
`YYYYQn` period：

```json
{
  "company_code": "CO_CD",
  "period": {
    "type": "year_quarter",
    "year_column": "FISCAL_YEAR",
    "quarter_column": "FISCAL_QUARTER"
  },
  "metric": "ITEM_CODE",
  "value": "ITEM_VALUE"
}
```

`FISCAL_YEAR=2025` 與 `FISCAL_QUARTER=Q3` 會正規化為 `period=2025Q3`。舊有
`"period": "FISCAL_PERIOD"` 單欄 Mapping 仍相容。原始欄位名稱與值會保留在
`source_period`，供 Evidence 追溯與除錯。年度非四位西元年，或季度不是 `Q1`～`Q4`（也接受
`1`～`4`）時，該資料會被拒絕，不會猜測期間。

公開 MCP 已能將「微軟 2025 Q3 法說會內容」解析為 `MSFT + 2025Q3 +
transcript`。這只代表查詢範圍可正確建立；要實際回答，Neo4j 必須已匯入
`co_code=MSFT`、`period=2025Q3`、`source_type=transcript` 的 Chunk。若該季法說會尚未
匯入，MCP 應回傳 `refused` 或 `needs_clarification`，不可用模型記憶補答。

本專案目前不使用公司資料庫的文字做 embedding，因此保持：

```json
"narrative_datasets": []
```

## 4. 選擇性同步資料庫結構

```bash
../.venv/bin/python -m scripts.sync_internal_database \
  --database-id internal_finance_db \
  --schema-only
```

這只會將資料庫的 Schema、Table、Column、PK、FK 與 Index 記錄成管理用圖譜，不會讀取財務
資料列，也不會 embedding。若目前不需要在 Neo4j 查看資料庫結構，這一步可以不執行。

## 5. 法說會原文寫入 Neo4j

法說會 ingestion 才會執行切塊與 embedding：
完整的原文解析、Block `1400/160` 參數、Embedding HTTP 契約、重跑與 Neo4j
upsert 行為見 [法說會 Embedding 與 Block 實作作業規格](TRANSCRIPT_EMBEDDING_OPERATIONS.md)。

```bash
cd backend

../.venv/bin/python -m scripts.ingest_transcripts \
  --sources <已設定的法說會來源代碼>
```

可接受的純文字格式包括：

```text
王小明：內容
王小明: 內容
[王小明] 內容
王小明（執行長）：內容

Speaker: 王小明
Title: 執行長
Content: 內容
```

## Neo4j 法說會圖譜規格

```text
(:Company {co_code, name})
  ├─[:HAS_EARNINGS_CALL]→ (:EarningsCall:Document)
  └─[:HAS_DOCUMENT]─────→ (:EarningsCall:Document)

(:EarningsCall)
  ├─[:HAS_TURN]───────→ (:SpeakerTurn)
  └─[:HAS_CHUNK]──────→ (:Chunk)

(:Chunk)-[:CONTAINS_TURN]──────→(:SpeakerTurn)
```

### 要加入圖譜的內容

| 節點 | 必要內容 | 用途 |
|---|---|---|
| `Company` | `co_code`、`name` | 一家公司對應多場法說會 |
| `EarningsCall:Document` | `source_id`、`co_code`、`period`、`fiscal_label`、`event_date`、`title`、`content_hash`、`data_version` | 定義一場法說會與來源版本 |
| `SpeakerTurn` | `turn_id`、`sequence`、`speaker`、`speaker_title`、`section`、`text` | 保存該場講者、當時職稱與依序可還原的逐字內容 |
| `Chunk` | `chunk_id`、`source_id`、`co_code`、`period`、`speakers`、`section`、`text`、`embedding`、`content_hash` | RAG 向量與全文檢索單位 |

### 不加入法說會向量圖譜的內容

- MariaDB 的營收、EPS、毛利率或其他財務數值。
- 整張公司資料表或所有欄位。
- 密碼、連線字串、個資或未核准內容。
- 模型自行推測的講者職稱、季度或摘要。

法說會完整逐字稿由 `SpeakerTurn` 還原；RAG 搜尋使用 `Chunk.embedding`。因此 Chunk 可以重新
切分或更新，而不影響原始逐字稿順序。講者不建立全域人物節點，避免換人、同名及跨季度職稱
變化造成額外身分維護。

## 6. 啟動與查詢

服務啟動後，Agent 可用自然語言透過 MCP 查詢：

```text
「微軟 FY2026 Q2 營收多少？」
→ MariaDB

「微軟最近法說會對 AI 需求怎麼說？」
→ Neo4j

「比較微軟近三季營收與管理層展望」
→ MariaDB + Neo4j
```

### 內部 MCP 是否必要

- 單機或同一個 Process：可設 `MCP_ENABLED=false`，直接呼叫 Repository。
- 正式多服務環境：建議設 `MCP_ENABLED=true`，用內部 Finance／Knowledge MCP 隔離資料庫憑證、
  網路權限、Timeout 與稽核。

內部 MCP 是部署與安全邊界，不是 RAG 正確性的必要條件；外部 Agent 只需使用 Public
Financial／Earnings Call MCP。

## 更新資料

MariaDB 財務查詢是即時讀取，不需要 embedding。新增或修改法說會原文後，應由人工或排程重新
執行 transcript ingestion：

```bash
../.venv/bin/python -m scripts.ingest_transcripts \
  --sources <法說會來源代碼>
```

相同 `source_id`、`turn_id` 與 `chunk_id` 會更新既有節點；同一場法說會已不存在的舊 Turn 與
Chunk 會在重新匯入時移除，不會一直累積重複內容。但現行程式每次仍會
重新計算指定來源的全部 embedding，尚未依 `content_hash` 跳過未變 Block。

## 小模型友善 Prompt

不要要求內部模型自己產生 SQL、Cypher 或判斷資料庫欄位。程式先完成公司、季度與工具路由，
再把已取得的 Evidence 交給模型。以下 Prompt 一次只負責一件事。

### 1. 問題分類

```text
判斷問題類型，只能選一個：
- finance：詢問財務數字
- earnings_call：詢問法說會內容或講者
- full_transcript：要求完整逐字稿
- mixed：同時詢問財務數字與法說會內容

問題：{query}

只輸出一個 JSON，不要說明。例如：
{"task":"finance"}
```

公司或季度沒有出現在問題中時，不要讓模型猜測；由程式回傳 `needs_clarification`。

### 2. 財務資料回答

```text
你只能使用下方資料回答，不可補充或猜測數字。

問題：{query}
資料：{database_evidence_json}

規則：
1. 回答公司、季度、指標、數值與單位。
2. 資料沒有答案時回答「資料不足」。
3. 保留 source_id、table、primary_key、data_version。

只輸出 JSON：
{"answer":"...","source_ids":["..."]}
```

### 3. 法說會內容回答

```text
你只能根據法說會證據回答，不可加入外部知識。

問題：{query}
法說會證據：{transcript_evidence_json}

規則：
1. 說明是哪一季法說會。
2. 有講者時保留講者姓名與當時職稱。
3. 證據沒有答案時回答「法說會未提及」。
4. 保留 source_id 與引用段落。

只輸出 JSON：
{"answer":"...","source_ids":["..."]}
```

### 4. 財務與法說會混合回答

```text
根據兩組資料回答，不可自行計算缺少的數字或補充外部資訊。

問題：{query}
財務資料：{database_evidence_json}
法說會證據：{transcript_evidence_json}

先回答財務數字，再回答管理層說法。資料不足時明確說明。

只輸出 JSON：
{
  "financial_answer":"...",
  "earnings_call_answer":"...",
  "source_ids":["..."]
}
```

### 5. 完整逐字稿

完整逐字稿不交給模型生成。程式直接呼叫 `get_earnings_call_transcript`，依 `next_cursor` 讀到
`null`，並原樣回傳 `speaker`、`title`、`content`，避免小模型摘要、遺漏或改寫原文。

## 重現完成檢查表

- [ ] `.env` 已設定唯讀 MariaDB 與 Neo4j/Ollama 連線。
- [ ] 已執行 schema discovery。
- [ ] 財務與公司主檔 mapping 已人工核准。
- [ ] `narrative_datasets` 保持空陣列。
- [ ] 法說會已完成 ingestion，Neo4j 中存在 EarningsCall、SpeakerTurn 與 Chunk。
- [ ] 財務問題可取得 MariaDB table/primary key 回溯資訊。
- [ ] 法說會問題可取得 source_id、季度與引用段落。
- [ ] 完整逐字稿已測試分頁讀到 `next_cursor = null`。
- [ ] 混合問題已確認同時包含 database 與 transcript 證據。

更完整的法說會節點與索引契約，請見
[Neo4j Earnings-Call Graph](NEO4J_EARNINGS_CALL_GRAPH.md)。

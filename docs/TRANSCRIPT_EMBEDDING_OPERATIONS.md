# 法說會 Embedding 與 Block 實作作業規格

> 文件性質：內部實作與營運附錄。本文依目前程式行為撰寫，用於把公司
> 法說會純文字導入 Neo4j，並建立可供 RAG 使用的 `Chunk.embedding`。

## 1. 目前實作邊界

目前正式原則是：

- MariaDB 的公司主檔、季度、營收、EPS 與其他結構化財務數值不做 embedding；
  查詢時使用參數化 SQL 精確讀取。
- 只有法說會原文會被解析、切成 Block，並將向量寫入 Neo4j `Chunk`。
- `SpeakerTurn` 保存按發言順序的原文；`Chunk` 是 RAG 的檢索單位。兩者不可混為
  同一個用途。
- 現行 ingestion 入口是 `backend/scripts/ingest_transcripts.py`。來源必須先登錄在
  該檔案的 `SOURCES`；CLI 會根據 `url` 下載內容。
- 雖然已有 `plain_text` Adapter，但目前尚未實作「從內部 MariaDB 法說會表自動抽取原文
  並直接呼叫 transcript ingestion」的 DB Adapter。導入內部 DB 前仍需增加一個讀取層，
  將每筆法說會轉成本文第 2 節的來源資料。
- 目前 Embedding Provider 僅實作 Ollama `/api/embed` 契約，沒有外部 GW 的認證、重試、
  rate limit 與批次 Adapter。若 GW 不是相同契約，必須先實作 Provider Adapter。

## 2. 一場法說會的必要來源資料

每場法說會在導入前必須能組成下列資料：

| 欄位 | 範例 | 用途 |
|---|---|---|
| `source_key` | `msft-fy2026-q2` | 來源穩定識別字，不可因標題改名 |
| `co_code` | `MSFT` | 連到公司主檔，也是檢索強制篩選條件 |
| `company_name` | `Microsoft Corporation` | `Company.name` |
| `period` | `2025Q4` | 系統內部標準期間 |
| `fiscal_label` | `FY2026 Q2` | 公司對外會計期間 |
| `event_date` | `2026-01-28` | 判斷最新法說會，不可用當天日期猜測 |
| `title` | `Microsoft FY2026 Q2 ...` | 法說會文件標題，不是講者職稱 |
| `url` | 官方 IR URL | 下載與追溯來源；內部 DB 可改為內部 locator |
| `adapter` | `plain_text` | 決定如何將原文解析成 speaker turns |
| `material_kind` | `full_transcript` | 防止把摘要當成完整逐字稿 |
| `speaker_titles` | `{"AMY HOOD":"EVP & CFO"}` | 僅來源確實提供時作為職稱補充 |

程式產生：

```text
source_id   = "ir-" + source_key + "-transcript"
data_version = "ir:" + lower(co_code) + ":" + event_date
```

`source_id` 是 Document 的唯一鍵。同一場法說會重跑時必須使用同一個
`source_key`，否則系統會把它視為另一場法說會。

## 3. 原文取得與快照

`ingest()` 依序處理 `--sources` 指定的來源：

1. 以 HTTP GET 取得 `source.url`，timeout 為 60 秒，允許 redirect。
2. HTTP 錯誤會立即停止，不會用空內容建圖。
3. 原始 bytes 寫入：

```text
data/raw/earnings_calls/<lower-co_code>/<source_key>.txt
data/raw/earnings_calls/<lower-co_code>/<source_key>.html
```

4. Document `content_hash` 是原始 bytes 的 SHA-256，用於確認導入的版本。

注意：現行寫入快照是 ingestion 工作的一部分，不是 dry-run。若改由內部 DB
提供原文，仍建議保留一份不可變的原文快照或 DB primary key + row version，
否則日後無法說明某個向量對應哪一版原文。

## 4. 純文字解析成 SpeakerTurn

### 4.1 支援格式

`plain_text` Adapter 支援：

```text
王小明：內容
王小明: 內容
[王小明] 內容
王小明（執行長）：內容

Speaker: 王小明
Title: 執行長
Content: 內容
```

段落內的後續非空白行會併入當前 speaker turn；空白行不會建立空 turn。
解析後的基本結構：

```json
{
  "speaker": "王小明",
  "title": "執行長",
  "section": "unknown",
  "text": "內容"
}
```

`title` 是講者當場職稱；沒有來源就保留 `null`，不使用模型猜測。同一份原文內
若某一 turn 明確提供某位 speaker 的 title，現行 Adapter 會將該 title 補到同名
speaker 的其他 turns。

### 4.2 Section

只在原文出現明確 heading 時設定：

```text
Prepared Remarks / Management Remarks / 管理層說明 / 開場致詞
Q&A / Questions and Answers / 問答環節
```

對外 MCP 不把 section 當成 title。不能確定時，`plain_text` 使用 `unknown`，
不猜測是 prepared remarks 或 Q&A。

### 4.3 完整逐字稿的 Turn 切分

Neo4j `SpeakerTurn` 用於回傳完整逐字稿，不使用 embedding。單一發言過長時，
會以語意邊界切成最多 4,000 字元的 parts，並保留相同的 speaker、title 與
`source_turn_sequence`。

```text
turn_id = <source_id>-turn-<source-turn-sequence:3>-part-<part-index:2>
```

`SpeakerTurn.sequence` 是實際顯示順序。逐字稿 MCP 依 `sequence` 分頁，不依向量分數排序。

## 5. RAG Block 切分算法

### 5.1 目前參數

`chunk_turns()` 的程式預設值：

| 參數 | 預設 | 含義 |
|---|---:|---|
| `max_chars` | `1400` | 每個 Chunk 的嚴格字元上限，含 metadata prefix |
| `min_chars` | `160` | 短 Chunk 合併目標，不是丟棄門檻 |
| overlap | `0` | 目前沒有重疊視窗 |
| 計量單位 | Unicode 字元 | 不是 tokenizer token |

`effective_min = min(min_chars, max_chars // 2)`，因此測試使用較小 `max_chars`時，
最小值也會自動縮小。

### 5.2 每個 Block 實際送去 embedding 的文字

每個 turn 會先加入：

```text
Speaker: <speaker>
Section: <section>
<verbatim turn text>
```

例如：

```text
Speaker: AMY HOOD
Section: question_and_answer
Demand continues to exceed available supply. We are adding capacity...
```

`body_limit = max_chars - len(prefix)`。所以講者名稱與 section 越長，留給原文的字元
預算越少。當 `body_limit < 32` 時直接報錯，不產生不可用 Block。

### 5.3 過長發言的切點

`build_semantic_blocks()` 會先將連續空白正規化成單一空格，然後處理過長文字：

1. 在不超過 `body_limit` 的區間內尋找靠近上限的邊界。
2. 句號、問號、驚嘆號、分號、冒號與空白可作為邊界，支援中英文標點。
3. 優先選離目標上限最近的邊界，同距離時優先標點而不是空白。
4. 找不到較好邊界時才使用硬切，仍保證不超過上限。
5. 任何非空白內容都不會因太短而被丟棄。

### 5.4 過短發言的合併

初步切分後，若 Block body 小於 `effective_min`：

1. 只考慮前一個或後一個且 `section` 相同的 Block。
2. 合併後總文字必須不超過 `max_chars`。
3. 若前後都可合併，目前程式選擇合併後較短、保留較多上限空間者。
4. 一個 Chunk 因此可能含多位講者，`speakers` 保存去重後的完整名單。
5. `speaker` 取合併內 body 最長的原始 Block 講者，僅為 primary speaker；檢索指定
   講者時會使用 `speakers`，不只使用 `speaker`。
6. 如果兩側都無法合併，短 Block 仍保留，不丟棄原文。

這個設計用來處理「單獨一句問題」或「Thank you」等短發言，避免產生大量
語意過少的向量，同時保留問題與回答的關聯。

### 5.5 Block ID 與屬性

Block 按最後順序編號：

```text
paragraph_id = turn-<turn_start:3>-to-<turn_end:3>-block-<sequence:3>
chunk_id     = <source_id>-<paragraph_id>
content_hash = sha256(<chunk.text>)
```

寫入 `Chunk` 的屬性：

```json
{
  "chunk_id": "ir-msft-fy2026-q2-transcript-turn-001-to-002-block-001",
  "co_code": "MSFT",
  "source_id": "ir-msft-fy2026-q2-transcript",
  "source_type": "transcript",
  "title": "Microsoft FY2026 Q2 Earnings Conference Call Transcript",
  "period": "2025Q4",
  "fiscal_label": "FY2026 Q2",
  "event_date": "2026-01-28",
  "speaker": "AMY HOOD",
  "speakers": ["ANALYST", "AMY HOOD"],
  "section": "question_and_answer",
  "sequence": 1,
  "paragraph_id": "turn-001-to-002-block-001",
  "text": "Speaker: ...",
  "embedding": [0.012, -0.034],
  "captured_at": "2026-07-23T...Z",
  "content_hash": "sha256:...",
  "data_version": "ir:msft:2026-01-28"
}
```

ID 與 turn/block 順序有關。若在原文前方插入新發言，後續 ID 可能改變；
重新導入會刪除該 `source_id` 已不存在的舊 ID，不會保留新舊兩套 Block。

## 6. Embedding 呼叫

### 6.1 現行契約

對所有本次導入的 `chunk.text` 建立一個陣列，呼叫：

```http
POST <OLLAMA_URL>/api/embed
Content-Type: application/json

{
  "model": "<OLLAMA_EMBEDDING_MODEL>",
  "input": ["block 1 text", "block 2 text"]
}
```

預設：

```dotenv
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_EMBEDDING_MODEL=qwen3-embedding:0.6b
```

回應必須有與 input 數量相同的 `embeddings` 陣列，否則整次 ingestion 報錯停止。
HTTP timeout 為 120 秒。向量維度從第一個回應向量取得，用來建立 Neo4j
vector index。

### 6.2 目前限制

- 一次將本次 ingestion 全部 Block 送出，目前沒有 batch-size 切分。
- 沒有針對 HTTP 429/5xx 的 retry/backoff。
- 沒有外部 GW API key/header 設定。
- 沒有逐 Block 的失敗重跑或 checkpoint。
- 每次重跑都會重新計算全部指定來源的向量；目前不會依
  `content_hash` 跳過未變 Block。
- `Chunk` 目前沒有寫入 `embedding_model`、`embedding_provider`、`embedding_version`
  或 `embedding_dimensions`。更換模型時需改用新 index 並全量重建，不可在同一
  `embedding` 屬性與 index 混用不同模型。

小量 PoC 可使用現行實作；大量內部法說會正式導入前，應先補上 batch、
retry、hash skip、provider metadata 與可恢復 checkpoint。

### 6.3 接外部 Embedding GW 的最小契約

外部 GW Adapter 必須將供應商回應正規化成：

```python
embed_documents(texts: list[str]) -> list[list[float]]
embed_query(text: str) -> list[float]
```

並明確配置：endpoint、認證 header、model ID/version、dimensions、batch limit、
timeout、rate limit 與 normalization。Document 與 query 必須使用同一個模型版本；
不可使用 GW document vector 配本地 query vector。

GW 尚未實作前，不要只將一般 OpenAI-compatible base URL 放到 `OLLAMA_URL`；
只有它完全實作相同 `/api/embed` request/response 契約時才能直接代換。

## 7. Neo4j 建圖與 Upsert

Embedding 數量檢查通過後，程式才連接 Neo4j，建立：

```text
(:Company)-[:HAS_EARNINGS_CALL]->(:EarningsCall:Document)
(:Company)-[:HAS_DOCUMENT]->(:EarningsCall:Document)
(:EarningsCall)-[:HAS_TURN]->(:SpeakerTurn)
(:EarningsCall)-[:HAS_CHUNK]->(:Chunk)
(:Chunk)-[:CONTAINS_TURN]->(:SpeakerTurn)
```

唯一約束與 index：

| 類型 | 內容 |
|---|---|
| Unique | `Company.co_code` |
| Unique | `Document.source_id` |
| Unique | `SpeakerTurn.turn_id` |
| Unique | `Chunk.chunk_id` |
| Range | `EarningsCall(co_code, event_date)` |
| Range | `Chunk.co_code` |
| Vector | `Chunk.embedding`, cosine，篩選欄位 `co_code/period/source_type` |
| Full text | `Chunk.text`, `Chunk.title` |

寫入使用 Cypher `MERGE` 鍵值，所以同 ID 不會新增重複節點；`SET` 會更新內文、
hash、captured time 與 embedding。

重新導入同一 `source_id` 時：

1. 找出該 EarningsCall 目前仍存在的 `turn_ids` 與 `chunk_ids`。
2. 刪除已不在新版集合的 stale `SpeakerTurn` 與 `Chunk`。
3. `MERGE` 現有 ID，更新內容與向量。
4. 重建該 Chunk 對應 turn range 的 `CONTAINS_TURN` 關係。

因此：

- 不會因重跑而無限累積同 ID 節點。
- 會重新呼叫 embedding，並覆寫同 ID 的向量。
- 若錯誤改變 `source_key`，就會產生新 Document；這不是 upsert 能自動判斷的重複。
- ingestion 還會移除舊版全域 `Speaker` 節點與 `HAS_PARTICIPANT`/`SPOKEN_BY`
  關係，將 speaker 資料統一留在 event-scoped `SpeakerTurn`。

## 8. 查詢時如何使用 Block

1. Public MCP 從自然語言 `query` 解析公司與期間。
2. Repository 強制使用 `co_code`，必要時再加 `period` 與 `source_type=transcript`。
3. 中文問題會加入受控英文關鍵字，複合問題最多拆成 3 個 query facets。
4. Query embedding 會加入檢索 instruction：

```text
Instruct: Retrieve verbatim earnings-call transcript passages that answer the query,
scoped to the specified company and reporting period
Query: <expanded user query>
```

5. 若問題明確出現已知 speaker，先使用 `Chunk.speakers` 篩選，再計算 cosine similarity。
6. 未指定 speaker 時使用 Neo4j vector index，並在 server side 限制公司、期間與來源。
7. 向量候選再與 full-text 結果混合排序，最後轉成含 `source_id`、locator 與
   `content_hash` 的 Evidence。

完整逐字稿要求不走上述 vector Top-K，而是選定單一 EarningsCall 後，依
`SpeakerTurn.sequence` 取得原文。

## 9. 執行流程

### 9.1 先設定

```dotenv
DATA_MODE=local

NEO4J_URI=neo4j://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<secret>
NEO4J_DATABASE=neo4j
NEO4J_VECTOR_INDEX=chunk_embedding_v1
NEO4J_FULLTEXT_INDEX=chunk_fulltext_v1

OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_EMBEDDING_MODEL=qwen3-embedding:0.6b
```

### 9.2 小批導入

```bash
cd backend

../.venv/bin/python -m scripts.ingest_transcripts \
  --sources msft-fy2026-q2
```

成功時輸出會包含：

```json
{
  "sources": 1,
  "chunks": 42,
  "embedding_dimensions": 1024,
  "details": [
    {
      "source_id": "ir-msft-fy2026-q2-transcript",
      "co_code": "MSFT",
      "period": "2025Q4",
      "event_date": "2026-01-28",
      "material_kind": "full_transcript",
      "chunks": 42,
      "turns": 31
    }
  ]
}
```

數字只是格式範例；實際 chunks/turns 以當次原文為準。

### 9.3 Neo4j 核對

```cypher
MATCH (c:Company {co_code: 'MSFT'})-[:HAS_EARNINGS_CALL]->(call:EarningsCall)
RETURN call.source_id, call.fiscal_label, call.event_date, call.content_hash
ORDER BY call.event_date DESC;
```

```cypher
MATCH (call:EarningsCall {source_id: 'ir-msft-fy2026-q2-transcript'})
OPTIONAL MATCH (call)-[:HAS_TURN]->(turn:SpeakerTurn)
WITH call, count(turn) AS turns
OPTIONAL MATCH (call)-[:HAS_CHUNK]->(chunk:Chunk)
RETURN call.source_id, turns, count(chunk) AS chunks,
       min(size(chunk.text)) AS min_chars,
       max(size(chunk.text)) AS max_chars,
       min(size(chunk.embedding)) AS min_dimensions,
       max(size(chunk.embedding)) AS max_dimensions;
```

應確認：

- `max_chars <= 1400`。
- 所有 Chunk 的 embedding dimensions 相同。
- `turns > 0` 且 `chunks > 0`。
- `co_code`、period、source type 正確，不與其他公司混用。

### 9.4 重跑檢查

使用相同 source 再執行一次，檢查唯一鍵是否有重複：

```cypher
MATCH (n:Chunk)
WITH n.chunk_id AS id, count(*) AS copies
WHERE copies > 1
RETURN id, copies;
```

正常結果為空。但重跑仍會產生 embedding 計算成本，直到實作 hash skip 為止。

## 10. 內部 DB + 外部 GW 正式接入順序

```text
1. 用唯讀帳號探索 MariaDB schema
2. 確定法說會 table/view 與 primary key/row version
3. 將每筆資料映射成 TranscriptSource + raw text
4. 先用 1 家公司、1季跑 parser 與 Block QA
5. 建立 Neo4j constraints/full-text/vector indexes
6. 用外部 GW document embedding 寫入專用 embedding property/index
7. 使用同 GW/model 產生 query embedding
8. 通過公司、期間、speaker、長短問題與負面案例 Golden Set
9. 再全量導入，並加入排程與增量同步
```

建議外部 GW 不覆寫現有 local 向量來做比較，而是使用：

```text
Chunk.embedding_local   -> chunk_embedding_local_v1
Chunk.embedding_gateway -> chunk_embedding_gateway_v1
```

這樣可用相同 Block 公平比較 Recall@5、MRR@10、speaker match、latency、error rate
與成本，也能快速 rollback。

## 11. 正式全量導入前的必做改進

| 優先度 | 項目 | 原因 |
|---|---|---|
| P0 | 內部 DB transcript Adapter | 目前 CLI 僅支援登錄後的 URL 來源 |
| P0 | 外部 GW Provider Adapter | 目前只支援 Ollama `/api/embed` 契約 |
| P0 | Provider/model/dimensions metadata | 防止向量模型混用 |
| P0 | Batch + retry/backoff + timeout | 避免大批次超時後全部重來 |
| P0 | `content_hash` skip | 未變 Block 不應重複支付 embedding 成本 |
| P1 | Checkpoint/失敗清單 | 允許從失敗批次繼續 |
| P1 | Staging index + atomic promotion | 避免半套新向量被線上查詢 |
| P1 | 資料品質報告 | 檢查空原文、無講者、重複季度與異常 Block |

在上述 P0 完成前，目前實作適合可控小批次 PoC 與檢索品質驗證，不應直接
宣稱可無人值守地同步公司全部法說會。

## 12. 對應程式與測試

| 用途 | 檔案 |
|---|---|
| Transcript 來源、parser、Block、Neo4j upsert | `backend/scripts/ingest_transcripts.py` |
| 語意邊界切分與短 Block 平衡 | `backend/scripts/text_blocks.py` |
| `/api/embed`、constraints 與 indexes | `backend/scripts/init_data.py` |
| Query embedding、公司/期間/speaker 篩選 | `backend/app/repositories.py` |
| 連線與檢索參數 | `backend/app/config.py`、`.env.example` |
| Parser、長短 Block、stale deletion 回歸測試 | `backend/tests/test_transcript_ingestion.py` |

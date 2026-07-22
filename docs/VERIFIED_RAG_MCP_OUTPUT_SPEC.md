# Verified RAG MCP 輸出規格

> 文件層級：內部 Runtime 契約附錄。提供給一般 MCP 串接者時，請使用
> [MCP_PROVIDER_HANDOFF_SPEC.md](MCP_PROVIDER_HANDOFF_SPEC.md)。

- 文件版本：`2.0`
- 適用專案：`fin_poc`
- 最後更新：`2026-07-21`
- 核心原則：財務資料與法說會逐字稿分開檢索、分開驗證、分開引用
- 規格修改方式：[MCP_API_DESIGN_AND_CHANGE_GUIDE.md](MCP_API_DESIGN_AND_CHANGE_GUIDE.md)
- Runtime Tool Schema 快照：[mcp-tools.json](mcp-tools.json)
- 對外串接交付文件：[MCP_PROVIDER_HANDOFF_SPEC.md](MCP_PROVIDER_HANDOFF_SPEC.md)
- 財務資料正規化規格：[FINANCIAL_DATA_SPEC.md](FINANCIAL_DATA_SPEC.md)

## 1. 目標與範圍

本專案對外提供兩個互相隔離的 MCP 工具：

| 用途 | MCP Endpoint | Tool | 允許檢索的資料 |
|---|---|---|---|
| 財務數據與申報文件 | `http://127.0.0.1:8003/mcp` | `ask_financial_rag`、`retrieve_financial_evidence` | 結構化財務資料、SEC filing、官方財務文件 |
| 法說會與管理層發言 | `http://127.0.0.1:8004/mcp` | `ask_earnings_call`、`list_earnings_calls`、`retrieve_multi_period_earnings_call_evidence`、`get_earnings_call_transcript`、`retrieve_earnings_call_evidence`、`retrieve_earnings_call_blocks` | 法說會逐字稿 |

這兩個工具不會把不同類型的資料混在同一個 Top-K 檢索結果中。呼叫端可以是單一 Agent，不需要為此建立多 Agent。

`ask_*` 工具會產生並驗證答案；`retrieve_*_evidence` 只回傳已驗證 Evidence，不呼叫答案生成或語意驗證 LLM，適合由外部 Agent 負責 Generation 的架構。

本規格的目標是讓 Agent 能夠：

1. 根據使用者自然語言選擇正確工具。
2. 只在證據足夠時回答。
3. 回傳可追溯到來源 URL、原文、期間與內容雜湊的引用。
4. 在資料不足、問題有歧義或證據衝突時拒答或要求補充。
5. 對財務與逐字稿資料分別進行回歸測試，避免彼此影響。

## 2. 架構與資料隔離

```text
使用者問題
    |
    v
單一 Agent / MCP Client
    |
    +--> ask_financial_rag  (8003)
    |       |
    |       +--> financial retrieval profile
    |               +--> structured financial database
    |               +--> SEC / official financial documents
    |
    +--> ask_earnings_call  (8004)
            |
            +--> transcript retrieval profile
                    +--> earnings-call transcripts only
```

隔離規則：

- `ask_financial_rag` 不得回傳 `transcript` 證據。
- `ask_earnings_call` 不得回傳 `database`、`financial_report` 或 `graph` 證據。
- 混合問題必須拆成兩次獨立工具呼叫，再由 Agent 合併結論。
- 合併答案時，財務事實與管理層說法必須保留各自的引用，不得把兩種來源視為同一項證據。
- 內部 Knowledge MCP 與 Finance MCP 屬於實作細節，不應直接暴露給最終 Agent。
- 外部 SQL DB 只有在 schema mapping 經人工核准後，才能加入 Financial MCP。

## 3. 工具選擇規則

| 問題意圖 | 應呼叫工具 | 範例 |
|---|---|---|
| 營收、淨利、EPS、資產、現金流等數字 | `ask_financial_rag` | `Microsoft FY2026 Q2 revenue?` |
| SEC filing、風險因子、會計揭露 | `ask_financial_rag` | `Apple 10-K 提到哪些供應鏈風險？` |
| 管理層說了什麼、展望、電話會議 Q&A | `ask_earnings_call` | `Microsoft FY2026 Q2 管理層如何說明 AI demand？` |
| 同時比較實際數字與管理層說法 | 兩個工具都呼叫 | `營收成長多少，管理層如何解釋？` |

Agent 不應只靠關鍵字判斷公司。使用者可以輸入公司名稱、別名或股票代碼；工具會解析為資料庫使用的股票代碼。若公司或期間無法可靠判定，工具必須回傳 `needs_clarification`，不能自行猜測。

## 4. 輸入契約

兩個工具使用相同的輸入結構：

```json
{
  "query": "Microsoft FY2026 Q2 revenue?",
  "co_code": "MSFT"
}
```

### 4.1 欄位定義

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---:|---|
| `query` | string | 是 | 使用者原始問題；不得只傳關鍵字集合 |
| `co_code` | string | 否 | 股票代碼提示，例如 `MSFT`、`AAPL`、`NVDA` |

規則：

- `query` 是主要判斷依據。
- `co_code` 是提示，不應用來掩蓋問題中的明顯公司衝突。
- 公司、期間、指標或問題意圖不足時，回傳 `needs_clarification`。
- 尚未存在於資料庫的未來期間，例如 `Apple 2035 Q4`，不得產生數字。

## 5. 統一輸出契約

兩個工具都回傳相同的頂層格式：

```json
{
  "schema_version": "1.1",
  "status": "answered",
  "answer": "...",
  "citations": [],
  "confidence": 0.97,
  "verified": true,
  "verification_notes": [],
  "warnings": [],
  "co_code": "MSFT",
  "display": null,
  "routes": ["finance"],
  "trace_id": "uuid",
  "verification": {"passed": true},
  "data_versions": ["sec:MSFT:0000950170-26-006311"],
  "latency_ms": 112.52,
  "clarification_question": null,
  "period_resolution": {
    "input": "最近一季",
    "resolved_period": "2026Q1",
    "method": "latest_verified_available",
    "confidence": 1.0
  }
}
```

### 5.1 頂層欄位

| 欄位 | 型別 | 必定存在 | 說明 |
|---|---|---:|---|
| `schema_version` | string | 是 | MCP Runtime Schema 版本 |
| `status` | enum | 是 | `answered`、`refused`、`needs_clarification` |
| `answer` | string | 是 | 已驗證回答或安全拒答說明；需要追問時可為空字串 |
| `citations` | array | 是 | 支撐回答的來源；非 `answered` 時通常為空陣列 |
| `confidence` | number | 是 | `0.0` 到 `1.0`，代表本次證據充分程度 |
| `verified` | boolean | 是 | 是否通過回答層驗證 |
| `verification_notes` | string[] | 是 | 驗證通過或失敗的原因 |
| `warnings` | string[] | 是 | 資料限制、衝突或其他注意事項 |
| `co_code` | string/null | 是 | 已解析的股票代碼；無法解析時為 `null` |
| `display` | object/null | 是 | 法說會結構化顯示資料；財務或拒答案例為 `null` |
| `routes` | string[] | 是 | 本次實際使用的檢索路由；未使用時為空陣列 |
| `trace_id` | string/null | 是 | 本次請求追蹤識別碼 |
| `verification` | object | 是 | Evidence、答案與可靠度 Gate 的完整結果 |
| `data_versions` | string[] | 是 | 使用的資料版本或 accession identifier |
| `latency_ms` | number | 是 | MCP 工具端的處理時間，單位為毫秒 |
| `clarification_question` | string/null | 是 | 需要使用者補充時的追問 |
| `period_resolution` | object/null | 是 | 明確或相對期間的正規化結果 |

### 5.2 狀態語意

#### `answered`

只有同時符合以下條件才能使用：

- 公司與問題意圖已解析。
- 找到符合該工具資料範圍的證據。
- 每個關鍵結論都能由引用支撐。
- 回答通過驗證，`verified` 必須為 `true`。
- `citations` 不得為空。

#### `refused`

適用情況：

- 查無資料。
- 指定期間不存在或尚未發生。
- 證據不足以支持回答。
- 證據相互衝突且無法可靠化解。
- 問題不屬於所呼叫工具的資料範圍。

`refused` 時不得用常識補答，也不得產生無來源數字。

#### `needs_clarification`

適用情況：

- 無法辨識公司。
- 多家公司名稱造成歧義。
- 問題缺少必要期間或指標。
- `query` 與 `co_code` 明顯衝突。

呼叫端應將 `clarification_question` 顯示給使用者，取得補充後重試原本選定的工具。

## 6. Citation 契約

```json
{
  "index": 1,
  "evidence_id": "ev-ir-msft-q1-p18",
  "source_id": "ir-msft-fy2026-q1-transcript",
  "source_type": "transcript",
  "title": "Microsoft FY2026 Q2 Earnings Call Transcript",
  "co_code": "MSFT",
  "period": "FY2026 Q2",
  "locator": {"paragraph_id": "paragraph-18"},
  "quoted_text": "...",
  "live_url": "https://www.microsoft.com/en-us/investor/events/fy-2026/earnings-fy-2026-q2",
  "content_hash": "sha256:...",
  "captured_at": "2026-07-20T00:00:00Z",
  "metadata": {
    "speaker": "Satya Nadella",
    "section": "Prepared Remarks",
    "event_date": "2026-01-28"
  }
}
```

### 6.1 Citation 欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| `index` | integer | 回答文字使用的 `[n]` 引用編號 |
| `evidence_id` | string | 唯一 Evidence 識別碼 |
| `source_id` | string | 可供 source preview 回查的來源識別碼 |
| `source_type` | enum | 來源類型，受工具白名單限制 |
| `title` | string | 文件或資料來源標題 |
| `co_code` | string | 股票代碼 |
| `period` | string/null | 財務期間或法說會期間 |
| `locator` | object | 可定位到表格、段落、章節或發言者的結構化位置 |
| `quoted_text` | string | 實際支撐回答的原文片段 |
| `live_url` | string/null | 官方或可核對來源網址 |
| `content_hash` | string/null | 擷取內容的 SHA-256 雜湊 |
| `captured_at` | string/null | 本地快照建立時間，ISO 8601 |
| `metadata` | object | 來源特有欄位，例如 speaker、section、event_date |

### 6.2 每個工具允許的來源類型

`ask_financial_rag`：

- `database`
- `financial_report`
- `url`

`ask_earnings_call`：

- `transcript`

`retrieve_earnings_call_blocks` 同樣只允許 `transcript`，但以巢狀 `content` object 回傳原文、
section、locator、source hash 與 URL。指定唯一已知講者時，每筆 `speaker` 必須是查詢命中的
講者；`speakers` 保留同一合併 block 內的所有講者。

`get_earnings_call_transcript` 不使用 embedding。它選定單一官方 `EarningsCall`，依
`SpeakerTurn.sequence` 回傳 `conversations`。每筆只有一位 speaker；`speaker.title` 是該場
官方職稱，未提供時為 `null`；`content` 是逐字原文。超長來源 turn 會在同一 speaker 下切成
不超過 4,000 characters 的 segments，並以 `next_cursor` 分頁。
對外 conversation contract 不輸出內部 `section`。純文字 ingestion 支援
`姓名：內容`、`姓名: 內容`、`[姓名] 內容`、`姓名（職稱）：內容`，以及分行的
`Speaker` / `Title` / `Content`；沒有明確段落標題時，內部 section 設為 `unknown`。

`list_earnings_calls` 依 `event_date` 列出單一公司的可用法說會。
`retrieve_multi_period_earnings_call_evidence` 最多處理四季，所有 vector/full-text 檢索均先
套用單季 filter，回應也依季度分組。重點型查詢分別檢索營運、策略、展望／風險與 Q&A；
`broad_facet_retrieval` 只代表多面向證據覆蓋，不代表已逐 turn 產生完整摘要。

若財務工具回傳 `transcript`，或逐字稿工具回傳財務來源，即視為隔離測試失敗。

### 6.3 引用最低要求

每項引用至少要能回答：

1. 哪家公司？
2. 哪個期間？
3. 出自哪份文件或資料紀錄？
4. 原文或數值是什麼？
5. 如何透過 URL、locator 或本地快照重新核對？

只有 URL、沒有 `quoted_text` 的引用，不足以支撐 `answered`。只有模型回答、沒有 citation 的結果，也不得標示為 `verified: true`。

## 7. 資料正確性與可追溯流程

```text
官方來源 URL
  -> 本地原始快照
  -> SHA-256 內容雜湊
  -> 公司 / 期間 / 指標 / speaker / section 正規化
  -> Qwen embedding 0.6B 建立向量
  -> 依工具套用來源白名單後檢索
  -> 產生 evidence
  -> 產生 answer + citations
  -> 驗證引用、數值與來源隔離
  -> answered / refused / needs_clarification
```

### 7.1 本地資料保存

- SEC 與財務來源快照：`data/raw/sec/`
- 法說會來源快照：`data/raw/earnings_calls/`
- Golden Set：`eval/sec_golden_set.json`、`eval/transcript_golden_set.json`

本地快照與 `content_hash` 用來確認後續重新測試時使用的是哪一版內容。`live_url` 用來人工回到官方頁面核對；若官方頁面日後更新，仍可用快照與雜湊辨識版本差異。

### 7.2 回答驗證

每次回答至少檢查：

- citation 的公司是否與解析結果一致。
- citation 的期間是否符合問題。
- `quoted_text` 是否真的包含或支持回答中的關鍵事實。
- 數值、單位與期間是否一致。
- citation 是否屬於所呼叫工具的來源白名單。
- 查無資料或未來期間是否正確拒答。

### 7.3 目前驗證範圍

目前本地真實資料回歸結果：

| 測試集 | 結果 | 驗證內容 |
|---|---:|---|
| SEC financial Golden Set | `6/6` | 公司、期間、數值、來源與拒答 |
| Transcript Golden Set | `6/6` | 公司、期間、發言內容、speaker/section 與拒答 |
| Backend tests | `67/67` | API、MCP、檢索、引用、Runtime Schema、公司索引、期間解析、Financial Schema v2、精確值、動態 Key、指標 Alias 排序、外部 DB/API、MCP auth、併發控制與資料隔離 |

這些結果證明目前測試案例與已匯入資料可重現通過，不代表所有公司、所有期間或任何未知文件格式都已獲得百分之百保證。新公司、新文件格式或新資料來源上線前，必須新增對應 Golden Set 後再驗收。

目前逐字稿真實資料涵蓋 Microsoft FY2026 Q2 與 FY2026 Q3。未取得官方完整逐字稿的公司或期間，不應標示為已支援的 transcript coverage。

### 7.4 臨時與外部 SQL 資料庫

未知 DB 不會直接由模型猜測 schema。Long-form 資料至少映射 `company_code`、`period`、`metric`、`value`；動態 Key/Value 財報則必須經 Financial Schema v2 的 Metric Dictionary 與 Provider Mapping。只有 `approved: true` 的 dataset/API/mapping 才能成為 Evidence。原始 Payload 與未知 Key 保留，但未知語意不可回答。

外部 DB Adapter 必須符合：

- 連線密碼只存在環境變數，不寫入 Mapping 或 discovery report。
- 僅反射已設定的資料表並使用 bound parameters 執行 `SELECT`。
- 不接受模型或使用者提供任意 SQL。
- Evidence 包含 database ID、dataset ID、table、primary key、data version 與 record hash。
- 單一外部 DB 離線時，既有來源仍可運作；沒有其他合格證據時必須拒答。
- mapping 變更後須重新核准並執行 Golden Set。
- 外部 API 僅允許設定檔中的 GET endpoint，必須限制 redirect、timeout、連線數與回應大小，並再次核對回傳的公司及期間。

## 8. 執行驗證

從專案根目錄執行：

```bash
cd backend
../.venv/bin/python -m scripts.evaluate_sec
../.venv/bin/python -m scripts.evaluate_transcripts
../.venv/bin/pytest -q
```

驗收條件：

- 兩個 Golden Set 都必須全數通過。
- 財務測試不得命中逐字稿來源。
- 逐字稿測試不得命中財務或 graph 來源。
- 正向案例必須 `status=answered`、`verified=true` 且引用非空。
- 負向案例必須 `refused` 或 `needs_clarification`，不得幻覺補答。
- 任何資料格式轉換後，都必須重新執行完整測試。

## 9. 回應範例

### 9.1 財務問題：成功回答

```json
{
  "schema_version": "1.1",
  "status": "answered",
  "answer": "Microsoft FY2026 Q2 的營收為……",
  "citations": [
    {
      "index": 1,
      "evidence_id": "ev-db-msft-revenue",
      "source_id": "sec-msft-companyfacts",
      "source_type": "database",
      "title": "Microsoft FY2026 Q2 financial facts",
      "co_code": "MSFT",
      "period": "FY2026 Q2",
      "locator": {
        "table": "financial_facts",
        "primary_key": "fact:sha256...",
        "columns": [
          "co_code", "period", "metric_code", "value_exact", "unit",
          "statement_type", "duration_type", "consolidation_scope"
        ]
      },
      "quoted_text": "Revenue ...",
      "live_url": "https://www.sec.gov/Archives/edgar/data/...",
      "content_hash": "sha256:...",
      "captured_at": "2026-07-20T00:00:00Z",
      "metadata": {
        "metric_code": "revenue",
        "provider_metric_key": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "value_exact": "...",
        "unit": "USD",
        "statement_type": "income_statement",
        "duration_type": "quarter",
        "consolidation_scope": "consolidated"
      }
    }
  ],
  "confidence": 0.97,
  "verified": true,
  "verification_notes": ["The cited period and metric match the query."],
  "warnings": [],
  "co_code": "MSFT",
  "display": null,
  "routes": ["finance"],
  "trace_id": "uuid",
  "verification": {"passed": true},
  "data_versions": ["sec:MSFT:accession-id"],
  "latency_ms": 112.52,
  "clarification_question": null,
  "period_resolution": {
    "input": "2026Q2",
    "resolved_period": "2026Q2",
    "method": "explicit_fiscal_quarter",
    "confidence": 1.0
  }
}
```

### 9.2 法說會問題：成功回答

```json
{
  "schema_version": "1.1",
  "status": "answered",
  "answer": "管理層在 FY2026 Q2 法說會中表示……",
  "citations": [
    {
      "index": 1,
      "evidence_id": "ev-ir-msft-q2-p18",
      "source_id": "ir-msft-fy2026-q2-transcript",
      "source_type": "transcript",
      "title": "Microsoft FY2026 Q2 Earnings Call Transcript",
      "co_code": "MSFT",
      "period": "FY2026 Q2",
      "locator": {"paragraph_id": "paragraph-18"},
      "quoted_text": "...",
      "live_url": "https://www.microsoft.com/en-us/investor/events/fy-2026/earnings-fy-2026-q2",
      "content_hash": "sha256:...",
      "captured_at": "2026-07-20T00:00:00Z",
      "metadata": {
        "speaker": "Satya Nadella",
        "section": "Prepared Remarks",
        "event_date": "2026-01-28"
      }
    }
  ],
  "confidence": 0.95,
  "verified": true,
  "verification_notes": ["The quoted passage matches the requested company and period."],
  "warnings": [],
  "co_code": "MSFT",
  "display": {
    "title": "MSFT FY2026 Q2 法說會",
    "period": "FY2026 Q2",
    "speakers": ["Satya Nadella"],
    "content": "管理層在 FY2026 Q2 法說會中表示……",
    "sources": [
      {
        "citation_index": 1,
        "speaker": "Satya Nadella",
        "section": "Prepared Remarks",
        "source_content": "...",
        "source_url": "https://www.microsoft.com/...",
        "locator": {"paragraph_id": "paragraph-18"},
        "content_hash": "sha256:..."
      }
    ]
  },
  "routes": ["transcript"],
  "trace_id": "uuid",
  "verification": {"passed": true},
  "data_versions": ["ir:MSFT:FY2026-Q2:sha256"],
  "latency_ms": 229.04,
  "clarification_question": null,
  "period_resolution": {
    "input": "2026Q2",
    "resolved_period": "2026Q2",
    "method": "explicit_fiscal_quarter",
    "confidence": 1.0
  }
}
```

### 9.3 未來期間或查無資料：拒答

```json
{
  "schema_version": "1.1",
  "status": "refused",
  "answer": "目前找不到足以回答此問題的授權來源，因此不產生推測性答案。",
  "citations": [],
  "confidence": 0.0,
  "verified": false,
  "verification_notes": ["No evidence was found for the requested period."],
  "warnings": ["The requested period is unavailable or has not occurred."],
  "co_code": "AAPL",
  "display": null,
  "routes": [],
  "trace_id": "uuid",
  "verification": {"passed": false},
  "data_versions": [],
  "latency_ms": 18.4,
  "clarification_question": null,
  "period_resolution": null
}
```

### 9.4 公司不明：要求補充

```json
{
  "schema_version": "1.1",
  "status": "needs_clarification",
  "answer": "",
  "citations": [],
  "confidence": 0.0,
  "verified": false,
  "verification_notes": ["Company could not be resolved reliably."],
  "warnings": [],
  "co_code": null,
  "display": null,
  "routes": [],
  "trace_id": null,
  "verification": {"passed": false},
  "data_versions": [],
  "latency_ms": 5.2,
  "clarification_question": "請問你指的是哪一家公司或股票代碼？",
  "period_resolution": null
}
```

## 10. Agent 整合規則

推薦的單一 Agent 流程：

```text
1. 判斷問題屬於財務、逐字稿或混合意圖。
2. 財務意圖呼叫 ask_financial_rag。
3. 逐字稿意圖呼叫 ask_earnings_call。
4. 混合意圖分別呼叫兩個工具。
5. 對每個工具結果獨立檢查：
   status == answered
   verified == true
   citations.length > 0
6. 只整理通過檢查的內容，並保留原 citation。
7. 任一部分拒答時，明確指出哪一部分缺少證據，不得讓另一工具的資料代替。
```

呼叫端不得：

- 將模型既有知識偽裝成 MCP 查詢結果。
- 在 `refused` 後自行補上沒有 citation 的答案。
- 刪除 `warnings` 或 `verification_notes`。
- 將兩個工具的 confidence 簡單平均後宣稱整體已驗證。
- 把法說會預測或管理層意見當作已實現的財務事實。

## 11. 新資料與新格式的相容性要求

資料樣式可以改變，但進入檢索層前必須正規化為穩定欄位：

- 通用欄位：`co_code`（Adapter mapping 可命名為 `company_code`）、`period`、`source_type`、`title`、`text`、`live_url`、`content_hash`、`captured_at`
- 財務欄位：`metric_code`、`provider_metric_key`、`value_exact`、`unit`、`statement_type`、`duration_type`、`consolidation_scope`、`data_version`
- 逐字稿欄位：`speaker`、`section`、`event_date`、`paragraph_index`

新增 PDF、HTML、JSON、XBRL 或第三方資料時，應新增 adapter 將其轉成上述欄位，而不是讓檢索器直接依賴原始格式。每個 adapter 必須具備：

- 原始資料保存。
- 可重現的解析流程。
- 內容雜湊。
- 欄位完整性檢查。
- 至少一組正向與負向 Golden Set。
- 雙 MCP 隔離回歸測試。

## 12. 版本相容性

- `2.x`：雙 MCP 隔離架構，使用本文件的統一輸出格式。
- 增加 optional 欄位屬於 minor update。
- 刪除或重新命名既有欄位、改變 status 語意、改變來源白名單，必須升 major version。
- 呼叫端應忽略未知 optional 欄位，但不得忽略 `status`、`verified`、`citations` 與 `warnings`。

## 13. 安全與稽核

- 對外錯誤訊息不得包含 API key、資料庫密碼、內部 prompt 或完整 stack trace。
- 日誌可保存 query、解析後公司、路由、citation ID、data version、latency 與 status。
- 敏感憑證不得寫入 citation、Golden Set 或原始快照。
- 若來源內容、雜湊或資料版本改變，應留下新的版本紀錄並重新跑 Golden Set。

---

本規格將「有回答」與「已驗證」分開處理。只有具備正確來源類型、可核對引用、期間一致且通過驗證的結果，才能回傳 `status: answered` 與 `verified: true`。

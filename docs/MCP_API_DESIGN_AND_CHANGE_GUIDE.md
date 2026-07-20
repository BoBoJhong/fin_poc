# MCP API 設計與變更指南

> 文件層級：內部 Schema 維護附錄。對外使用規格請見
> [MCP_PROVIDER_HANDOFF_SPEC.md](MCP_PROVIDER_HANDOFF_SPEC.md)。

- 適用專案：`fin_poc`
- 文件版本：`1.1`
- 最後更新：`2026-07-20`
- 相關契約：[VERIFIED_RAG_MCP_OUTPUT_SPEC.md](VERIFIED_RAG_MCP_OUTPUT_SPEC.md)

## 1. 目的

本文件說明：

1. 如何定義 Financial MCP 與 Earnings Call MCP 的輸入、輸出。
2. 如何將法說會結果呈現為「標題、發表人、內文、來源內容」。
3. 未來修改 MCP 規格時，需要變更哪些程式與測試。
4. 如何判斷修改是否相容，以及何時需要建立新版工具。

MCP 規格由本專案擁有者定義。模型只能依照 Tool Schema 呼叫工具，不應自行改變欄位名稱、狀態語意或引用規則。

## 2. 設計原則

### 2.1 穩定 Envelope 與業務顯示分離

所有 MCP 工具保留共用 Envelope：

```json
{
  "schema_version": "1.1",
  "status": "answered",
  "answer": "...",
  "co_code": "MSFT",
  "citations": [],
  "verification": {"passed": true},
  "data_versions": [],
  "trace_id": "..."
}
```

法說會專屬顯示格式放在額外的 `display` 欄位。這樣既有 Agent 仍可讀取 `answer` 與 `citations`，新介面則可直接顯示標題、發表人與來源內容。

### 2.2 Citation 是事實來源

- `answer`／`display.content` 是根據證據產生的回答。
- `display.sources[].source_content` 必須直接來自 citation 的 `quoted_text`。
- `speaker` 必須直接來自 citation metadata，不能由模型猜測。
- `source_url`、`locator`、`content_hash` 必須保留，才能人工核對。
- `display` 只是呈現層，不能取代 `citations`。

### 2.3 拒答格式必須穩定

`refused` 或 `needs_clarification` 時：

- 不得產生假的 speaker 或 source content。
- `citations` 必須為空陣列。
- `display` 可以是 `null`。
- 呼叫端不得使用模型記憶補答。

## 3. 法說會建議輸出規格

### 3.1 MCP Tool

```text
Endpoint: http://127.0.0.1:8004/mcp
Tool: ask_earnings_call
```

輸入：

```json
{
  "query": "Microsoft FY2026 Q1 管理層如何說明 AI demand？",
  "co_code": "MSFT"
}
```

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---:|---|
| `query` | string | 是 | 使用者完整問題，應包含公司與問題意圖 |
| `co_code` | string | 否 | 股票代碼提示，不能取代問題中的公司資訊 |

### 3.2 成功輸出

目前採用以下 schema。為聚焦 display，本段只展示主要欄位；完整 required envelope 以 [VERIFIED_RAG_MCP_OUTPUT_SPEC.md](VERIFIED_RAG_MCP_OUTPUT_SPEC.md) 與 Runtime output schema 為準：

```json
{
  "schema_version": "1.1",
  "status": "answered",
  "answer": "管理層表示 AI 與雲端需求持續增加……[1]",
  "co_code": "MSFT",
  "display": {
    "title": "MSFT FY2026 Q1 法說會",
    "period": "FY2026 Q1",
    "speakers": ["Satya Nadella"],
    "content": "管理層表示 AI 與雲端需求持續增加……[1]",
    "sources": [
      {
        "citation_index": 1,
        "speaker": "Satya Nadella",
        "section": "Prepared Remarks",
        "source_content": "逐字稿實際原文片段……",
        "source_url": "https://www.microsoft.com/...",
        "locator": {
          "paragraph_id": "paragraph-18"
        },
        "content_hash": "sha256:..."
      }
    ]
  },
  "citations": [
    {
      "index": 1,
      "evidence_id": "...",
      "co_code": "MSFT",
      "source_id": "ir-msft-fy2026-q1-transcript",
      "title": "Microsoft FY2026 Q1 Earnings Call Transcript",
      "source_type": "transcript",
      "period": "FY2026 Q1",
      "locator": {
        "paragraph_id": "paragraph-18"
      },
      "quoted_text": "逐字稿實際原文片段……",
      "metadata": {
        "speaker": "Satya Nadella",
        "section": "Prepared Remarks",
        "event_date": "2025-10-29"
      },
      "live_url": "https://www.microsoft.com/...",
      "content_hash": "sha256:...",
      "captured_at": "2026-07-20T00:00:00Z"
    }
  ],
  "routes": ["transcript"],
  "trace_id": "trace-id",
  "verification": {
    "passed": true
  },
  "data_versions": ["ir:MSFT:FY2026-Q1:sha256"]
}
```

### 3.3 介面顯示

```text
標題：MSFT FY2026 Q1 法說會

發表人：Satya Nadella

內文：
管理層表示 AI 與雲端需求持續增加……[1]

來源內容：
「逐字稿實際原文片段……」

來源位置：Prepared Remarks / paragraph 18
來源網址：https://www.microsoft.com/...
```

若命中多位發言人，`speakers` 與 `sources` 必須保留多筆資料，不能將不同人的發言合併後標成同一位 speaker。

### 3.4 拒答輸出

```json
{
  "schema_version": "1.1",
  "status": "refused",
  "answer": "目前找不到指定期間的官方法說會逐字稿。",
  "co_code": "AAPL",
  "display": null,
  "citations": [],
  "routes": ["transcript"],
  "trace_id": "trace-id",
  "verification": {
    "passed": false
  },
  "data_versions": []
}
```

## 4. 欄位來源規則

| 輸出欄位 | 資料來源 | 允許模型產生 |
|---|---|---:|
| `display.title` | `co_code + period` 的固定格式 | 否 |
| `display.period` | 已驗證 citation period | 否 |
| `display.speakers` | citation `metadata.speaker` 去重 | 否 |
| `display.content` | 已通過引用驗證的 `answer` | 是，但必須驗證 |
| `source_content` | citation `quoted_text` | 否 |
| `section` | citation `metadata.section` | 否 |
| `source_url` | source preview `live_url` | 否 |
| `locator` | citation locator | 否 |
| `content_hash` | source preview hash | 否 |

建議以 deterministic transformer 從已驗證結果建立 `display`，不要再呼叫一次模型生成顯示欄位。

## 5. 版本策略

### 5.1 不破壞相容性的修改

以下通常可以增加 minor version，例如 `1.0` → `1.1`：

- 新增 optional 欄位。
- 新增 `display`，同時保留 `answer` 與 `citations`。
- citation metadata 新增 optional 欄位。
- 補充 Tool description，但不改變行為。

### 5.2 破壞相容性的修改

以下必須升 major version，或建立新工具：

- 刪除或重新命名既有欄位。
- 將 optional 欄位改為 required。
- 改變欄位型別，例如 string 改成 array。
- 改變 `answered`、`refused`、`needs_clarification` 的語意。
- 改變 citation 或 verification 的最低要求。
- 將法說會與財務資料重新混入同一個工具。

建議建立新工具名稱，例如：

```text
ask_earnings_call       # 保留既有契約
ask_earnings_call_v2    # 新的破壞性契約
```

舊工具至少保留一個遷移週期，確認所有 Agent／Client 已切換後才移除。

## 6. 修改 MCP 規格的標準流程

### Step 1：先修改文件

在實作前先更新：

- `docs/VERIFIED_RAG_MCP_OUTPUT_SPEC.md`
- 本文件中的 JSON 範例與版本紀錄

明確定義：

- Tool 名稱與用途。
- input schema。
- 成功、拒答、追問輸出。
- required／optional 欄位。
- citation 與 verification 規則。
- 相容性及遷移方式。

### Step 2：建立型別模型

公開 MCP Envelope 與顯示模型位於 `backend/app/mcp_contracts.py`；共用 Evidence 模型位於 `backend/app/models.py`。例如：

```python
class TranscriptDisplaySource(BaseModel):
    citation_index: int
    speaker: str | None = None
    section: str | None = None
    source_content: str
    source_url: str | None = None
    locator: SourceLocator
    content_hash: str | None = None


class TranscriptDisplay(BaseModel):
    title: str
    period: str
    speakers: list[str]
    content: str
    sources: list[TranscriptDisplaySource]
```

正式輸出應由 Pydantic model 驗證，不建議長期使用沒有型別限制的任意 `dict`。

### Step 3：修改 MCP Tool

法說會工具位置：

```text
backend/mcp_servers/transcript.py
```

財務工具位置：

```text
backend/mcp_servers/rag.py
```

修改時必須維持：

- Transcript MCP 只能使用 `transcript` citation。
- Financial MCP 不得使用 `transcript` citation。
- `display.sources` 必須由驗證後 citations 建立。
- 驗證失敗時不得回傳成功顯示內容。
- 財務資料內部改成 Financial Schema v2 或新增 Provider Key，不應改變公開 Envelope；只要 Evidence/Citation 欄位不變，就不需要升 MCP major version。

### Step 4：更新 Client 型別與介面

若前端需要顯示新欄位，修改：

```text
frontend/src/types.ts
frontend/src/App.tsx
```

`display` 是 required-but-nullable：財務與拒答回應必須為 `null`，逐字稿成功回答則為 object。前端應依值是否為 `null` 回退顯示既有 `answer` 與 citation。

### Step 5：更新自動化測試

至少修改或新增：

```text
backend/tests/test_transcript_mcp.py
backend/tests/test_rag_mcp.py
backend/tests/test_http_mcp_integration.py
eval/transcript_golden_set.json
```

法說會輸出測試至少要驗證：

- `display.title` 的公司與期間正確。
- speaker 等於 citation metadata speaker。
- `source_content` 完全等於 citation `quoted_text`。
- source URL、locator、hash 可回查。
- 多位 speaker 不會被錯誤合併。
- `refused` 時 `display=null` 且 citation 為空。
- Financial MCP 不會因本次修改而回傳 transcript。

### Step 6：執行驗收

```bash
make verify
cd backend
../.venv/bin/python -m scripts.evaluate_sec
../.venv/bin/python -m scripts.evaluate_transcripts
```

任一 Golden Set、來源隔離或拒答測試失敗，都不能發布新規格。

### Step 7：重新啟動與端對端測試

```bash
.venv/bin/python backend/scripts/run_local.py
```

使用 MCP Client 實際呼叫 `ask_earnings_call`，確認 Tool Schema 與回傳 JSON 都符合文件，而不只是直接呼叫 Python function。

## 7. 變更提案模板

每次修改可以先填以下模板：

```markdown
# MCP Change Proposal

- Tool：ask_earnings_call
- 原版本：1.1
- 新版本：1.2
- 修改目的：
- 新增欄位：
- 刪除／重新命名欄位：
- Required 欄位變更：
- Status 語意是否改變：否
- Citation 規則是否改變：否
- 是否向下相容：是
- Client 遷移方式：
- Golden Set 變更：
- 回復方案：
```

## 8. 發布檢查表

- [ ] 文件與程式的 `schema_version` 一致。
- [ ] Tool description 與實際資料範圍一致。
- [ ] 成功、拒答、追問案例都有固定 Schema。
- [ ] 新欄位已加入 Pydantic／TypeScript 型別。
- [ ] `source_content` 沒有被模型改寫。
- [ ] 財務與逐字稿來源仍然隔離。
- [ ] 動態財務 Key 只有核准 Mapping 能進入 Evidence，未知 Key 不會被模型猜測。
- [ ] 所有 backend tests 通過。
- [ ] SEC 與 transcript Golden Set 通過。
- [ ] Frontend build 通過。
- [ ] 實際 MCP HTTP 呼叫通過。
- [ ] 破壞性修改已有新工具或遷移期。

## 9. 本次實作狀態

針對「標題、發表人、內文、來源內容」需求，目前已採用 `schema_version: 1.1`。頂層 `display` 為 required-but-nullable，並保留既有 `answer`、`citations`、`verification` 與 `data_versions`。

Runtime 已使用 Pydantic 驗證並對外發布 JSON output schema。若未來要改變 `display` 型別、子欄位 required 狀態或狀態語意，必須規劃 major schema／新版工具，不可直接破壞既有工具。

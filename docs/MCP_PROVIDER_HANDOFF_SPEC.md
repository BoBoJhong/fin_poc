# Verified Financial RAG MCP — 對外交付規格

> 本文件是提供給外部 Agent／MCP Client 團隊的正式人工規格。

- Public Tool contract：`2.0`
- Public response schema：`2.0`
- Transport：MCP Streamable HTTP
- 公司識別：只從自然語言 `query` 解析

## 1. 對外服務

| 服務 | 預設 URL | 用途 |
|---|---|---|
| Financial MCP | `http://127.0.0.1:8003/mcp` | MariaDB 財務指標與核准 filing |
| Earnings Call MCP | `http://127.0.0.1:8004/mcp` | Neo4j 法說會逐字稿 |

`8001` Knowledge MCP 與 `8002` Finance MCP 是內部實作，不提供外部 Agent。
正式環境 URL 與 Bearer Token 由部署方透過安全管道提供。

## 2. 統一輸入規格

每個 Tool 都以 `query` 作為公司與問題的唯一自然語言輸入：

```json
{
  "query": "微軟 2025 Q3 法說會內容"
}
```

公開 Tool 不接受 `co_code` 或 `company_code` 選擇參數。Agent 必須把對話脈絡改寫成
自足問題，例如「那上一季呢？」應改寫成「微軟 2025 Q2 法說會重點是什麼？」後再呼叫。
`cursor`、`limit`、`quarters` 只在需要分頁或多期控制的 Tool 使用。

## 3. Tool 清單與選擇

### Financial MCP

| Tool | 使用時機 | 額外參數 |
|---|---|---|
| `ask_financial_rag` | 要可直接呈現的財務回答 | 無 |
| `retrieve_financial_evidence` | Agent 自行整理已驗證財務證據 | 無 |

### Earnings Call MCP

| Tool | 使用時機 | 額外參數 |
|---|---|---|
| `ask_earnings_call` | 詢問法說會說了什麼、原因或觀點 | 無 |
| `list_earnings_calls` | 先確認有哪些季度／「最近幾季」 | `limit`，預設 10 |
| `retrieve_multi_period_earnings_call_evidence` | 比較最多四季的主題或重點 | `quarters`、`limit` |
| `get_earnings_call_transcript` | 取得指定季度完整逐字稿 | `cursor`、`limit` |
| `retrieve_earnings_call_evidence` | 取得一般法說會證據 | 無 |
| `retrieve_earnings_call_blocks` | 需要 block 與巢狀 content | 無 |

若使用者問「最近三季」，先呼叫 `list_earnings_calls`，再把回傳的季度傳給多期檢索。
若要數季完整逐字稿，每季各自呼叫 `get_earnings_call_transcript`，持續使用
`next_cursor`，直到它為 `null`。

## 4. 共用回答格式

`ask_financial_rag` 與 `ask_earnings_call` 使用相同的精簡 Envelope：

```json
{
  "schema_version": "2.0",
  "status": "answered",
  "answer": "管理層表示雲端需求持續成長。[1]",
  "company_code": "MSFT",
  "period": "2025Q3",
  "citations": [
    {
      "index": 1,
      "source_id": "msft-2025q3-call",
      "source_type": "transcript",
      "excerpt": "Demand for our cloud offerings remained strong...",
      "period": "2025Q3",
      "locator": {"paragraph_id": "turn-18"},
      "source_url": null,
      "content_hash": "sha256:...",
      "data_version": "2025Q3-v1",
      "speaker": "Satya Nadella"
    }
  ],
  "warnings": [],
  "clarification_question": null
}
```

`company_code` 是服務解析出的公司主檔代碼；Neo4j 與內部資料仍可使用 `co_code`，但不會把
內部命名暴露成兩套公開欄位。資料庫來源沒有 URL 時，`source_url` 合法地為 `null`；追溯依
`source_id`、`locator`、`content_hash` 與 `data_version` 完成。

### 狀態處理

- `answered`：只有 `citations` 非空時才可使用 `answer`。
- `refused`：不可用模型記憶補答案；檢查 `warnings`。
- `needs_clarification`：把 `clarification_question` 詢問使用者後，以完整新 `query` 重試。

Schema 2.0 已移除重複或偏內部診斷的 `display`、`co_code`、`verified`、`verification`、
`confidence`、`routes`、`trace_id`、`latency_ms`、`period_resolution`。安全語意直接由
`status`、非空 citations 與 warnings 表達。

## 5. Evidence-only 格式

Evidence Tool 的共同格式為：

```json
{
  "schema_version": "2.0",
  "status": "retrieved",
  "company_code": "MSFT",
  "period": "2025Q3",
  "items": [
    {
      "source_id": "msft-2025q3-call",
      "source_type": "transcript",
      "title": "Microsoft FY2025 Q3 Earnings Call",
      "content": "...",
      "score": 0.91,
      "period": "2025Q3",
      "locator": {"paragraph_id": "turn-18"},
      "content_hash": "sha256:...",
      "data_version": "2025Q3-v1",
      "speaker": "Satya Nadella",
      "speakers": ["Satya Nadella"]
    }
  ],
  "warnings": [],
  "clarification_question": null
}
```

財務與法說會 Evidence 的形狀相同，但來源白名單互相隔離。`title` 是文件／證據標題；講者
職稱不放在這個欄位。

## 6. 完整逐字稿格式

`get_earnings_call_transcript` 不走向量 Top-K，而依原文順序回傳 speaker turns：

```json
{
  "schema_version": "2.0",
  "status": "retrieved",
  "company_code": "MSFT",
  "period": "2025Q3",
  "quarter": "FY2025 Q3",
  "conversations": [
    {
      "speaker": {
        "name": "Satya Nadella",
        "title": "Chairman and Chief Executive Officer"
      },
      "content": "..."
    }
  ],
  "next_cursor": 20,
  "source_id": "msft-2025q3-call",
  "source_url": null,
  "warnings": [],
  "clarification_question": null
}
```

多發言人以 `conversations[]` 分層；每筆的 `speaker` 是 `{name, title}`，外層 `content` 是該次
發言。`speaker.title` 指發言人職稱；發言人不需要建立獨立 Neo4j 節點。內部 parser 可保留
`section` 以改善切塊，但公開格式不輸出。

## 7. Block 格式

`retrieve_earnings_call_blocks` 的 `items[].content` 可以是 JSON object：

```json
{
  "period": "2025Q3",
  "speaker": "Satya Nadella",
  "speakers": ["Satya Nadella"],
  "title": "Microsoft FY2025 Q3 Earnings Call",
  "score": 0.91,
  "content": {
    "text": "...",
    "paragraph_id": "turn-18",
    "source_id": "msft-2025q3-call",
    "content_hash": "sha256:...",
    "source_url": null
  }
}
```

## 8. Agent 最小 Prompt

```text
你只能使用 MCP 回傳資料回答。
每次呼叫的 query 必須包含公司名稱或股票代碼，以及使用者要求的期間。
財務數字使用 Financial MCP；法說會內容使用 Earnings Call MCP。
status=answered 且 citations 非空才可呈現答案。
status=refused 時不得自行補資料。
status=needs_clarification 時詢問 clarification_question。
使用 [n] 保留 citations 對應；資料庫來源沒有 URL 是正常情況。
```

## 9. OpenCode 設定

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "verified-financial-rag": {
      "type": "remote",
      "url": "http://127.0.0.1:8003/mcp",
      "headers": {"Authorization": "Bearer {env:VERIFIED_RAG_MCP_TOKEN}"}
    },
    "verified-earnings-call": {
      "type": "remote",
      "url": "http://127.0.0.1:8004/mcp",
      "headers": {"Authorization": "Bearer {env:VERIFIED_RAG_MCP_TOKEN}"}
    }
  }
}
```

只把 Token 放在環境變數或 Secret Manager，不寫入 repo。正式網際網路部署仍需組織的
OAuth/OIDC Gateway、TLS、Rate Limit 與存取稽核。

## 10. 驗收清單

- Tool schema 不接受 `co_code`／`company_code` 輸入。
- 正向回答是 `schema_version=2.0`、`status=answered` 且 citations 非空。
- 查無資料回 `refused`，公司不明回 `needs_clarification`。
- 財務結果沒有 transcript；法說結果沒有財務資料。
- 指定季度及 `YEAR` + `QUARTER=Qn` 能正規化成 `YYYYQn`。
- 完整逐字稿依序分頁，沒有重複或漏掉 turn。
- 無 URL 的資料仍能以 source id、locator、hash、version 追溯。
- Client 保存未知 optional 欄位，但不可忽略 status、citations、warnings 或 schema version。

機器可讀 Tool Schema：[`mcp-tools.json`](mcp-tools.json)。內部欄位與版本治理詳見
[`VERIFIED_RAG_MCP_OUTPUT_SPEC.md`](VERIFIED_RAG_MCP_OUTPUT_SPEC.md) 與
[`MCP_API_DESIGN_AND_CHANGE_GUIDE.md`](MCP_API_DESIGN_AND_CHANGE_GUIDE.md)。

# Verified RAG MCP Runtime 輸出規格

> 內部 Runtime 契約附錄。外部串接請優先閱讀
> [MCP_PROVIDER_HANDOFF_SPEC.md](MCP_PROVIDER_HANDOFF_SPEC.md)。

## 1. 契約版本與邊界

- Public Tool contract：`2.0`
- Public response schema：`2.0`
- Legacy HTTP API response：`1.1`，由 adapter 維持相容，不等於 Public MCP Schema
- Runtime models：`backend/app/mcp_contracts.py`
- Machine schema：`docs/mcp-tools.json`

Financial MCP 只能使用結構化財務／核准 filing evidence；Earnings Call MCP 只能使用 Neo4j
transcript evidence。外部 MariaDB 欄位先在 adapter 正規化，不可直接穿透公開契約。

## 2. 共用設計原則

1. 公司只從自足的自然語言 `query` 解析；控制參數不得充當公司選擇器。
2. 公開公司欄位統一為 `company_code`；內部 Company、Neo4j 與 SQL mapping 可保留 `co_code`。
3. 回答是否可用由 `status` 與 citations 判斷，不輸出重複的 verified/verification 欄位。
4. 來源允許沒有 URL，但必須保留 `source_id`、locator、hash 與 data version。
5. `null` 欄位固定存在，避免小模型依不同回應猜測形狀。
6. Internal diagnostics、routing、latency 與 period-resolution trace 不進 Public MCP。

## 3. 回答 Envelope

| 欄位 | 型別 | 說明 |
|---|---|---|
| `schema_version` | `"2.0"` | Runtime response version |
| `status` | `answered/refused/needs_clarification` | 回應狀態 |
| `answer` | string | 只由已驗證 Evidence 生成 |
| `company_code` | string/null | Company Master 解析結果 |
| `period` | string/null | 正規化期間，例如 `2025Q3` |
| `citations` | array | 回答使用的原文或精確值引用 |
| `warnings` | string[] | 拒答或資料限制 |
| `clarification_question` | string/null | 需要補充時的問題 |

Citation 必填欄位為 `index`、`source_id`、`source_type`、`excerpt`、`period`、`locator`、
`source_url`、`content_hash`、`data_version`、`speaker`。可為空的欄位仍輸出 `null`。

有效回答必須同時符合：

```text
status == "answered"
citations.length > 0
answer 中的 [n] 能對應 citations[index=n]
所有 citations 符合該 MCP 的 source-type 白名單
```

## 4. Evidence Envelope

Evidence-only tools 回傳 `status`、`company_code`、`period`、`items`、`warnings`、
`clarification_question`。每個 item 包含：

```text
source_id, source_type, title, content, score, period, locator,
content_hash, data_version, speaker, speakers
```

`title` 是文件標題，不是發言人職稱。`score` 是檢索排序訊號，不是回答已驗證的替代品。

## 5. 法說會專用回應

### List

`earnings_calls[]` 為 `{period, quarter, event_date, source_id}`。公司代碼只放在頂層，避免每筆重複。

### Multi-period

`quarters[]` 為 `{quarter, period, event_date, source_id, coverage_mode, items}`。各季獨立保留
Evidence，不可在驗證前混成一組。無明確主題時可使用 `broad_facet_retrieval`；有主題時使用
`topic_retrieval`。

### Full transcript

`get_earnings_call_transcript` 回傳固定順序的 `conversations[]`，每筆沿用內部
`TranscriptConversationTurn`：`speaker: {name, title}` 與 `content`。另包含 `quarter`、`next_cursor`、
`source_id`、`source_url`。這是完整閱讀路徑，不使用向量 Top-K。

### Blocks

`items[].content` 是 `{text, paragraph_id, source_id, content_hash, source_url}`。公開層不輸出
內部 `section` 或重複的 fiscal label。

## 6. 追溯與無 URL 資料

MariaDB 與 Neo4j 原文通常沒有公開 URL，此時 `source_url=null`。追溯鍵如下：

- `company_code`：公司範圍；
- `period`：期間；
- `source_id`：資料列或逐字稿來源；
- `locator`：row／table／paragraph 定位；
- `content_hash`：內容一致性；
- `data_version`：同步批次或資料版本。

來源預覽由內部 API/Gateway 使用自身權限取得，Public MCP 不暴露 DB 連線資訊。

## 7. 版本 1.1 → 2.0

2.0 是刻意的 breaking change：

- `co_code` 改為 `company_code`；
- `quoted_text` 改為較直觀的 `excerpt`；
- Evidence 的 `evidence` 改為 `items`；
- 移除 `display`、`routes`、`trace_id`、`verification`、`verified`、`confidence`、
  `verification_notes`、`data_versions`、`latency_ms`、`period_resolution`；
- 法說會 `section` 只留內部；
- list item 不重複公司代碼。

舊 HTTP `/api/v1/*` 仍由 `backend/app/http_contracts.py` 輸出 1.1，以免這次 MCP 變更破壞現有 UI。

## 8. 驗證要求

- Pydantic 對每個 Tool response 做 runtime validation。
- FastMCP 發布相同的 output JSON Schema。
- 測試必須覆蓋 answered/refused/needs-clarification、來源隔離、自然語言公司解析、期間解析、
  多期分組、逐字稿分頁與無 URL provenance。
- 修改 models 或 tools 後執行 `backend/scripts/export_mcp_spec.py --in-process` 更新機器規格。
- Schema breaking change 必須升 major version，並同步三份正式文件與 Agent prompt。

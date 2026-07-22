# MCP API 設計與變更指南

> 本文件供維護者修改 Public MCP Tool 或 Response Schema。現行公開契約為 `2.0`。

## 1. 現行決策

- 所有公開 Tool 的公司範圍只來自自然語言 `query`。
- 對外統一使用 `company_code`；`co_code` 是內部資料模型名稱。
- 回答 Tool 使用精簡的 Answer Envelope；檢索 Tool 使用 `items`。
- Agent 以 `status`、citations 與 warnings 判斷結果，不依賴內部驗證 trace。
- 完整逐字稿用 deterministic reader 與 cursor；主題問題才用向量檢索。
- 法說會 speaker 與 title 儲存在 turn/block 屬性，不建立 Speaker 節點。
- `section` 可供 parser、chunking 與 retrieval 使用，但不對外輸出。
- HTTP API 1.1 與 Public MCP 2.0 是兩個 adapter contract，不能混用版本號。

## 2. 程式位置

| 內容 | 位置 |
|---|---|
| Public response models | `backend/app/mcp_contracts.py` |
| Legacy HTTP models | `backend/app/http_contracts.py` |
| Financial MCP tools | `backend/mcp_servers/rag.py` |
| Transcript MCP tools | `backend/mcp_servers/transcript.py` |
| HTTP → Public MCP adapter | `backend/app/public_mcp_service.py` |
| Company/period parsing | `backend/app/company_resolver.py`, `period_resolver.py` |
| Runtime tests | `backend/tests/test_rag_mcp.py`, `test_transcript_mcp.py` |
| Machine schema exporter | `backend/scripts/export_mcp_spec.py` |

## 3. 新增或修改 Tool

1. 先界定 domain 與允許的 source types；不同信任政策應建立不同 Public MCP。
2. 定義最小輸入。公司名稱不可成為額外 selector；對話 Agent 必須產生自足 `query`。
3. 在 `mcp_contracts.py` 建立明確 Pydantic response model；所有公開欄位固定且可驗證。
4. Tool 必須設定 `output_schema=Model.model_json_schema()`，回傳前再 `model_dump(mode="json")`。
5. 補三種狀態、來源隔離、欄位 required/nullability 與真實 MCP transport 測試。
6. 更新人工規格、`mcp-tools.json`、Golden Set 與版本。

不要把供應商原始 payload、DB 欄位名、SQL、模型 trace、credentials 或任意 metadata 直接公開。
先由 typed adapter 轉成內部 Evidence，再由 compact transformer 產生公共格式。

## 4. 版本規則

需要升 major：

- 刪除或重新命名欄位；
- 將 nullable 改為 non-nullable；
- 改變 status、安全或 citation 語意；
- 修改 Tool input，使舊呼叫失效。

可升 minor：新增可忽略的 optional 欄位或向後相容的新 Tool。修文件或不影響契約的 bug 可升 patch。
不能只改文件版本；Pydantic model、FastMCP server version、機器 Schema、測試與 prompt 必須一致。

## 5. Response 審查問題

- Agent 是否真的需要這個欄位才能回答或追溯？
- 欄位是否與其他欄位重複？
- 小模型是否能用固定規則判斷，而不需理解內部驗證結構？
- 沒有 URL 時是否仍可追溯？
- 多季、多發言人與分頁是否保持固定形狀？
- Financial 與 Transcript 來源是否仍隔離？
- 這次改動是否意外破壞 Legacy HTTP？

若答案只是方便除錯，欄位應留在 log／trace，而非 Public MCP response。

## 6. 驗證命令

```bash
cd backend
DATA_MODE=mock MCP_ENABLED=false ../.venv/bin/python -m pytest -q tests/test_rag_mcp.py tests/test_transcript_mcp.py tests/test_product_runtime.py tests/test_api.py
DATA_MODE=mock MCP_ENABLED=false ../.venv/bin/python -m scripts.export_mcp_spec --in-process
../.venv/bin/ruff check app mcp_servers scripts tests
../.venv/bin/python -m compileall -q app mcp_servers scripts
```

正式交付另需在允許 localhost socket 的環境跑 MCP HTTP initialize/tools-list/invoke integration tests。

## 7. Change Proposal 範本

```text
目的：
受影響 Tool：
目前版本：
目標版本：
輸入差異：
輸出差異：
來源與安全語意是否改變：
HTTP 相容性：
遷移方式：
測試與 Golden Set：
文件與 machine schema：
```

實際欄位定義見 [Runtime 輸出規格](VERIFIED_RAG_MCP_OUTPUT_SPEC.md)，外部操作方式見
[對外交付規格](MCP_PROVIDER_HANDOFF_SPEC.md)。

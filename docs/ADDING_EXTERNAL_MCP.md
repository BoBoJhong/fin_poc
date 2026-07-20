# 新增外部 MCP 整合指南

> 文件角色：三份正式規格之一。只提供給需要將其他 MCP 整入本專案的開發者。
> 產品與部署基線請見 [PROJECT_SPEC.md](PROJECT_SPEC.md)。

- 文件版本：`1.0`
- 適用產品版本：`1.0.0`
- 公開 Response Schema：`1.1`
- 最後更新：`2026-07-21`

本文件說明如何把另一個 MCP Server 安全地整入本專案。現有 Runtime **不是**任意 MCP
URL 的自動代理器；新增 MCP 必須先決定整合層級、固定 Tool allowlist、轉換資料契約，並通過
公司、期間、來源及引用驗證。這是刻意的產品邊界，避免外部 Tool 的欄位或提示直接污染已驗證
答案。

## 1. 先選擇整合模式

| 需求 | 模式 | 是否修改本專案 |
|---|---|---:|
| 同事的 Agent 同時使用本 MCP 與另一個獨立 MCP | Agent 並列註冊 | 否 |
| 另一個 MCP 提供財務文件、指標或逐字稿，需進入目前 RAG | 內部 Evidence Provider | 是 |
| 新增新聞、法規、研究報告等獨立領域 | 新增公開 Domain MCP | 是 |
| 新 MCP 完全取代現有 Knowledge／Finance MCP | 相容 Adapter／Gateway | 是 |

若兩個 MCP 的答案不需要共同驗證，優先使用「Agent 並列註冊」。只有資料必須進入本專案的
Evidence、Citation、拒答與來源回查流程時，才將它接入 Runtime。

## 2. 模式 A：由外部 Agent 並列調用

這是風險最低、最快的方式。本專案不需要修改；在 Agent Host 同時註冊兩個 Server：

```json
{
  "mcpServers": {
    "verified-financial-rag": {
      "transport": "http",
      "url": "https://rag.example.com/financial/mcp",
      "headers": {"Authorization": "Bearer ${VERIFIED_RAG_MCP_TOKEN}"}
    },
    "another-domain": {
      "transport": "http",
      "url": "https://another.example.com/mcp",
      "headers": {"Authorization": "Bearer ${ANOTHER_MCP_TOKEN}"}
    }
  }
}
```

Agent 必須保留不同 MCP 的來源與信任等級，不得把另一個 MCP 的內容標示成本專案的
`verified=true`。若要合成答案，分別保留 citation、status、warning 與資料版本。

## 3. 接入前取得的規格

Provider 至少要提供：

1. MCP Transport、正式 URL、認證方法與 TLS 要求。
2. `tools/list` 的 Tool 名稱、說明、Input Schema、Output Schema。
3. Timeout、Rate Limit、最大 Response、分頁與重試政策。
4. 資料涵蓋範圍、更新頻率、版本、授權與保存政策。
5. 公司識別欄位、財務期間定義、時區與 Fiscal Calendar 規則。
6. Source ID、原始網址或定位資訊、內容雜湊及擷取時間。
7. 錯誤與部分成功的語意。

尚未取得 Output Schema 或來源追溯欄位時，只能進行隔離 Sandbox 探索，不得直接加入正式
RAG。Token 必須透過 Secret Manager／環境變數注入，不得放在 Mapping、Prompt 或 Git。

## 4. 模式 B：接成既有 RAG 的 Evidence Provider

### 4.1 固定內部契約

外部 MCP 回應必須在 Adapter 邊界轉成
[`Evidence`](../backend/app/models.py)；不得讓供應商欄位穿透到公開 MCP：

```json
{
  "evidence_id": "vendor:stable-record-id",
  "co_code": "MSFT",
  "source_id": "vendor-source-id",
  "source_type": "financial_report",
  "title": "Official filing title",
  "content": "Exact source passage",
  "score": 0.91,
  "period": "2026Q1",
  "locator": {"page": 12, "paragraph_id": "p-18"},
  "captured_at": "2026-07-21T00:00:00Z",
  "content_hash": "sha256:...",
  "data_version": "vendor:revision-42",
  "metadata": {"provider": "approved_vendor"}
}
```

必要規則：

- `co_code` 必須映射到 Company Master 的既有代碼，不能由模型建立。
- `period` 必須轉為本專案標準 Fiscal Period。
- `source_type` 必須屬於目標 Profile 的 allowlist。
- `content`／數值必須來自 Provider 原始回應，不能由 Adapter 生成推測內容。
- `evidence_id`、`source_id`、locator、hash、version 必須能重現來源。
- 供應商分數不能直接視為可信；仍須通過本專案 `EvidenceValidator`。

### 4.2 程式擴充位置

1. 在 [`backend/app/config.py`](../backend/app/config.py) 增加明確設定，例如
   `risk_mcp_url`、認證 Secret 名稱、timeout 與 concurrency；不要接受使用者傳入 URL。
2. 在 [`backend/app/mcp_gateway.py`](../backend/app/mcp_gateway.py) 的
   `MultiServerMCPClient` 註冊固定 Server，並只包裝核准的 Tool。
3. 建立 Provider-specific Adapter，把 Output Schema 轉成 `Evidence`／`SourcePreview`。
4. 在 [`backend/app/agents.py`](../backend/app/agents.py) 加入明確 Route 與 Retrieval Node，
   或將相同資料類別併入既有 Node。
5. 在 [`backend/app/validation.py`](../backend/app/validation.py) 固定新來源的 Profile allowlist、
   provenance 與衝突規則。
6. 若來源需要預覽，在 Gateway 實作對應的 Source Preview Tool 包裝。

目前 Gateway 以 `tool.name` 建立索引，因此所有內部 MCP Tool 名稱必須唯一。新增 Server 前須
加入重名測試；不得依賴後載入的 Tool 覆蓋先載入 Tool。建議 Provider Tool 使用領域前綴，例如
`search_risk_documents`，而不是通用的 `search`。

### 4.3 不可直接信任的回應

下列輸出不能直接成為 Evidence：

- 只有一段生成答案，沒有原始 passage／record；
- 沒有公司或期間範圍；
- `source_id` 每次請求都隨機且無法回查；
- Provider 將多家公司合併在同一段而沒有逐筆 scope；
- 只有 URL、沒有擷取內容、版本或定位；
- Tool 允許模型提交 SQL、Cypher、任意 URL 或未限制的 Provider 參數。

這類 MCP 可以由 Agent 獨立使用，但不能取得本專案的 Verified RAG 信任標記。

## 5. 模式 C：新增公開 Domain MCP

若新領域需要不同來源政策，例如新聞與財務／逐字稿不能共用信任語意，應新增獨立公開 MCP，
不要把所有來源塞進 `ask_financial_rag`。

實作順序：

1. 定義 retrieval profile、允許的 `source_type`、新鮮度與衝突規則。
2. 決定沿用 `VerifiedRAGResponse 1.1`，或建立新的 major schema。
3. 建立 `backend/mcp_servers/<domain>.py`，提供 `ask_<domain>` 與
   `retrieve_<domain>_evidence`。
4. 新增專用 Service／Adapter，並保留 Company、Period、Evidence Validator 等共用控制。
5. 在 `backend/scripts/run_local.py`、部署服務、port／URL 設定與健康檢查加入新 Server。
6. 若前端也要使用，擴充 `PublicMCPChatService.select_tool`；不可只依模糊 LLM 自由選 Tool。
7. 更新 `export_mcp_spec.py`，重新產生 `docs/mcp-tools.json`。
8. 新增 Golden Set、負面案例、跨來源隔離與負載測試。

### 何時必須分成新 MCP

- Source allowlist 或法律責任不同。
- 更新時效與快取策略不同。
- 回應需要完全不同的 display/schema。
- 需要不同 IAM scope 或資料授權。
- 混入既有 Top-K 會降低財務或逐字稿準確率。

## 6. 認證、韌性與併發

- 每個外部 MCP 使用獨立 Credential，禁止共用本專案公開 Token。
- 僅允許固定 HTTPS host；不得接受 Redirect 到未核准 host。
- 設定 connect/read/total timeout、最大 Response、連線池與並發上限。
- 只對唯讀且冪等的 Tool 進行有限次重試，使用 exponential backoff＋jitter。
- Provider 失敗時，非必要來源應隔離並產生 warning；必要來源失敗則拒答。
- 外部 MCP 的 Rate Limit 必須納入本專案容量模型，正式 LLM/DB/MCP 一起壓測。
- Log 保存 provider、tool、trace ID、latency、status；不得保存 Token。

## 7. 驗收測試

新增 MCP 至少要有：

- [ ] MCP initialize／tools list 成功，Schema 已保存。
- [ ] 缺少或錯誤認證會失敗。
- [ ] Tool 名稱不與現有 Server 衝突。
- [ ] 正確公司、期間與來源可轉為合法 Evidence。
- [ ] 錯公司、錯期間、未核准 source type 被拒絕。
- [ ] Future／missing period 不會 fallback 到其他期間。
- [ ] Source Preview 可由 citation 重現原始記錄。
- [ ] Provider timeout、429、503、無效 JSON 與部分資料不會產生未驗證答案。
- [ ] 混合來源衝突會拒答或明確警告。
- [ ] Golden Set 的檢索、引用、數值與跨公司隔離通過。
- [ ] 正式環境 query mix 的 p95、p99、錯誤率與 backpressure 達 SLA。

完成後執行：

```bash
make test
make export-api
make export-mcp
npm --prefix frontend run build
```

若有正式答案 LLM，另執行：

```bash
cd backend
../.venv/bin/python -m scripts.evaluate_llm_behavior --live-llm
```

## 8. 版本與發布

只新增內部 Provider 且公開 Envelope／語意不變，可維持 Schema `1.1`，但必須更新
`data_versions`、coverage 與 Provider 文件。新增公開 Tool 是 additive change；移除／改名 Tool、
改變必要欄位、status、citation 或 source allowlist 則是 breaking change，依
[`MCP_API_DESIGN_AND_CHANGE_GUIDE.md`](MCP_API_DESIGN_AND_CHANGE_GUIDE.md) 升版。

正式發布前同步更新 README、Architecture、Configuration、Deployment、Provider Handoff、
Product Readiness、`mcp-tools.json` 與相關 Golden Set。沒有通過上述驗收前，新 MCP 應維持
disabled／unapproved，不得進入正式回答路徑。

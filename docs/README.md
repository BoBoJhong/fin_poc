# 文件總覽

本專案只維護三份主要人工規格。一般使用、交付與開發只需依角色閱讀其中一份，不需要把
所有技術附錄交給同事。

## 三份正式規格

| 規格 | 對象 | 內容 |
|---|---|---|
| [專案規格](PROJECT_SPEC.md) | 產品負責人、維護者、部署人員 | 產品範圍、架構、啟動、資料接入、部署、可靠度、併發與驗收 |
| [MCP 對外交付規格](MCP_PROVIDER_HANDOFF_SPEC.md) | 使用 MCP 的同事／外部 Agent 團隊 | Endpoint、認證、Tools、Input／Output、citation、status、重試與串接驗收 |
| [新增 MCP 擴充規格](ADDING_EXTERNAL_MCP.md) | 要把其他 MCP 整入本專案的開發者 | 整合模式、Tool allowlist、Evidence Adapter、安全、測試與版本政策 |

### 怎麼選

- 要了解或接手本專案：只看「專案規格」。
- 要把目前 MCP 給同事：只提供「MCP 對外交付規格」及機器 Schema。
- 要新增新聞、法規、ESG 或其他 MCP：看「新增 MCP 擴充規格」。

## 提供給同事的最小交付包

1. [MCP 對外交付規格](MCP_PROVIDER_HANDOFF_SPEC.md)。
2. [mcp-tools.json](mcp-tools.json)。
3. 正式 Financial／Earnings Call MCP HTTPS URL。
4. 支援的公司、期間、來源、Rate Limit 與 SLA。
5. 透過獨立安全管道提供的 Token／OAuth 資訊。

不要把內部 Architecture、DB Mapping、部署密碼、ports `8001/8002` 或所有技術附錄交給
一般 MCP 使用者。

## 機器規格

這兩份是生成產物，不算人工閱讀規格，也不應手動修改：

| 檔案 | 用途 | 更新方式 |
|---|---|---|
| [openapi.json](openapi.json) | HTTP API Schema | `make export-api` |
| [mcp-tools.json](mcp-tools.json) | MCP Tool Input／Output Schema | MCP 運行時執行 `make export-mcp` |

## 內部技術附錄

下列文件保留是因為包含實作、欄位或營運細節，但不是第四、第五份主要規格。只有遇到對應
工作時才查閱，內容變更必須回寫三份正式規格中受影響的摘要與政策。

| 附錄 | 用途 |
|---|---|
| [Architecture](../ARCHITECTURE.md) | 元件、資料流與安全邊界的詳細設計 |
| [HTTP API Reference](API_REFERENCE.md) | REST／SSE Route 細節 |
| [Embedding and Chunking Research Notes](EMBEDDING_CHUNKING_RESEARCH.md) | 長文件切分、細粒度檢索論文與 parent-child 設計提案 |
| [Neo4j Earnings-Call Graph](NEO4J_EARNINGS_CALL_GRAPH.md) | 法說會、講者、職稱、逐字 turn 與 embedding chunk 的固定圖譜契約 |
| [Configuration](CONFIGURATION.md) | 環境變數完整清單 |
| [Deployment](DEPLOYMENT.md) | systemd、Nginx、Firewall 與 Rollback 操作 |
| [External Integration](EXTERNAL_INTEGRATION_GUIDE.md) | LLM、SQL DB、REST API Mapping 細節 |
| [Financial Schema v2](FINANCIAL_DATA_SPEC.md) | 財務指標、精確值、維度與版本資料模型 |
| [MCP Change Guide](MCP_API_DESIGN_AND_CHANGE_GUIDE.md) | Breaking change 與 Schema 升版細節 |
| [MCP Output Spec](VERIFIED_RAG_MCP_OUTPUT_SPEC.md) | 完整 Runtime 欄位定義 |
| [Product Readiness](PRODUCT_READINESS.md) | 測試結果、容量基準與尚未完成事項 |
| [機密引用範本](PRIVATE_SPEC_REFERENCES.example.md) | 內部機密文件位置範本，不保存秘密 |

## 規格優先順序

若內容衝突，依下列順序處理：

1. Runtime Pydantic Schema、驗證程式與自動測試。
2. 從運行服務產生的 `openapi.json`、`mcp-tools.json`。
3. 三份正式規格。
4. 內部技術附錄。
5. README 摘要。

發現不一致時，必須同步 Runtime、測試、機器 Schema 與受影響的正式規格，不能只修改其中
一份說明文件。

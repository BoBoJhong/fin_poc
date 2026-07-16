# 公司機密規格引用表（範本）

> 本檔只保存「引用資訊」，不得貼入 API Key、Token、密碼、內部 Schema、Prompt、真實資料或其他機密內容。  
> 請複製為 `PRIVATE_SPEC_REFERENCES.md` 後在公司內部填寫；實際檔案已被 `.gitignore` 排除。

| 規格 ID | 用途 | 公司內部文件編號／連結代號 | 版本 | 負責單位／窗口 | 對應程式替換點 | 確認狀態 |
| --- | --- | --- | --- | --- | --- | --- |
| SPEC-LLM-001 | 公司 LLM API Contract | `<internal-reference-only>` | `<version>` | `<owner>` | `backend/app/llm.py` | 待確認 |
| SPEC-DB-001 | SQLite Schema／MariaDB→SQLite Mapping | `<internal-reference-only>` | `<version>` | `<owner>` | `backend/app/repositories.py` | 待確認 |
| SPEC-COMPANY-001 | `co_code` Master／Alias／授權集合 | `<internal-reference-only>` | `<version>` | `<owner>` | `backend/app/main.py`、Company Resolver | 待確認 |
| SPEC-GRAPH-001 | 最小 Graph Ontology／Provenance | `<internal-reference-only>` | `<version>` | `<owner>` | `backend/app/repositories.py`、`backend/scripts/init_data.py` | 待確認 |
| SPEC-ACCEPT-001 | 真實 Golden Set／驗收規則 | `<internal-reference-only>` | `<version>` | `<owner>` | `eval/`、`backend/scripts/evaluate.py` | 待確認 |

## 使用規則

- Git 只提交本範本，不提交填寫後的私密引用表。
- 程式碼及公開文件只引用 `SPEC-...` ID，不複製公司規格內容。
- Secret 只能透過公司核准的 Secret Manager 或本機未版控 `.env` 注入。
- 規格變更時更新版本與確認狀態，並重新執行對應測試。

# Embedding and Chunking Research Notes

> 文件狀態：第一階段 bounded semantic blocks 已實作；hierarchical parent-child retrieval
> 仍是待 Golden Set 驗證的第二階段提案。

本文件整理與本專案長文件、SEC filing 及 earnings-call transcript 檢索相關的研究，並說明
哪些設計適合目前的可驗證金融 RAG 架構。數值型財務事實仍應走結構化查詢；本文只討論敘事
文件與 metric alias 的 embedding retrieval。

## Baseline findings and phase-one status

舊版 SEC ingestion 會忽略少於 40 characters 的行、允許單一超長段落突破 1,200
characters，並在 36 chunks 後停止。舊版 transcript ingestion 則會為極短 speaker turn
建立獨立向量。這些行為是本次改善的 baseline，不再是目前實作。

因此主要風險不是單純超過模型 context window，而是：

- 大型 chunk 將多個主題壓縮成一個向量，降低特定事實的可檢索性。
- 過短 chunk 缺少公司、期間、speaker 或主題上下文。
- SEC 的短行過濾與 `max_chunks` 會造成不可檢索的原文缺口。
- 固定長度邊界可能分離問題與回答、主張與限定條件，或風險與影響。

第一階段目前實作：

- SEC 預設 block 範圍為 240–1,200 characters；完整文件小於下限時除外。
- SEC 保留 selected section 的每一個非空白行，超長段落依標點或空白硬切，不再設預設
  document-level chunk count。若呼叫端明確設定上限，超過時報錯而不是截斷。
- Transcript 儲存文字（包含 speaker/section labels）嚴格不超過 1,400 characters。
- 少於 160 characters 的 transcript turn 優先併入同 section 的相鄰 block；合併後保存
  `speakers`，並以內容最多的 speaker 作既有 `speaker` 欄位。
- Re-ingestion 會刪除同一 source 已不在新版集合中的 stale chunks，避免新舊向量並存。
- 明確包含多個子問題的英文 query 會保留原句並拆成最多三個 facets，以各 facet cosine
  score 的平均值重排；全文搜尋也限定在同公司、期間、source type 與指定 speaker。
- 目前仍以 characters 控制 hard bound。Repo 未包含與 Ollama Qwen GGUF 完全一致的
  tokenizer；在未能保證 tokenizer parity 前，不以估算值冒充精確 token count。

Qwen3-Embedding-0.6B 的官方 model card 標示 32K context length，因此本專案觀察到的約
3,000-character chunk 通常不是 context overflow；chunking 的主要目標仍是語意純度、
完整性及可引用性，而不是把輸入逼近模型上限。

## Proposed second stage

本專案最適合先採用以 **Dense X Retrieval** 為主要依據的 hierarchical parent-child
retrieval：

```text
verbatim Document
  -> structure-aware parent segment (citation and generation context)
       -> fine-grained child fact / proposition (embedding retrieval unit)
       -> fine-grained child fact / proposition

query -> retrieve child units -> aggregate/rerank parents -> return verbatim parent evidence
```

設計原則：

1. Parent 保存未改寫原文、來源位置與 hash，用於 answer context 及 citation。
2. Child 是較細的句子、claim 或 proposition，用於提高檢索精度，並保留 `parent_id` 與
   原文 span。
3. 衍生或由模型改寫的 proposition 不得冒充 quoted text；引用必須回到 parent 原文。
4. SEC 優先依 Item、heading、paragraph 與 table boundary 切分；transcript 優先保留
   speaker turn 及 Q&A exchange。
5. 長度限制改用實際 tokenizer token count。過長段落必須硬切，短行應與相鄰內容合併，
   不得因文件總 chunk 數而靜默截斷。
6. Child 命中後回填 parent，必要時再加入相鄰 parent，減少固定 overlap 所造成的索引膨脹。
7. Query embedding 已加入與金融證據檢索相符的英文 instruction；document embedding 不加
   query instruction。自然語言唯一命中已知 speaker 時，先以公司、期間與 speaker 篩選，
   再計算 cosine ranking。

第一階段可以使用原始句子或規則式 claim units 作 child，先避免 LLM proposition rewriting
改變數字、否定詞或限定條件。受約束的 proposition extraction 應在 Golden Set 證明收益後
再導入。

## Research comparison

| Research | Main contribution | Relevance to this repository | Adoption note |
|---|---|---|---|
| Dense X Retrieval | 比較 document、passage、sentence 與 proposition 等檢索粒度；提出自足的 atomic proposition 作為 retrieval unit | 直接處理大 chunk 語意混雜與小 chunk 缺乏自足性的問題 | **Primary reference**；child 用於檢索，parent 原文用於可驗證引用 |
| Document Segmentation Matters / PIC | 以文件摘要作 pseudo-instruction，按語意動態聚合相關句子 | 可改善 SEC heading/paragraph 形成的機械邊界 | 適合作為第二階段 semantic segmentation 實驗；需衡量摘要品質與 ingestion 成本 |
| FunnelRAG | 以 coarse-to-fine、large-to-small 的多階段方式檢索 | 適合在 Neo4j 建立 child 命中、parent aggregation、hybrid rerank 流程 | 可作 parent-child retrieval orchestration 的主要參考 |
| Late Chunking | 先用 long-context model 編碼全文 token，再於 pooling 前形成 contextual chunk embeddings | 能處理 chunk 脫離全文後失去指涉上下文的問題 | 目前 Ollama `/api/embed` 不提供 token hidden states，且 Qwen3 官方範例使用 last-token pooling；不可直接套用，需更換 serving/pooling stack |
| LumberChunker | 由 LLM 動態尋找長篇敘事中的主題轉折點 | 對長 transcript 的語意段落可能有幫助 | 評估資料以 narrative books 為主；金融文件上線前需獨立驗證成本、穩定性與 citation boundary |
| RAPTOR | 對 chunks 遞迴聚類與摘要，建立不同抽象層級的檢索樹 | 適合需要跨多段彙整的全局問題 | 對目前精確、可追溯的金融引用可能過重；摘要節點只能作 routing，不能作原文證據 |

## Why Late Chunking is not the first implementation

Late Chunking 很符合「短 chunk 缺乏周邊上下文」的問題，但它需要在 transformer 輸出 token
representations 後自行 pooling。目前 `Ollama /api/embed` 回傳的是完成 pooling 的單一向量，
無法依 parent 內的 child span 重新 pooling。Qwen3-Embedding 官方 Transformers 範例也使用
last-token pooling，而 Late Chunking 論文描述的通用條件以可控制的 mean pooling 為核心。

因此它應保留為替換 embedding runtime 後的研究項目，而不是目前 ingestion 修正的前置條件。

## Qwen3 Embedding usage

Qwen3-Embedding-0.6B 官方 model card 說明：

- Context length 為 32K，最大 embedding dimension 為 1024。
- Query 可使用 task-specific instruction，document 不需加入該 instruction。
- 官方報告多數 retrieval 情境加入 instruction 可改善約 1%–5%，且建議 multilingual task
  使用英文 instruction。

目前使用的 query 格式：

```text
Instruct: Retrieve verbatim financial-report or earnings-call evidence that answers the query, scoped to the specified company and reporting period.
Query: <normalized user query>
```

此格式與 multi-facet fusion 已以現有 transcript Golden Set 驗證；六個中英文、跨季度及
speaker/section 測試案例的 Recall@5 與引用支持率皆通過。此小型結果只證明目前資料集的回歸
行為，仍不得直接外推為未匯入公司或任意問題皆能回答。

## Evaluation plan

至少比較以下組別：

1. Current character-based chunking baseline。
2. Token-aware、structure-based parent chunks，不建立 child index。
3. Parent-child retrieval，child 使用原始句子或規則式 claim units。
4. 若第三組有效，再評估 PIC segmentation 或受約束的 proposition extraction。

主要指標：

- Source character/token coverage，目標為 100%，且不得靜默截斷。
- Retrieval Recall@5、MRR 或 nDCG@10。
- 引用原文及 locator 命中率。
- 公司、期間與 source-type isolation rate。
- 數字、單位、否定詞與條件限定的保留率。
- Index size、ingestion latency、query latency 與 embedding 呼叫數。

任何由模型生成的 child text 都應另外測試：child 與原文 span 是否一致、數字是否完全相同、
是否新增原文不存在的因果或時間關係。

## References

1. Chen, T., et al. (2024). [Dense X Retrieval: What Retrieval Granularity Should We
   Use?](https://aclanthology.org/2024.emnlp-main.845/). EMNLP 2024.
   DOI: [10.18653/v1/2024.emnlp-main.845](https://doi.org/10.18653/v1/2024.emnlp-main.845).
2. Wang, Z., et al. (2025). [Document Segmentation Matters for Retrieval-Augmented
   Generation](https://aclanthology.org/2025.findings-acl.422/). Findings of ACL 2025.
   DOI: [10.18653/v1/2025.findings-acl.422](https://doi.org/10.18653/v1/2025.findings-acl.422).
3. Zhong, Z., et al. (2025). [Mix-of-Granularity: Optimize the Chunking Granularity for
   Retrieval-Augmented Generation](https://aclanthology.org/2025.coling-main.384/). COLING 2025.
   DOI: [10.18653/v1/2025.coling-main.384](https://doi.org/10.18653/v1/2025.coling-main.384).
4. Zhao, X., et al. (2025). [FunnelRAG: A Coarse-to-Fine Progressive Retrieval Paradigm for
   RAG](https://aclanthology.org/2025.findings-naacl.165/). Findings of NAACL 2025.
   DOI: [10.18653/v1/2025.findings-naacl.165](https://doi.org/10.18653/v1/2025.findings-naacl.165).
5. Günther, M., et al. (2024). [Late Chunking: Contextual Chunk Embeddings Using Long-Context
   Embedding Models](https://arxiv.org/abs/2409.04701). arXiv:2409.04701.
6. Duarte, A. V., et al. (2024). [LumberChunker: Long-Form Narrative Document
   Segmentation](https://aclanthology.org/2024.findings-emnlp.377/). Findings of EMNLP 2024.
   DOI: [10.18653/v1/2024.findings-emnlp.377](https://doi.org/10.18653/v1/2024.findings-emnlp.377).
7. Sarthi, P., et al. (2024). [RAPTOR: Recursive Abstractive Processing for Tree-Organized
   Retrieval](https://openreview.net/forum?id=GN921JHCRw). ICLR 2024.
8. Zhang, Y., et al. (2025). [Qwen3 Embedding: Advancing Text Embedding and Reranking Through
   Foundation Models](https://arxiv.org/abs/2506.05176). arXiv:2506.05176. See also the official
   [Qwen3-Embedding-0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B).

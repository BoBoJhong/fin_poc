import {
  FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { fetchSource, streamChat } from "./api";
import type {
  ChatResult,
  Citation,
  SourceLocator,
  SourcePreview,
  TranscriptDisplay,
} from "./types";

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  result?: ChatResult;
  status?: string;
  error?: boolean;
}

const testCases = [
  {
    label: "Microsoft 法說 · Q3",
    expected: "官方逐字稿：Prepared Remarks 與展望",
    query: "微軟 2026 Q1 法說會中，Amy Hood 對下一季公司總營收的展望是多少？",
    tone: "pass",
  },
  {
    label: "Microsoft 法說 · Q2",
    expected: "官方逐字稿：Q&A、發言人與季度隔離",
    query: "What did Amy Hood say in Microsoft 2025 Q4 earnings call Q&A about connecting CapEx to Azure revenue?",
    tone: "pass",
  },
  {
    label: "Apple · SEC",
    expected: "真實 10-Q：財務＋風險＋官方來源",
    query: "蘋果 2026 Q1 的營收、毛利率與主要風險是什麼？",
    tone: "pass",
  },
  {
    label: "Microsoft · SEC",
    expected: "真實 10-Q：跨語言檢索與引用",
    query: "What were Microsoft 2026 Q1 revenue and gross margin, and its main risks?",
    tone: "pass",
  },
  {
    label: "NVIDIA · SEC",
    expected: "真實 10-Q：公司隔離與來源回查",
    query: "輝達 2026 Q1 的營收、毛利率與供應鏈風險？",
    tone: "pass",
  },
  {
    label: "綜合 RAG",
    expected: "財務＋文件＋Graph，應通過",
    query: "壓測企業0200 2026 Q2 的營收、毛利率與主要風險？",
    tone: "pass",
  },
  {
    label: "Graph 關聯",
    expected: "應回傳產品與風險路徑",
    query: "壓測企業0120 的產品P0120與供應鏈節點風險R0120有什麼關聯？",
    tone: "pass",
  },
  {
    label: "股票代碼",
    expected: "應解析為 TST0050",
    query: "TST0050 2026 Q2 的營收是多少？",
    tone: "pass",
  },
  {
    label: "公司 Alias",
    expected: "應解析為 TST0030",
    query: "壓企0030 2026 Q2 的毛利率是多少？",
    tone: "pass",
  },
  {
    label: "期間拒答",
    expected: "無 2035 Q4 證據，應拒答",
    query: "壓測企業0020 2035 Q4 的營收是多少？",
    tone: "reject",
  },
  {
    label: "公司隔離",
    expected: "DEMO02 無風險文件，應拒答",
    query: "示範製造有哪些營運風險？",
    tone: "reject",
  },
  {
    label: "多公司",
    expected: "應要求一次只指定一家公司",
    query: "比較壓測企業0001與壓測企業0002的營收",
    tone: "guard",
  },
  {
    label: "缺少公司",
    expected: "應要求補充公司名稱或代碼",
    query: "2026 Q2 的營收是多少？",
    tone: "guard",
  },
] as const;

const sourceLabels: Record<string, string> = {
  financial_report: "財報",
  transcript: "法說逐字稿",
  url: "網頁快照",
  database: "DB 紀錄",
  graph: "Graph 路徑",
};

function AnswerText({
  text,
  citations,
  onCitation,
  activeCitationId,
  streaming,
}: {
  text: string;
  citations: Citation[];
  onCitation: (citation: Citation) => void;
  activeCitationId?: string;
  streaming?: boolean;
}) {
  const parts = text.split(/(\[\d+\]|\*\*[^*]+\*\*)/g);
  return (
    <div className="answer-text">
      {parts.map((part, index) => {
        const citationMatch = part.match(/^\[(\d+)]$/);
        if (citationMatch) {
          const citation = citations.find((item) => item.index === Number(citationMatch[1]));
          return citation ? (
            <button
              className={`inline-citation${citation.evidence_id === activeCitationId ? " active" : ""}`}
              key={`${part}-${index}`}
              onClick={() => onCitation(citation)}
              title={citation.title}
            >
              {part}
            </button>
          ) : (
            part
          );
        }
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>;
        }
        return <span key={`${part}-${index}`}>{part}</span>;
      })}
      {streaming && <span className="stream-cursor" aria-hidden="true" />}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="copy-button"
      onClick={() => {
        void navigator.clipboard.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      }}
    >
      {copied ? "✓ 已複製" : "複製"}
    </button>
  );
}

function TranscriptDisplayCard({
  display,
  citations,
  onCitation,
  activeCitationId,
}: {
  display: TranscriptDisplay;
  citations: Citation[];
  onCitation: (citation: Citation) => void;
  activeCitationId?: string;
}) {
  return (
    <section className="transcript-display">
      <h3>{display.title}</h3>
      <div className="transcript-field">
        <b>發表人：</b>
        <span>{display.speakers.length ? display.speakers.join("、") : "未標示"}</span>
      </div>
      <div className="transcript-field transcript-content">
        <b>內文：</b>
        <AnswerText
          text={display.content}
          citations={citations}
          onCitation={onCitation}
          activeCitationId={activeCitationId}
        />
      </div>
      <details className="transcript-sources">
        <summary>來源內容（{display.sources.length}）</summary>
        {display.sources.map((source) => {
          const citation = citations.find((item) => item.index === source.citation_index);
          return (
            <button
              type="button"
              key={`${source.citation_index}-${source.locator.paragraph_id || "source"}`}
              onClick={() => citation && void onCitation(citation)}
            >
              <span>{source.speaker || "未標示發表人"} · {source.section || "逐字稿"}</span>
              <q>{source.source_content}</q>
            </button>
          );
        })}
      </details>
    </section>
  );
}

interface HighlightRange {
  start: number;
  end: number;
}

function findHighlightRanges(text: string, quotedText?: string): HighlightRange[] {
  const quote = quotedText?.trim();
  if (!quote) return [];

  const exactStart = text.indexOf(quote);
  if (exactStart >= 0) return [{ start: exactStart, end: exactStart + quote.length }];

  const fragments = [...new Set(
    quote
      .split(/[，。；;！？!?]/)
      .map((fragment) => fragment.trim())
      .filter((fragment) => fragment.length >= 6),
  )].sort((left, right) => right.length - left.length);
  const ranges: HighlightRange[] = [];
  for (const fragment of fragments) {
    let offset = text.indexOf(fragment);
    while (offset >= 0) {
      ranges.push({ start: offset, end: offset + fragment.length });
      offset = text.indexOf(fragment, offset + fragment.length);
    }
  }

  return ranges
    .sort((left, right) => left.start - right.start)
    .reduce<HighlightRange[]>((merged, range) => {
      const previous = merged.at(-1);
      if (previous && range.start <= previous.end) {
        previous.end = Math.max(previous.end, range.end);
      } else {
        merged.push({ ...range });
      }
      return merged;
    }, []);
}

function HighlightedSourceText({
  text,
  ranges,
}: {
  text: string;
  ranges: HighlightRange[];
}) {
  const firstMarkRef = useRef<HTMLElement>(null);
  const firstStart = ranges.length ? ranges[0].start : -1;

  useEffect(() => {
    if (firstStart >= 0) {
      firstMarkRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [text, firstStart]);

  const content: ReactNode[] = [];
  let cursor = 0;
  ranges.forEach((range, index) => {
    if (range.start > cursor) content.push(text.slice(cursor, range.start));
    content.push(
      <mark
        className="source-highlight"
        key={`${range.start}-${index}`}
        ref={index === 0 ? firstMarkRef : undefined}
      >
        {text.slice(range.start, range.end)}
      </mark>,
    );
    cursor = range.end;
  });
  if (cursor < text.length) content.push(text.slice(cursor));
  return <pre className="transcript-preview">{content.length ? content : text}</pre>;
}

function LocatorChips({ locator }: { locator: SourceLocator }) {
  const chips: Array<[string, string]> = [];
  if (locator.paragraph_id) chips.push(["段落", locator.paragraph_id]);
  if (locator.page != null) chips.push(["頁", String(locator.page)]);
  if (locator.timestamp) chips.push(["時間", locator.timestamp]);
  if (locator.table) chips.push(["資料表", locator.table]);
  if (locator.primary_key) chips.push(["主鍵", locator.primary_key]);
  if (!chips.length) return null;
  return (
    <span className="locator-chips">
      {chips.map(([label, value]) => (
        <span className="locator-chip" key={label}>
          <b>{label}</b>
          {value}
        </span>
      ))}
    </span>
  );
}

function SourcePanel({
  preview,
  citation,
  loading,
  error,
}: {
  preview?: SourcePreview;
  citation?: Citation;
  loading: boolean;
  error?: string;
}) {
  const [tab, setTab] = useState<"snapshot" | "live">("snapshot");

  if (loading) {
    return <div className="source-empty"><span className="loader" />正在載入來源…</div>;
  }
  if (error) return <div className="source-empty error-box">{error}</div>;
  if (!preview) {
    return (
      <div className="source-empty">
        <div className="empty-icon">↗</div>
        <h3>來源核對區</h3>
        <p>點擊回答中的引註，即可查看原始段落、DB 紀錄或 Graph 路徑。</p>
      </div>
    );
  }

  const highlightRanges = preview.text
    ? findHighlightRanges(preview.text, citation?.quoted_text)
    : [];
  const quoteLocated = highlightRanges.length > 0;

  return (
    <div className="source-content">
      <div className="source-heading">
        <span className="source-type">{sourceLabels[preview.source_type]}</span>
        <h2>{preview.title}</h2>
        <p>{preview.source_id}</p>
      </div>

      {citation && (
        <div className="citation-strip" aria-label="引用出處">
          <span className="citation-badge">引註 [{citation.index}]</span>
          <LocatorChips locator={preview.locator} />
          <span className={`locate-state ${quoteLocated ? "located" : "excerpt-only"}`}>
            {quoteLocated ? `已定位 ${highlightRanges.length} 段原文` : "未能於原文定位"}
          </span>
        </div>
      )}

      {preview.text && (
        <section className="reading-pane">
          <div className="source-section-label">來源原文</div>
          <HighlightedSourceText text={preview.text} ranges={highlightRanges} />
        </section>
      )}

      {citation && !quoteLocated && citation.quoted_text && (
        <section className="excerpt-fallback">
          <div className="source-section-label">引用摘錄</div>
          <blockquote>{citation.quoted_text}</blockquote>
        </section>
      )}

      {(preview.snapshot_html || preview.live_url) && (
        <div className="preview-tabs">
          <button className={tab === "snapshot" ? "active" : ""} onClick={() => setTab("snapshot")}>回答快照</button>
          <button className={tab === "live" ? "active" : ""} onClick={() => setTab("live")} disabled={!preview.live_url}>Live URL</button>
        </div>
      )}

      {tab === "snapshot" && preview.snapshot_html && (
        <iframe
          className="source-frame"
          title="source snapshot"
          srcDoc={preview.snapshot_html}
          sandbox=""
        />
      )}
      {tab === "live" && preview.live_url && (
        <div className="live-preview">
          <div className="iframe-warning">
            外部網站可能禁止 iframe；回答稽核仍以「回答快照」為準。
            <a href={preview.live_url} target="_blank" rel="noreferrer">另開頁面</a>
          </div>
          <iframe
            className="source-frame"
            title="live source"
            src={preview.live_url}
            sandbox="allow-forms allow-popups"
          />
        </div>
      )}
      {preview.database_record && (
        <pre className="record-preview">{JSON.stringify(preview.database_record, null, 2)}</pre>
      )}
      {preview.locator.graph_path && preview.locator.graph_path.length > 0 && (
        <div className="graph-path">
          {preview.locator.graph_path.map((node, index) => (
            <div key={`${node}-${index}`}>
              <span>{node}</span>
              {index < preview.locator.graph_path!.length - 1 && <b>↓</b>}
            </div>
          ))}
        </div>
      )}

      <dl className="source-meta">
        {citation?.evidence_id && <><dt>Evidence</dt><dd>{citation.evidence_id}</dd></>}
        {citation?.co_code && <><dt>公司</dt><dd>{citation.co_code}</dd></>}
        <dt>Source ID</dt><dd>{preview.source_id}</dd>
        {preview.locator.paragraph_id && <><dt>段落</dt><dd>{preview.locator.paragraph_id}</dd></>}
        {preview.locator.timestamp && <><dt>時間</dt><dd>{preview.locator.timestamp}</dd></>}
        {preview.captured_at && <><dt>擷取時間</dt><dd>{preview.captured_at}</dd></>}
        {preview.content_hash && <><dt>內容雜湊</dt><dd>{preview.content_hash}</dd></>}
      </dl>
    </div>
  );
}

export default function App() {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "請在問題中輸入公司名稱、簡稱或股票代碼，再詢問財報、法說會、財務數字或關聯。所有 DEMO 資料均為虛構。",
    },
  ]);
  const [preview, setPreview] = useState<SourcePreview>();
  const [activeCitation, setActiveCitation] = useState<Citation>();
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string>();
  const requestId = useRef(0);
  const conversationRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const sourcePanelRef = useRef<HTMLElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [panelWidth, setPanelWidth] = useState(() => {
    const saved = Number(localStorage.getItem("source-panel-width"));
    return saved >= 320 && saved <= 900 ? saved : 440;
  });
  const [sourcePanelCollapsed, setSourcePanelCollapsed] = useState(
    () => localStorage.getItem("source-panel-collapsed") === "true",
  );
  const [resizing, setResizing] = useState(false);
  const resizeState = useRef<{ startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    localStorage.setItem("source-panel-width", String(panelWidth));
  }, [panelWidth]);

  useEffect(() => {
    localStorage.setItem("source-panel-collapsed", String(sourcePanelCollapsed));
  }, [sourcePanelCollapsed]);

  function beginPanelResize(event: ReactPointerEvent<HTMLDivElement>) {
    resizeState.current = { startX: event.clientX, startWidth: panelWidth };
    setResizing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function movePanelResize(event: ReactPointerEvent<HTMLDivElement>) {
    const state = resizeState.current;
    if (!state) return;
    const limit = Math.max(320, Math.round(window.innerWidth * 0.6));
    setPanelWidth(Math.min(Math.max(state.startWidth + (state.startX - event.clientX), 320), limit));
  }

  function endPanelResize(event: ReactPointerEvent<HTMLDivElement>) {
    resizeState.current = null;
    setResizing(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  useEffect(() => {
    const node = conversationRef.current;
    if (node && stickToBottom.current) node.scrollTop = node.scrollHeight;
  }, [messages]);

  function handleConversationScroll() {
    const node = conversationRef.current;
    if (!node) return;
    stickToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
  }

  const latestResult = useMemo(
    () => [...messages].reverse().find((message) => message.result)?.result,
    [messages],
  );

  async function openCitation(citation: Citation) {
    setSourcePanelCollapsed(false);
    setActiveCitation(citation);
    setPreviewLoading(true);
    setPreviewError(undefined);
    if (window.matchMedia("(max-width: 900px)").matches) {
      sourcePanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    try {
      const source = await fetchSource(citation.source_id, citation.co_code);
      setPreview({
        ...source,
        locator: { ...source.locator, ...citation.locator },
      });
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : "來源載入失敗");
    } finally {
      setPreviewLoading(false);
    }
  }

  function closePreview() {
    setPreview(undefined);
    setActiveCitation(undefined);
    setPreviewError(undefined);
  }

  async function ask(rawQuery: string) {
    const query = rawQuery.trim();
    if (!query || busy) return;
    const id = `request-${++requestId.current}`;
    stickToBottom.current = true;
    setMessages((current) => [
      ...current,
      { id: `${id}-user`, role: "user", text: query },
      { id, role: "assistant", text: "", status: "正在準備檢索…" },
    ]);
    setInput("");
    if (textareaRef.current) textareaRef.current.style.height = "";
    setBusy(true);
    try {
      await streamChat(query, (event) => {
        setMessages((current) =>
          current.map((message) => {
            if (message.id !== id) return message;
            if (event.type === "status") return { ...message, status: event.data.message };
            if (event.type === "token") return { ...message, text: message.text + event.data.text };
            return { ...message, text: event.data.answer, result: event.data, status: undefined };
          }),
        );
      });
    } catch (error) {
      setMessages((current) =>
        current.map((message) =>
          message.id === id
            ? {
                ...message,
                text: error instanceof Error ? error.message : "系統暫時無法回答",
                status: undefined,
                error: true,
              }
            : message,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(input);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark">FI</div>
        <div>
          <h1>Financial Intelligence</h1>
          <p>GraphRAG × Controlled Agents × Verifiable Sources</p>
        </div>
        <div className="topbar-actions">
          <span className="environment"><i />Verified RAG</span>
          <span className="environment">全公司主檔</span>
        </div>
      </header>

      <main
        className={`workspace${resizing ? " resizing" : ""}${sourcePanelCollapsed ? " panel-collapsed" : ""}`}
        style={{ "--panel-w": `${panelWidth}px` } as CSSProperties}
      >
        <section className="chat-panel">
          <div className="conversation" ref={conversationRef} onScroll={handleConversationScroll}>
            {messages.map((message) => (
              <article className={`message ${message.role} ${message.error ? "error" : ""}`} key={message.id}>
                {message.role === "assistant" && <div className="avatar">AI</div>}
                <div className="message-body">
                  {message.role === "assistant" && <span className="role-label">Verified RAG</span>}
                  {message.status && !message.text && <div className="thinking"><span className="loader" />{message.status}</div>}
                  {message.result?.display ? (
                    <TranscriptDisplayCard
                      display={message.result.display}
                      citations={message.result.citations}
                      onCitation={openCitation}
                      activeCitationId={activeCitation?.evidence_id}
                    />
                  ) : message.text ? (
                    <AnswerText
                      text={message.text}
                      citations={message.result?.citations || []}
                      onCitation={openCitation}
                      activeCitationId={activeCitation?.evidence_id}
                      streaming={Boolean(message.status)}
                    />
                  ) : null}
                  {message.result && (
                    <div className="answer-footer">
                      <div className="citation-list">
                        {message.result.citations.map((citation) => (
                          <button
                            key={citation.evidence_id}
                            className={citation.evidence_id === activeCitation?.evidence_id ? "active" : ""}
                            onClick={() => void openCitation(citation)}
                          >
                            [{citation.index}] {sourceLabels[citation.source_type]}
                          </button>
                        ))}
                      </div>
                      <div className="answer-tools">
                        <CopyButton text={message.text} />
                      <span className={message.result.verification.passed ? "verified" : "unverified"}>
                        {message.result.verification.passed ? "✓ 已通過可靠度防線" : "! 驗證未通過"}
                      </span>
                      </div>
                    </div>
                  )}
                </div>
              </article>
            ))}
          </div>

          <div className="composer-zone">
            <details className="test-suite" open>
              <summary>
                <span>快速測試案例</span>
                <small>{testCases.length} 項 · 真實資料、拒答與防護</small>
              </summary>
              <div className="test-case-list">
                {testCases.map((testCase) => (
                  <button
                    className={`test-case ${testCase.tone}`}
                    key={testCase.label}
                    onClick={() => void ask(testCase.query)}
                    disabled={busy}
                    title={testCase.query}
                    type="button"
                  >
                    <b>{testCase.label}</b>
                    <span>{testCase.expected}</span>
                  </button>
                ))}
              </div>
            </details>
            <form className="composer" onSubmit={submit}>
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(event) => {
                  setInput(event.target.value);
                  const node = event.target;
                  node.style.height = "";
                  node.style.height = `${Math.min(node.scrollHeight, 130)}px`;
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    submit(event as unknown as FormEvent);
                  }
                }}
                placeholder="輸入公司名稱或股票代碼，例如：範例科技 2026 Q2 營收是多少？"
                rows={1}
              />
              <button className="send-button" type="submit" disabled={busy || !input.trim()} aria-label="送出問題">↑</button>
            </form>
            <div className="composer-note">
              <span>答案只使用授權 Evidence</span>
              {latestResult?.trace_id && <span>Trace: {latestResult.trace_id.slice(0, 8)}</span>}
            </div>
          </div>
        </section>

        <div
          className="panel-resizer"
          role="separator"
          aria-orientation="vertical"
          aria-label="拖曳調整來源面板寬度"
          title="拖曳調整寬度；雙擊還原預設"
          onPointerDown={beginPanelResize}
          onPointerMove={movePanelResize}
          onPointerUp={endPanelResize}
          onPointerCancel={endPanelResize}
          onDoubleClick={() => setPanelWidth(440)}
        />

        <aside
          className={`source-panel${sourcePanelCollapsed ? " collapsed" : ""}`}
          ref={sourcePanelRef}
        >
          <div className="panel-title">
            <span>Source Inspector</span>
            <div className="panel-title-side">
              {!sourcePanelCollapsed && <small>可稽核來源</small>}
              {!sourcePanelCollapsed && (preview || previewError) && (
                <button className="panel-close" onClick={closePreview} aria-label="關閉來源預覽">✕</button>
              )}
              <button
                className="panel-toggle"
                onClick={() => setSourcePanelCollapsed((current) => !current)}
                aria-label={sourcePanelCollapsed ? "展開來源核對區" : "收合來源核對區"}
                title={sourcePanelCollapsed ? "展開來源核對區" : "收合來源核對區"}
              >
                {sourcePanelCollapsed ? "‹" : "›"}
              </button>
            </div>
          </div>
          {!sourcePanelCollapsed && (
            <SourcePanel
              preview={preview}
              citation={activeCitation}
              loading={previewLoading}
              error={previewError}
            />
          )}
        </aside>
      </main>
    </div>
  );
}

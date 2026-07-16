import { FormEvent, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { fetchCompanies, fetchSource, streamChat, type CompanySummary } from "./api";
import type { ChatResult, Citation, SourcePreview } from "./types";

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  result?: ChatResult;
  status?: string;
  error?: boolean;
}

const suggestedQuestions = [
  "範例科技 2026 Q2 的營收和毛利率是多少？主要風險是什麼？",
  "範例科技的產品和營運風險有什麼 Graph 關聯？",
  "法說會中財務長對下半年風險說了什麼？",
];

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
}: {
  text: string;
  citations: Citation[];
  onCitation: (citation: Citation) => void;
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
              className="inline-citation"
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
    </div>
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
  const content: ReactNode[] = [];
  let cursor = 0;
  ranges.forEach((range, index) => {
    if (range.start > cursor) content.push(text.slice(cursor, range.start));
    content.push(
      <mark className="source-highlight" key={`${range.start}-${index}`}>
        {text.slice(range.start, range.end)}
      </mark>,
    );
    cursor = range.end;
  });
  if (cursor < text.length) content.push(text.slice(cursor));
  return <pre className="transcript-preview">{content.length ? content : text}</pre>;
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
        <section className="citation-note" aria-label="引用附註">
          <div className="citation-note-heading">
            <strong>引用附註 [{citation.index}]</strong>
            <span className={quoteLocated ? "located" : "excerpt-only"}>
              {quoteLocated ? `已定位 ${highlightRanges.length} 段原文` : "顯示引用摘錄"}
            </span>
          </div>
          <p>
            回答引用此來源的下列內容；黃色螢光表示在來源全文中實際比對到的文字。
          </p>
          <details open={!quoteLocated}>
            <summary>查看引用摘錄</summary>
            <blockquote>{citation.quoted_text}</blockquote>
          </details>
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
      {preview.text && (
        <section className="original-text-section">
          <div className="source-section-label">來源原文</div>
          <HighlightedSourceText text={preview.text} ranges={highlightRanges} />
        </section>
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
  const [coCode, setCoCode] = useState("DEMO01");
  const [companies, setCompanies] = useState<CompanySummary[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "請選擇公司並提出財報、法說會、財務數字或關聯問題。所有 DEMO 資料均為虛構。",
    },
  ]);
  const [preview, setPreview] = useState<SourcePreview>();
  const [activeCitation, setActiveCitation] = useState<Citation>();
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string>();
  const requestId = useRef(0);

  useEffect(() => {
    void fetchCompanies()
      .then((items) => {
        setCompanies(items);
        if (items.length && !items.some((item) => item.co_code === coCode)) {
          setCoCode(items[0].co_code);
        }
      })
      .catch(() => setCompanies([]));
  }, []);

  const latestResult = useMemo(
    () => [...messages].reverse().find((message) => message.result)?.result,
    [messages],
  );

  async function openCitation(citation: Citation) {
    setActiveCitation(citation);
    setPreviewLoading(true);
    setPreviewError(undefined);
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

  async function ask(rawQuery: string) {
    const query = rawQuery.trim();
    if (!query || busy) return;
    const id = `request-${++requestId.current}`;
    setMessages((current) => [
      ...current,
      { id: `${id}-user`, role: "user", text: query },
      { id, role: "assistant", text: "", status: "正在準備檢索…" },
    ]);
    setInput("");
    setBusy(true);
    try {
      await streamChat(query, coCode, (event) => {
        if (event.type === "result") setCoCode(event.data.co_code);
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
          <span className="environment"><i />Local PoC</span>
          <label>
            公司範圍
            <select value={coCode} onChange={(event) => setCoCode(event.target.value)} disabled={busy}>
              {(companies.length
                ? companies
                : [{ co_code: coCode, company_name: coCode }]
              ).map((company) => (
                <option key={company.co_code} value={company.co_code}>
                  {company.co_code} · {company.company_name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>

      <main className="workspace">
        <section className="chat-panel">
          <div className="conversation">
            {messages.map((message) => (
              <article className={`message ${message.role} ${message.error ? "error" : ""}`} key={message.id}>
                <div className="avatar">{message.role === "assistant" ? "AI" : "你"}</div>
                <div className="message-body">
                  <span className="role-label">{message.role === "assistant" ? "Financial Agent" : "User"}</span>
                  {message.status && !message.text && <div className="thinking"><span className="loader" />{message.status}</div>}
                  {message.text && (
                    <AnswerText
                      text={message.text}
                      citations={message.result?.citations || []}
                      onCitation={openCitation}
                    />
                  )}
                  {message.result && (
                    <div className="answer-footer">
                      <div className="citation-list">
                        {message.result.citations.map((citation) => (
                          <button key={citation.evidence_id} onClick={() => void openCitation(citation)}>
                            [{citation.index}] {sourceLabels[citation.source_type]}
                          </button>
                        ))}
                      </div>
                      <span className={message.result.verification.passed ? "verified" : "unverified"}>
                        {message.result.verification.passed ? "✓ 已通過雙層驗證" : "! 驗證未通過"}
                      </span>
                    </div>
                  )}
                </div>
              </article>
            ))}
          </div>

          <div className="composer-zone">
            {messages.length < 3 && (
              <div className="suggestions">
                {suggestedQuestions.map((question) => (
                  <button key={question} onClick={() => void ask(question)} disabled={busy}>{question}</button>
                ))}
              </div>
            )}
            <form className="composer" onSubmit={submit}>
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    submit(event as unknown as FormEvent);
                  }
                }}
                placeholder={`詢問 ${coCode} 的財報、法說會或公司關聯…`}
                rows={2}
                disabled={busy}
              />
              <button className="send-button" type="submit" disabled={busy || !input.trim()} aria-label="送出問題">↑</button>
            </form>
            <div className="composer-note">
              <span>答案只使用授權 Evidence</span>
              {latestResult && <span>Trace: {latestResult.trace_id.slice(0, 8)}</span>}
            </div>
          </div>
        </section>

        <aside className="source-panel">
          <div className="panel-title"><span>Source Inspector</span><small>可稽核來源</small></div>
          <SourcePanel
            preview={preview}
            citation={activeCitation}
            loading={previewLoading}
            error={previewError}
          />
        </aside>
      </main>
    </div>
  );
}

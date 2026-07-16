export type SourceType =
  | "financial_report"
  | "transcript"
  | "url"
  | "database"
  | "graph";

export interface SourceLocator {
  page?: number;
  paragraph_id?: string;
  timestamp?: string;
  table?: string;
  primary_key?: string;
  columns?: string[];
  graph_path?: string[];
}

export interface Citation {
  index: number;
  evidence_id: string;
  source_id: string;
  title: string;
  source_type: SourceType;
  locator: SourceLocator;
}

export interface ChatResult {
  answer: string;
  co_code: string;
  citations: Citation[];
  trace_id: string;
  routes: string[];
  verification: {
    passed?: boolean;
    [key: string]: unknown;
  };
  data_versions: string[];
}

export interface SourcePreview {
  source_id: string;
  co_code: string;
  source_type: SourceType;
  title: string;
  snapshot_html?: string;
  live_url?: string;
  text?: string;
  locator: SourceLocator;
  captured_at?: string;
  content_hash?: string;
  database_record?: Record<string, unknown>;
  graph?: Record<string, unknown>;
}

export type StreamEvent =
  | { type: "status"; data: { stage: string; message: string } }
  | { type: "token"; data: { text: string } }
  | { type: "result"; data: ChatResult };


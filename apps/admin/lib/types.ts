export type Approval = {
  id: string;
  conversation_id: string;
  status: "pending" | "approved" | "rejected" | "expired";
  payload: {
    tool?: string;
    arguments?: Record<string, unknown>;
    customer_id?: string;
    summary?: string;
  };
  decided_at: string | null;
  expires_at: string | null;
  created_at: string;
};

export type Document = {
  id: string;
  title: string;
  source_type: string;
  status: "pending" | "processing" | "ready" | "failed";
  error: string | null;
  size_bytes: number | null;
  language: string;
  effective_date: string | null;
  created_at: string;
};

export type Tool = {
  id: string;
  name: string;
  description: string;
  risk_level: "read" | "write" | "sensitive";
  http_method: string;
  url_template: string;
  requires_identity: boolean;
  policies: { type: string }[];
  created_at: string;
};

export type SecurityEvent = {
  id: string;
  created_at: string;
  conversation_id: string | null;
  layer: string;
  category: string;
  action: "blocked" | "flagged";
  detail: Record<string, unknown>;
};

export type Conversation = {
  id: string;
  customer_id: string;
  status: "open" | "escalated" | "closed";
  escalation_reason: string | null;
  created_at: string;
};

export type Message = {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  citations: { document_title: string; section: string; cite: string }[];
  created_at: string;
};

export type ConversationDetail = Conversation & { messages: Message[] };

export type Memory = { key: string; category: string; text: string; updated_at: string | null };

export type UsageDay = {
  day: string;
  messages: number;
  input_tokens: number;
  output_tokens: number;
  llm_calls: number;
  cost_usd: number;
  cached_messages: number;
  average_latency_ms: number | null;
};

export type UsageReport = { days: UsageDay[]; total_cost_usd: number; total_messages: number };

export type FirewallSettings = {
  firewall_enabled: boolean;
  injection_threshold: number;
  write_rate_limit: number;
  write_rate_window_minutes: number;
  tool_allowlist: string[] | null;
};

export type ApiKey = {
  id: string;
  name: string;
  prefix: string;
  kind: "publishable" | "secret";
  allowed_origins: string[];
  revoked_at: string | null;
  created_at: string;
};

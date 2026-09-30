export type Moment = { t: number; description: string };

export type Analysis = {
  summary: string;
  category: string;
  secondary_categories: string[];
  tags: string[];
  moments: Moment[];
  on_screen: string[];
  mood: string;
  energy: number;
  profanity: boolean;
  best_in: number;
  best_out: number;
  search_phrases: string[];
  confidence: number;
};

export type QA = {
  id: number;
  kind: "user" | "verify" | "crosscheck";
  question: string;
  answer: string;
  supported: number | null;
  provider: string;
  created_at: number;
};

export type QueueInfo = {
  paused: { reason: string; at: number; auto: boolean } | null;
  error_groups: { reason: string; count: number }[];
  eta_seconds: number | null;
};

export function fmtDuration(sec: number) {
  if (sec < 90) return "about a minute";
  const min = Math.round(sec / 60);
  if (min < 90) return `about ${min} minutes`;
  const h = Math.round(sec / 3600);
  if (h < 48) return `about ${h} hours`;
  return `about ${Math.round(sec / 86400)} days`;
}

export type Job = {
  id: number;
  kind: "sync" | "analyze" | "verify";
  clip_id: string | null;
  streamer_id: number | null;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  progress: number;
  message: string | null;
  clip_title?: string;
  thumbnail_url?: string;
  streamer_name?: string;
  updated_at: number;
};

export type Clip = {
  id: string;
  streamer_id: number;
  streamer_login: string;
  streamer_name: string;
  url: string;
  title: string;
  creator_name: string;
  game_name: string;
  view_count: number;
  created_at: string;
  duration: number;
  thumbnail_url: string;
  status: string;
  error: string | null;
  file_path: string | null;
  starred: number;
  category: string | null;
  summary: string | null;
  tags: string[] | null;
  mood: string | null;
  energy: number | null;
  confidence: number | null;
  needs_review: number;
  corrected: number;
  analysis: Analysis | null;
  provider: string | null;
  match?: { t: number; text: string } | null;
  // detail only
  transcript?: { start: number; end: number; text: string }[];
  frames?: { path: string; t: number }[];
  verification?: unknown;
  qa?: QA[];
  job?: Job | null;
  has_file?: boolean;
};

export type Streamer = {
  id: number;
  login: string;
  display_name: string;
  profile_image_url: string | null;
  synced_until: string | null;
  clip_count: number;
  analyzed_count: number | null;
};

export type Facets = {
  categories: { name: string; n: number }[];
  games: { name: string; n: number }[];
  moods: { name: string; n: number }[];
  counts: { total: number; analyzed: number; needs_review: number; starred: number };
};

export type Settings = Record<string, string | number | boolean>;

export type Status = {
  twitch: boolean;
  ffmpeg: boolean;
  whisper: boolean;
  embeddings: boolean;
  embeddings_state?: "ready" | "loading" | "off";
  whisper_device?: string | null;
  mode: "local" | "claude";
  local: { ok: boolean; message: string };
  claude: { ok: boolean; message: string };
};

export type OllamaModel = { name: string; size: number; vision: boolean; complete: boolean; missing_blobs: number };
export type OllamaStore = { path: string; root: string; size: number; models: OllamaModel[] };
export type PullState = { model: string | null; status: string; percent: number; error: string | null; done: boolean };
export type OllamaStatus = {
  stores: OllamaStore[];
  notes: string[];
  choice: { store: string | null; model: string; needs_download: boolean; reason: string };
  installed: boolean;
  running: boolean;
  running_models: string[];
  mismatch: boolean;
  env_models_dir: string;
  model: string;
  pull: PullState;
};
export type ResolveStatus = { resolve_installed: boolean; script_installed: boolean; script_current: boolean; script_path: string };
export type CheckResult = { ok: boolean; message: string };

export function fmtBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.round(n / 1e3)} KB`;
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    // The server refuses actions without this header, so other websites can't trigger them.
    headers: { "X-Clip-Manager": "1", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data?.detail) message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body: unknown = {}) => request<T>("POST", url, body),
  put: <T>(url: string, body: unknown) => request<T>("PUT", url, body),
  patch: <T>(url: string, body: unknown) => request<T>("PATCH", url, body),
  del: <T>(url: string) => request<T>("DELETE", url),
};

export function qs(params: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function fmtTime(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtViews(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
  return String(n);
}

export function fmtDate(iso: string): string {
  const d = new Date(iso);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: sameYear ? undefined : "numeric" });
}

export const CATEGORY_STYLE: Record<string, { emoji: string; color: string }> = {
  Funny: { emoji: "😂", color: "#ffd60a" },
  Fail: { emoji: "💀", color: "#ff453a" },
  "Clutch / Highlight": { emoji: "🔥", color: "#30d158" },
  Rage: { emoji: "😡", color: "#ff6961" },
  "Jumpscare / Scary": { emoji: "😱", color: "#bf5af2" },
  Wholesome: { emoji: "🥹", color: "#ff9fd6" },
  "Chat / Donation Reaction": { emoji: "💬", color: "#64d2ff" },
  IRL: { emoji: "📍", color: "#ffb340" },
  Music: { emoji: "🎵", color: "#a970ff" },
  "Collab / Guest": { emoji: "🤝", color: "#0a84ff" },
  "Drama / Serious": { emoji: "🎭", color: "#ac8e68" },
  Other: { emoji: "✨", color: "#8e8e93" },
};

export const IN_PROGRESS = new Set(["queued", "downloading", "transcribing", "analyzing"]);

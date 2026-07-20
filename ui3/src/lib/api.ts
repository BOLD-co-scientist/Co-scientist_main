import { streamEvents } from "./sse";
import type {
  AuthMe,
  Ev,
  CommitDetail,
  GitHistory,
  HitlDecision,
  HitlPending,
  HypSession,
  HypSummary,
  LibraryFile,
  LibraryHealth,
  LibraryUploadResponse,
  MemorySearchResult,
  RoleSummary,
  SendResult,
  SessionFile,
  SessionSummary,
} from "./types";

export interface StreamHandle {
  close: () => void;
}

/** The surface the UI talks to. Real and mock backends both implement it. */
export interface Api {
  authMe(): Promise<AuthMe>;
  listSessions(): Promise<SessionSummary[]>;
  getEvents(sid: string, limit?: number): Promise<Ev[]>;
  openStream(
    sid: string,
    sinceId: string | undefined,
    onEvent: (ev: Ev) => void,
    onError?: (e: unknown) => void,
  ): StreamHandle;
  createSession(task: string): Promise<{ session_id: string; task: string }>;
  sendMessage(sid: string, text: string): Promise<SendResult>;
  interject(sid: string, text: string): Promise<{ ok: boolean; queued: boolean }>;
  stop(sid: string): Promise<{ ok: boolean }>;
  listFiles(sid: string): Promise<SessionFile[]>;
  downloadFile(sid: string, path: string): Promise<void>;
  listLibrary(): Promise<LibraryFile[]>;
  uploadLibrary(
    file: File,
    onProgress?: (pct: number) => void,
    relpath?: string,
  ): Promise<LibraryUploadResponse>;
  deleteLibrary(name: string): Promise<{ ok: boolean }>;
  libraryHealth(): Promise<LibraryHealth>;
  getPending(sid: string): Promise<HitlPending[]>;
  answerHitl(
    sid: string,
    requestId: string,
    decision: HitlDecision,
    note?: string,
  ): Promise<{ ok: boolean }>;
  listRoles(): Promise<RoleSummary[]>;
  searchMemory(
    layer: string,
    q: string,
    k?: number,
    sid?: string,
  ): Promise<MemorySearchResult>;
  gitHistory(limit?: number): Promise<GitHistory>;
  getCommit(sha: string): Promise<CommitDetail>;
  spawnEvolution(command: string, base?: string): Promise<{ session_id: string; command: string }>;
  // ---- hypothesis engine (parallel-set flow) ----
  startHypothesis(goal: string, n?: number): Promise<HypSession>;
  refineHypothesis(hid: string, parentId: string, feedback?: string, n?: number): Promise<HypSession>;
  selectHypothesis(hid: string, hypId: string, note?: string): Promise<HypSession>;
  getHypothesis(hid: string): Promise<HypSession>;
  listHypothesisSessions(): Promise<HypSummary[]>;
}

export interface ApiConfig {
  getToken: () => string | null;
  onUnauthorized: () => void;
  base?: string;
}

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// ---- server → UI shape mappers ----
// The FastAPI server (api/server.py + api/schemas.py) uses slightly different
// field names than the design brief. Everything is reconciled here, in one
// place, so components and the mock backend keep the brief's vocabulary.

type Raw = Record<string, unknown>;

function mapSession(r: Raw): SessionSummary {
  const last_kind = (r.last_kind as string | undefined) ?? undefined;
  return {
    session_id: r.session_id as string,
    task: (r.task as string | null) ?? "(no task)",
    running: Boolean(r.running),
    // The server does not send `blocked`; a session whose latest event is an
    // unanswered HITL prompt is blocked. The store refines this for the active
    // session using the live pending list.
    blocked: last_kind === "hitl.pending",
    last_kind,
    updated: (r.last_ts as string | null) ?? undefined,
  };
}

// Human-readable verbs per HITL kind, so the card says what will happen in
// plain language instead of echoing the raw tool name.
const HITL_ACTIONS: Record<string, string> = {
  propose_merge: "Merge this evolution into your live system",
  propose_skill: "Save this workflow as a reusable skill",
  ask: "The agent needs your input to continue",
  library_write: "Write a file into your library",
};

// Turn a HITL payload into readable prose. scaffold/hitl.py writes
// {id, ts, kind, summary, payload, decision}; the payload shape varies by kind
// (evolution propose_merge: {branch, diff_preview, rationale, ...};
// skill: {name, description, body}; ask: {question}). Never dump raw JSON at
// the human — extract the fields that matter and format them.
function readableDetail(payload: unknown): string | undefined {
  if (payload == null) return undefined;
  if (typeof payload === "string") return payload;
  if (typeof payload !== "object") return String(payload);
  const p = payload as Record<string, unknown>;
  const parts: string[] = [];
  const str = (k: string) => (typeof p[k] === "string" ? (p[k] as string).trim() : "");

  if (str("rationale")) parts.push(str("rationale"));
  if (str("question")) parts.push(str("question"));
  if (str("name") && str("description")) parts.push(`Skill “${str("name")}” — ${str("description")}`);
  if (str("body")) parts.push(str("body"));
  if (str("branch")) parts.push(`Branch: ${str("branch")}`);

  // Parse changed-file names out of a unified diff preview → a tidy list.
  const diff = str("diff_preview");
  if (diff) {
    const files = Array.from(diff.matchAll(/^diff --git a\/(.+?) b\//gm)).map((m) => m[1]);
    if (files.length) {
      parts.push(`Files changed (${files.length}):\n` + files.map((f) => `  • ${f}`).join("\n"));
    }
  }

  if (parts.length) return parts.join("\n\n");
  // Unknown shape: key: value lines (skip noisy blobs) — still no JSON braces.
  const lines = Object.entries(p)
    .filter(([k]) => !["diff_preview", "body"].includes(k))
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  return lines.length ? lines.join("\n") : undefined;
}

function mapPending(r: Raw): HitlPending {
  const kind = (r.kind as string) ?? "";
  return {
    request_id: (r.id as string) ?? "",
    title: (r.summary as string) ?? HITL_ACTIONS[kind] ?? kind ?? "Approval required",
    action: HITL_ACTIONS[kind] ?? kind ?? undefined,
    detail: readableDetail(r.payload),
    requester: (r.requester as string) ?? undefined,
    created: (r.ts as string) ?? undefined,
  };
}

function mapHealth(r: Raw): LibraryHealth {
  return {
    used_bytes: (r.total_bytes as number) ?? 0,
    free_bytes: (r.disk_free_bytes as number) ?? 0,
  };
}

function mapMemory(layer: string, q: string, r: Raw): MemorySearchResult {
  const hits = ((r.hits as Raw[]) ?? []).map((h) => ({
    layer: (h.layer as string) ?? layer,
    score: typeof h.score === "number" ? h.score : 0,
    text: (h.text as string) ?? JSON.stringify(h),
  }));
  return { layer, query: q, hits };
}

function fmtEpoch(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function refKind(name: string): GitHistory["commits"][number]["refs"][number]["kind"] {
  const bare = name.replace(/^origin\//, "");
  if (bare === "main" || bare === "master") return "main";
  if (bare.startsWith("evo/")) return "evo";
  if (/^v\d/.test(bare)) return "tag";
  return "branch";
}

function mapGit(r: Raw): GitHistory {
  const head = (r.head as string | null) ?? "";
  const commits = ((r.commits as Raw[]) ?? []).map((c) => {
    const sha = c.sha as string;
    const refs = ((c.refs as string[]) ?? []).map((name) => ({ name, kind: refKind(name) }));
    if (head && sha === head) refs.unshift({ name: "HEAD", kind: "head" as const });
    return {
      // Keep the full sha so parent→child edge matching works; components
      // truncate for display. (Parents are full-length shas from git.)
      sha,
      parents: (c.parents as string[]) ?? [],
      author: (c.author as string) ?? "",
      ts: typeof c.ts === "number" ? fmtEpoch(c.ts) : String(c.ts ?? ""),
      refs,
      subject: (c.subject as string) ?? "",
    };
  });
  return { head, commits };
}

export function createApi(cfg: ApiConfig): Api {
  const base = cfg.base ?? import.meta.env.VITE_API_BASE ?? "";
  const url = (p: string) => `${base}${p}`;

  async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = cfg.getToken();
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body && !(init.body instanceof FormData))
      headers.set("Content-Type", "application/json");

    const res = await fetch(url(path), { ...init, headers });
    if (res.status === 401) {
      cfg.onUnauthorized(); // clear + bounce to login (§7.5)
      throw new HttpError(401, "Unauthorized");
    }
    if (!res.ok) throw new HttpError(res.status, `${res.status} ${res.statusText}`);
    if (res.status === 204) return undefined as T;
    const ct = res.headers.get("content-type") ?? "";
    return (ct.includes("application/json") ? await res.json() : await res.text()) as T;
  }

  return {
    authMe: () => req<AuthMe>("/auth/me"),

    listSessions: async () => (await req<Raw[]>("/sessions")).map(mapSession),

    getEvents: (sid, limit = 200) =>
      req<Ev[]>(`/sessions/${encodeURIComponent(sid)}/events?limit=${limit}`),

    openStream(sid, sinceId, onEvent, onError) {
      const token = cfg.getToken() ?? "";
      const close = streamEvents({
        url: url(`/events/${encodeURIComponent(sid)}`),
        token,
        sinceId,
        onEvent,
        onError,
        onUnauthorized: cfg.onUnauthorized,
      });
      return { close };
    },

    createSession: (task) =>
      req<{ session_id: string; task: string }>(`/research/sessions`, {
        method: "POST",
        body: JSON.stringify({ task }),
      }),

    async sendMessage(sid, text): Promise<SendResult> {
      const token = cfg.getToken();
      const headers = new Headers({ "Content-Type": "application/json" });
      if (token) headers.set("Authorization", `Bearer ${token}`);
      const res = await fetch(url(`/research/sessions/${encodeURIComponent(sid)}/messages`), {
        method: "POST",
        headers,
        body: JSON.stringify({ text }),
      });
      if (res.status === 401) {
        cfg.onUnauthorized();
        return { ok: false, status: 401, error: "Unauthorized" };
      }
      // 409 = busy / no resumable turn — surfaced inline, composer text kept (§7.6).
      if (res.status === 409)
        return { ok: false, status: 409, error: "Session busy or no resumable turn." };
      if (res.status === 404) return { ok: false, status: 404, error: "Session not found." };
      if (!res.ok) return { ok: false, status: res.status, error: `${res.status}` };
      const data = (await res.json().catch(() => ({}))) as { resumed?: boolean };
      return { ok: true, status: res.status, resumed: data.resumed };
    },

    interject: (sid, text) =>
      req<{ ok: boolean; queued: boolean }>(`/research/sessions/${encodeURIComponent(sid)}/interject`, {
        method: "POST",
        body: JSON.stringify({ text }),
      }),

    stop: (sid) => req<{ ok: boolean }>(`/sessions/${encodeURIComponent(sid)}/stop`, { method: "POST" }),

    listFiles: (sid) => req<SessionFile[]>(`/sessions/${encodeURIComponent(sid)}/files`),

    // Authenticated download → blob → object URL → programmatic click (§7.2).
    async downloadFile(sid, path) {
      const token = cfg.getToken();
      const headers = new Headers();
      if (token) headers.set("Authorization", `Bearer ${token}`);
      const res = await fetch(
        url(`/sessions/${encodeURIComponent(sid)}/files/download?path=${encodeURIComponent(path)}`),
        { headers },
      );
      if (res.status === 401) {
        cfg.onUnauthorized();
        throw new HttpError(401, "Unauthorized");
      }
      if (!res.ok) throw new HttpError(res.status, `download ${res.status}`);
      const blob = await res.blob();
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = path.split("/").pop() ?? "download";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(href);
    },

    listLibrary: () => req<LibraryFile[]>("/library/files"),

    // Multipart upload with progress via XHR (fetch has no upload progress) (§7.3).
    // `relpath` (the browser's webkitRelativePath) preserves folder structure
    // when uploading a directory; omitted for a plain single-file upload.
    uploadLibrary(file, onProgress, relpath) {
      return new Promise<LibraryUploadResponse>((resolve, reject) => {
        const token = cfg.getToken();
        const form = new FormData();
        form.append("file", file);
        if (relpath) form.append("relpath", relpath);
        const xhr = new XMLHttpRequest();
        xhr.open("POST", url("/library/files"));
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
        };
        xhr.onload = () => {
          if (xhr.status === 401) {
            cfg.onUnauthorized();
            reject(new HttpError(401, "Unauthorized"));
          } else if (xhr.status === 413) {
            reject(new HttpError(413, "File exceeds the library size limit."));
          } else if (xhr.status >= 200 && xhr.status < 300) {
            try {
              resolve(JSON.parse(xhr.responseText) as LibraryUploadResponse);
            } catch {
              resolve({ ok: true, name: file.name, size: file.size });
            }
          } else {
            reject(new HttpError(xhr.status, `upload ${xhr.status}`));
          }
        };
        xhr.onerror = () => reject(new HttpError(0, "network error"));
        xhr.send(form);
      });
    },

    deleteLibrary: (name) =>
      // Encode each path segment but keep the "/" separators so the
      // {name:path} route matches nested library paths.
      req<{ ok: boolean }>(
        `/library/files/${name.split("/").map(encodeURIComponent).join("/")}`,
        { method: "DELETE" },
      ),

    libraryHealth: async () => mapHealth(await req<Raw>("/library/health")),

    getPending: async (sid) =>
      (await req<Raw[]>(`/hitl/${encodeURIComponent(sid)}/pending`)).map(mapPending),

    answerHitl: (sid, requestId, decision, note) =>
      req<{ ok: boolean }>(`/hitl/${encodeURIComponent(sid)}/${encodeURIComponent(requestId)}/answer`, {
        method: "POST",
        body: JSON.stringify({ decision, note }),
      }),

    listRoles: () => req<RoleSummary[]>("/roles"),

    searchMemory: async (layer, q, k = 5, sid) =>
      mapMemory(
        layer,
        q,
        await req<Raw>(
          `/memory/${encodeURIComponent(layer)}?q=${encodeURIComponent(q)}&k=${k}` +
            (sid ? `&sid=${encodeURIComponent(sid)}` : ""),
        ),
      ),

    // Public endpoint — no auth (§5).
    gitHistory: async (limit = 200) => mapGit(await req<Raw>(`/git/history?limit=${limit}`)),

    getCommit: (sha) => req<CommitDetail>(`/git/commit/${encodeURIComponent(sha)}`),

    spawnEvolution: (command, base) =>
      req<{ session_id: string; command: string }>("/evolution/commands", {
        method: "POST",
        body: JSON.stringify({ command, base }),
      }),
    // ---- hypothesis engine ---- (generation can take 10-30s; no special timeout needed)
    startHypothesis: (goal, n) =>
      req<HypSession>("/hypothesis/sessions", {
        method: "POST",
        body: JSON.stringify({ goal, config: n ? { n_initial: n } : undefined }),
      }),
    refineHypothesis: (hid, parentId, feedback, n) =>
      req<HypSession>(`/hypothesis/${encodeURIComponent(hid)}/refine`, {
        method: "POST",
        body: JSON.stringify({ parent_id: parentId, feedback, n }),
      }),
    selectHypothesis: (hid, hypId, note) =>
      req<HypSession>(`/hypothesis/${encodeURIComponent(hid)}/select`, {
        method: "POST",
        body: JSON.stringify({ hypothesis_id: hypId, note }),
      }),
    getHypothesis: (hid) => req<HypSession>(`/hypothesis/${encodeURIComponent(hid)}`),
    listHypothesisSessions: () => req<HypSummary[]>("/hypothesis/sessions"),
  };
}

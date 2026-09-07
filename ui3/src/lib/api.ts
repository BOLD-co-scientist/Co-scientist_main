import { streamEvents } from "./sse";
import type {
  AuthMe,
  BriefFields,
  BriefPreview,
  BriefRecord,
  BriefSummary,
  Ev,
  CommitDetail,
  GitHistory,
  VersionList,
  HitlDecision,
  HitlPending,
  HypSession,
  HypSummary,
  ImportRecord,
  LibraryFile,
  LibraryHealth,
  LibraryUploadResponse,
  MemorySearchResult,
  ProjectNear,
  ProjectNodeView,
  ProjectTree,
  RecommendationsView,
  Reflection,
  RoleSummary,
  // R19
  DecideResult,
  EvoMode,
  EvoModeInfo,
  GoalLedger,
  GoalsPatch,
  JudgeInfo,
  JudgeVerdict,
  ProposalList,
  SendResult,
  SessionBrief,
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
  forkSession(sid: string): Promise<{ session_id: string; parent: string }>;
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
  // R16: the per-session evolution reflection + proposals (empty when none).
  getReflection(sid: string): Promise<Reflection>;
  reflect(sid: string): Promise<{ ok: boolean }>;
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
  spawnEvolution(command: string, base?: string, proposalId?: string, scope?: string): Promise<{ session_id: string; command: string }>;
  versions(): Promise<VersionList>;
  activateVersion(id: string): Promise<VersionList>;
  // ---- R19 guided evolution search: goal ledger, planner, judge, modes ----
  evoMode(): Promise<EvoModeInfo>;
  setEvoMode(mode: EvoMode): Promise<EvoModeInfo>;
  judgeInfo(): Promise<JudgeInfo>;
  getGoals(bid: string): Promise<GoalLedger>;
  deriveGoals(bid: string, hints?: string, keepSubgoals?: boolean): Promise<GoalLedger>;
  updateGoals(bid: string, patch: GoalsPatch): Promise<GoalLedger>;
  answerDrift(bid: string, changed: boolean, note?: string): Promise<GoalLedger>;
  plan(bid: string, trigger?: string): Promise<{ ok: boolean; status: string }>;
  listProposals(bid?: string): Promise<ProposalList>;
  decideProposal(pid: string, decision: "pick" | "both" | "decline", note?: string, withId?: string): Promise<DecideResult>;
  // ---- hypothesis engine (parallel-set flow) ----
  startHypothesis(goal: string, n?: number): Promise<HypSession>;
  refineHypothesis(hid: string, parentId: string, feedback?: string, n?: number): Promise<HypSession>;
  selectHypothesis(hid: string, hypId: string, note?: string): Promise<HypSession>;
  getHypothesis(hid: string): Promise<HypSession>;
  listHypothesisSessions(): Promise<HypSummary[]>;
  // ---- onboarding phase (O1): problem brief → data → hypotheses → launch ----
  createBrief(fields: Partial<BriefFields>): Promise<BriefRecord>;
  listBriefs(): Promise<BriefSummary[]>;
  getBrief(bid: string): Promise<BriefRecord>;
  updateBrief(bid: string, patch: Partial<BriefFields>): Promise<BriefRecord>;
  deleteBrief(bid: string): Promise<{ ok: boolean }>;
  previewBrief(bid: string): Promise<BriefPreview>;
  briefHypotheses(bid: string, n?: number): Promise<HypSession>;
  launchBrief(bid: string, autonomous?: boolean): Promise<{ session_id: string; task: string; brief_id: string; node_id?: string | null }>;
  getSessionBrief(sid: string): Promise<SessionBrief>;
  downloadLibrary(name: string): Promise<void>;
  // ---- project tree + advisor + imports (O2) ----
  registerBrief(bid: string, visibility?: "org" | "private"): Promise<{ node: ProjectNodeView; changed: boolean; advisor_started: boolean; brief: BriefRecord }>;
  projectTree(): Promise<ProjectTree>;
  getProject(pid: string): Promise<ProjectNodeView>;
  projectNear(pid: string): Promise<ProjectNear>;
  patchProject(pid: string, patch: { status?: string; visibility?: "org" | "private"; keywords?: string[] }): Promise<ProjectNodeView>;
  declareEdge(pid: string, dst: string, type: string, rationale: string): Promise<ProjectNodeView>;
  decideEdge(pid: string, eid: string, decision: "confirm" | "reject"): Promise<ProjectNodeView>;
  spawnBrief(pid: string): Promise<BriefRecord>;
  advise(pid: string): Promise<{ ok: boolean; running: boolean; started: boolean }>;
  getRecommendations(pid: string): Promise<RecommendationsView>;
  createImport(pid: string, body: { kind: "harness" | "tool"; owner: string; version_id: string; mode?: "adopt" | "merge"; tools?: string[]; roles?: string[]; include_skills?: string[] }): Promise<ImportRecord>;
  getImport(iid: string): Promise<ImportRecord>;
  listImports(): Promise<ImportRecord[]>;
  evolveImport(iid: string): Promise<{ ok: boolean; session_id: string; import: ImportRecord }>;
  shareVersion(id: string, shared: boolean): Promise<VersionList>;
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
    has_brief: Boolean(r.has_brief),
    brief_title: (r.brief_title as string | null) ?? null,
    hypothesis: (r.hypothesis as string | null) ?? null,
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
  // scaffold/hitl.py writes {id, ts, kind, summary, payload, decision}.
  const payload = r.payload;
  const kind = (r.kind as string) ?? undefined;
  const base = {
    request_id: (r.id as string) ?? "",
    title: (r.summary as string) ?? kind ?? "Approval required",
    requester: (r.requester as string) ?? undefined,
    created: (r.ts as string) ?? undefined,
    // R19: the judge's gate-2 review rides on the pending record when it has run.
    judge: (r.judge as JudgeVerdict | undefined) ?? undefined,
  };
  const p =
    payload && typeof payload === "object" && !Array.isArray(payload)
      ? (payload as Record<string, unknown>)
      : null;
  // First non-empty string field from the payload.
  const str = (k: string): string | undefined => {
    const v = p?.[k];
    return typeof v === "string" && v.trim() ? v : undefined;
  };

  // Every HITL kind's payload is structured; dumping it as JSON re-escapes the
  // prose (and diffs / skill files) the human actually needs to read, and
  // "What will happen" ends up showing a mechanical `kind`. So per kind, pull
  // the human-readable field, render it as markdown, and write a plain-language
  // action. Backends: research/runtime.py (checkpoint), scaffold/system_tools.py
  // (ask), evolution/tools/propose_merge.py (evolution_merge), scaffold/skills.py
  // (skill_proposal).
  switch (kind) {
    case "checkpoint": {
      const dt = str("deferred_tool");
      const deferred = dt ? cleanToolName(dt) : "";
      const count = typeof p?.event_count === "number" ? (p.event_count as number) : undefined;
      return {
        ...base,
        action: deferred
          ? `The agent paused before calling ${deferred}. Review the progress below, then Approve to let it continue or Reject to redirect.`
          : `The agent paused for your review. Approve to let it continue or Reject to redirect.`,
        detail: str("agent_summary"),
        detailMarkdown: true,
        meta: count != null ? `${count} events since last checkpoint` : undefined,
      };
    }
    case "ask":
      return {
        ...base,
        action: "The agent needs your input before it can continue.",
        detail: str("details"),
        detailMarkdown: true,
        requester: str("asker") ?? base.requester,
      };
    case "evolution_merge": {
      const branch = str("branch");
      const diff = str("diff_preview");
      const strict = p?.strict === true;
      // Rationale as prose; the diff inside a fenced block so the markdown
      // renderer shows it monospace with its own horizontal scroll.
      const detail = [str("rationale"), diff ? "```diff\n" + diff + "\n```" : ""]
        .filter(Boolean)
        .join("\n\n");
      return {
        ...base,
        action: branch
          ? `The evolution agent wants to merge ${branch} into main.`
          : "The evolution agent wants to merge its changes into main.",
        detail: detail || undefined,
        detailMarkdown: true,
        meta: [branch, strict ? "strict · smoke-gated" : null].filter(Boolean).join(" · ") || undefined,
      };
    }
    case "skill_proposal": {
      const name = str("name");
      const overwrite = p?.overwrite === true;
      const detail = [str("description"), str("skill_md")].filter(Boolean).join("\n\n");
      return {
        ...base,
        action: name
          ? `The agent wants to save a reusable skill${overwrite ? " (overwriting an existing one)" : ""}: ${name}.`
          : "The agent wants to save a reusable skill.",
        detail: detail || undefined,
        detailMarkdown: true,
        meta: overwrite ? "overwrites an existing skill" : undefined,
      };
    }
    case "harness_import":
    case "tool_import": {
      // O2: an import the API prepared (smoke already ran in a worktree). Show
      // what happens, the gate result, the risks and the bounded diff.
      const isHarness = kind === "harness_import";
      const smoke = p?.smoke && typeof p.smoke === "object" ? (p.smoke as { ran?: boolean; ok?: boolean | null }) : null;
      const risks = Array.isArray(p?.risks) ? (p!.risks as string[]) : [];
      const tools = Array.isArray(p?.tools) ? (p!.tools as string[]) : [];
      const roles = Array.isArray(p?.roles) ? (p!.roles as string[]) : [];
      const skills = Array.isArray(p?.donor_skills) ? (p!.donor_skills as string[]) : [];
      const diff = str("diff_preview");
      const parts: string[] = [];
      if (str("what_happens")) parts.push(str("what_happens")!);
      if (str("rationale")) parts.push(`**Why the advisor recommended it:** ${str("rationale")}`);
      if (str("from_node_title")) parts.push(`**Evolved for:** ${str("from_node_title")}`);
      if (!isHarness && tools.length) parts.push(`**Tools:** ${tools.map((t) => "`" + t + "`").join(", ")} → wired into ${roles.join(", ")}`);
      if (smoke) parts.push(`**Smoke + compat gate:** ${smoke.ran ? (smoke.ok ? "passed in a temporary worktree ✓" : "FAILED") : "not run (no tests in the donor tree)"}`);
      if (risks.length) parts.push("**Risks (computed):**\n" + risks.map((r) => `- ${r}`).join("\n"));
      if (skills.length) parts.push(`**Donor skills (not copied automatically):** ${skills.join(", ")}`);
      if (str("diffstat")) parts.push("```\n" + str("diffstat") + "\n```");
      if (diff) parts.push("```diff\n" + diff + "\n```");
      return {
        ...base,
        action: isHarness
          ? `Import ${str("owner_name") ?? "a colleague"}'s harness version and ${str("mode") === "merge" ? "merge it into your harness" : "switch to it"}. Approve to apply, Reject to keep your current harness.`
          : `Add ${tools.join(", ") || "the selected tools"} from ${str("owner_name") ?? "a colleague"}'s harness to yours. Approve to fast-forward, Reject to discard the staged change.`,
        detail: parts.join("\n\n") || undefined,
        detailMarkdown: true,
        requester: "project-tree advisor",
        meta: [str("version_id"), str("mode"), smoke ? (smoke.ok ? "smoke ✓" : "smoke ✗") : null].filter(Boolean).join(" · ") || undefined,
      };
    }
    case "longjob_submit": {
      // R12 long-job dispatch. Show the command as a code block (not escaped
      // JSON) and the resources/image on the meta line.
      const spec = p?.spec && typeof p.spec === "object" && !Array.isArray(p.spec)
        ? (p.spec as Record<string, unknown>)
        : {};
      const n = (k: string): number | undefined => (typeof spec[k] === "number" ? (spec[k] as number) : undefined);
      const backend = typeof spec.backend === "string" ? (spec.backend as string) : undefined;
      const image = typeof spec.image === "string" ? (spec.image as string) : undefined;
      const cmd = str("command") || (typeof spec.command === "string" ? (spec.command as string) : undefined);
      const resources = [
        backend ? `backend ${backend}` : null,
        n("cpu") != null ? `${n("cpu")} cpu` : null,
        n("gpu") ? `${n("gpu")} gpu` : null,
        n("walltime_min") != null ? `${n("walltime_min")}m walltime` : null,
        n("mem_mb") != null ? `${n("mem_mb")} MB` : null,
      ].filter(Boolean).join(" · ");
      return {
        ...base,
        action: backend
          ? `The agent wants to run a background job on the ${backend} backend. Review the command below, then Approve to dispatch or Reject to stop it.`
          : "The agent wants to run a background job. Review the command below, then Approve or Reject.",
        detail: cmd ? "```bash\n" + cmd + "\n```" : undefined,
        detailMarkdown: true,
        meta: [str("job_id"), image, resources].filter(Boolean).join(" · ") || undefined,
      };
    }
  }

  // Fallback: string payloads pass through; unknown object payloads still show
  // as JSON (a raw blob beats dropped data), but never surface a snake_case kind
  // as the action.
  let detail: string | undefined;
  if (typeof payload === "string") detail = payload;
  else if (payload != null) detail = JSON.stringify(payload, null, 2);
  return { ...base, action: kind ? kind.replace(/[._]/g, " ") : undefined, detail };
}

// SDK MCP tool names arrive as "mcp__<server>__<method>"; show just the server
// (the part a human cares about), mirroring eventVM.cleanTool.
function cleanToolName(name: string): string {
  const m = /^mcp__([^_].*?)__[^_].*$/.exec(name);
  return m ? m[1] : name;
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
    if (!res.ok) {
      // Carry the server's `detail` (a string, or a structured object such as
      // the launch validation {missing, data}) so callers can show the reason.
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
        else if (body.detail && typeof body.detail === "object") {
          const d = body.detail as Record<string, unknown>;
          const parts: string[] = [];
          if (Array.isArray(d.missing) && d.missing.length) parts.push(`missing: ${(d.missing as string[]).join(", ")}`);
          if (Array.isArray(d.data) && d.data.length) parts.push(`data: ${(d.data as string[]).join("; ")}`);
          if (typeof d.detail === "string") parts.unshift(d.detail);
          if (parts.length) detail = parts.join(" — ");
        }
      } catch {
        /* non-JSON error body */
      }
      throw new HttpError(res.status, detail);
    }
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

    forkSession: (sid) =>
      req<{ session_id: string; parent: string }>(`/sessions/${sid}/fork`, { method: "POST" }),

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

    getReflection: async (sid) => {
      const r = await req<Raw>(`/sessions/${encodeURIComponent(sid)}/reflection`);
      const proposals = Array.isArray(r.proposals) ? (r.proposals as Reflection["proposals"]) : [];
      return { session_id: sid, reflection: (r.reflection as string) ?? "", proposals };
    },
    reflect: (sid) =>
      req<{ ok: boolean }>(`/sessions/${encodeURIComponent(sid)}/reflect`, { method: "POST" }),

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

    versions: () => req<VersionList>("/versions"),
    activateVersion: (id) =>
      req<VersionList>(`/versions/${encodeURIComponent(id)}/activate`, { method: "POST" }),

    spawnEvolution: (command, base, proposalId, scope) =>
      req<{ session_id: string; command: string }>("/evolution/commands", {
        method: "POST",
        body: JSON.stringify({ command, base, proposal_id: proposalId, scope: scope ?? "harness" }),
      }),

    // ---- R19 guided evolution search ----
    evoMode: () => req<EvoModeInfo>("/evolution/mode"),
    setEvoMode: (mode) => req<EvoModeInfo>("/evolution/mode", { method: "PUT", body: JSON.stringify({ mode }) }),
    judgeInfo: () => req<JudgeInfo>("/evolution/judge"),
    getGoals: (bid) => req<GoalLedger>(`/evolution/goals/${encodeURIComponent(bid)}`),
    deriveGoals: (bid, hints, keepSubgoals) =>
      req<GoalLedger>(`/evolution/goals/${encodeURIComponent(bid)}/derive`, {
        method: "POST",
        body: JSON.stringify({ approach_hints: hints ?? undefined, keep_subgoals: keepSubgoals ?? undefined }),
      }),
    updateGoals: (bid, patch) =>
      req<GoalLedger>(`/evolution/goals/${encodeURIComponent(bid)}`, { method: "PUT", body: JSON.stringify(patch) }),
    answerDrift: (bid, changed, note) =>
      req<GoalLedger>(`/evolution/goals/${encodeURIComponent(bid)}/drift/answer`, {
        method: "POST",
        body: JSON.stringify({ changed, note: note ?? "" }),
      }),
    plan: (bid, trigger = "manual") =>
      req<{ ok: boolean; status: string }>("/evolution/plan", { method: "POST", body: JSON.stringify({ brief_id: bid, trigger }) }),
    listProposals: (bid) => req<ProposalList>(`/evolution/proposals${bid ? `?brief_id=${encodeURIComponent(bid)}` : ""}`),
    decideProposal: (pid, decision, note, withId) =>
      req<DecideResult>(`/evolution/proposals/${encodeURIComponent(pid)}/decide`, {
        method: "POST",
        body: JSON.stringify({ decision, note: note ?? undefined, with_id: withId ?? undefined }),
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

    // ---- onboarding phase (O1) ----
    createBrief: (fields) =>
      req<BriefRecord>("/onboarding/briefs", { method: "POST", body: JSON.stringify(fields) }),
    listBriefs: () => req<BriefSummary[]>("/onboarding/briefs"),
    getBrief: (bid) => req<BriefRecord>(`/onboarding/briefs/${encodeURIComponent(bid)}`),
    updateBrief: (bid, patch) =>
      req<BriefRecord>(`/onboarding/briefs/${encodeURIComponent(bid)}`, {
        method: "PUT",
        body: JSON.stringify(patch),
      }),
    deleteBrief: (bid) =>
      req<{ ok: boolean }>(`/onboarding/briefs/${encodeURIComponent(bid)}`, { method: "DELETE" }),
    previewBrief: (bid) => req<BriefPreview>(`/onboarding/briefs/${encodeURIComponent(bid)}/preview`),
    // Generation can take 10-30s (same as the Hypotheses page).
    briefHypotheses: (bid, n) =>
      req<HypSession>(`/onboarding/briefs/${encodeURIComponent(bid)}/hypotheses`, {
        method: "POST",
        body: JSON.stringify({ n }),
      }),
    launchBrief: (bid, autonomous = false) =>
      req<{ session_id: string; task: string; brief_id: string }>(
        `/onboarding/briefs/${encodeURIComponent(bid)}/launch`,
        { method: "POST", body: JSON.stringify({ autonomous }) },
      ),
    getSessionBrief: (sid) => req<SessionBrief>(`/sessions/${encodeURIComponent(sid)}/brief`),

    // O2: every library file is retrievable at any time (authenticated blob download).
    async downloadLibrary(name) {
      const token = cfg.getToken();
      const headers = new Headers();
      if (token) headers.set("Authorization", `Bearer ${token}`);
      const res = await fetch(url(`/library/files/download?name=${encodeURIComponent(name)}`), { headers });
      if (res.status === 401) {
        cfg.onUnauthorized();
        throw new HttpError(401, "Unauthorized");
      }
      if (!res.ok) throw new HttpError(res.status, `download ${res.status}`);
      const blob = await res.blob();
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = name.split("/").pop() ?? "download";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(href);
    },

    // ---- project tree + advisor + imports (O2) ----
    registerBrief: (bid, visibility) =>
      req(`/onboarding/briefs/${encodeURIComponent(bid)}/register`, { method: "POST", body: JSON.stringify(visibility ? { visibility } : {}) }),
    projectTree: () => req<ProjectTree>("/projects/tree"),
    getProject: (pid) => req<ProjectNodeView>(`/projects/${encodeURIComponent(pid)}`),
    projectNear: (pid) => req<ProjectNear>(`/projects/${encodeURIComponent(pid)}/near`),
    patchProject: (pid, patch) => req<ProjectNodeView>(`/projects/${encodeURIComponent(pid)}`, { method: "PATCH", body: JSON.stringify(patch) }),
    declareEdge: (pid, dst, type, rationale) =>
      req<ProjectNodeView>(`/projects/${encodeURIComponent(pid)}/edges`, { method: "POST", body: JSON.stringify({ dst, type, rationale }) }),
    decideEdge: (pid, eid, decision) =>
      req<ProjectNodeView>(`/projects/${encodeURIComponent(pid)}/edges/${encodeURIComponent(eid)}/${decision}`, { method: "POST" }),
    spawnBrief: (pid) => req<BriefRecord>(`/projects/${encodeURIComponent(pid)}/spawn-brief`, { method: "POST" }),
    advise: (pid) => req(`/projects/${encodeURIComponent(pid)}/advise`, { method: "POST" }),
    getRecommendations: (pid) => req<RecommendationsView>(`/projects/${encodeURIComponent(pid)}/recommendations`),
    createImport: (pid, body) => req<ImportRecord>(`/projects/${encodeURIComponent(pid)}/imports`, { method: "POST", body: JSON.stringify(body) }),
    getImport: (iid) => req<ImportRecord>(`/projects/imports/${encodeURIComponent(iid)}`),
    listImports: () => req<ImportRecord[]>("/projects/imports"),
    evolveImport: (iid) => req(`/projects/imports/${encodeURIComponent(iid)}/evolve`, { method: "POST" }),
    shareVersion: (id, shared) => req<VersionList>(`/versions/${encodeURIComponent(id)}/share`, { method: "POST", body: JSON.stringify({ shared }) }),
  };
}

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createApi, HttpError, type Api, type StreamHandle } from "../lib/api";
import { createMockApi } from "../lib/mock";
import { statusOf, type StatusView } from "../lib/format";
import type {
  AuthMe,
  Ev,
  GitHistory,
  HitlPending,
  HypCard,
  HypSession,
  LibraryFile,
  LibraryHealth,
  MemoryHit,
  Reflection,
  RoleSummary,
  SessionBrief,
  SessionFile,
  SessionSummary,
  VersionList,
} from "../lib/types";

const TOKEN_KEY = "csk_token";
const THEME_KEY = "cs_theme";
const HYP_KEY = "cs_active_hyp"; // persisted id of the open hypothesis session
const BRIEF_KEY = "cs_active_brief"; // persisted id of the onboarding draft being edited
const EVO_KEY = "cs_active_evo"; // persisted id of the open evolution session
const REFL_DISMISS_KEY = "cs_refl_dismissed"; // sids whose reflection nudge was closed
const isEvo = (sid: string) => sid.startsWith("evo-");
const USE_MOCK = import.meta.env.VITE_MOCK === "1";

type MainView = "session" | "hypothesis" | "evolution" | "onboarding" | "projects";
type RightTab = "hitl" | "files" | "context";
type Theme = "dark" | "light";

interface AppCtx {
  mock: boolean;
  api: Api;
  theme: Theme;
  toggleTheme: () => void;

  authed: boolean;
  user: AuthMe | null;
  loggingIn: boolean;
  authError: string | null;
  login: (key: string) => void;
  logout: () => void;

  sessions: SessionSummary[];
  activeId: string | null;
  active: SessionSummary | null;
  status: StatusView | null;
  select: (sid: string) => void;
  createSession: (task: string) => void;
  forkSession: (sid: string) => Promise<void>;
  refreshSessions: () => Promise<void>;
  draftNew: boolean;
  /** "New session" — opens the onboarding phase (O1): brief → data → hypotheses → launch. */
  startNewSession: () => void;
  /** Quick start: the free-form composer path (no brief). */
  startQuickSession: () => void;
  queue: string[];

  // ---- onboarding phase (O1) ----
  onboardingBriefId: string | null;
  setOnboardingBriefId: (bid: string | null) => void;
  launchBrief: (bid: string) => Promise<string | null>;
  /** The frozen brief of the active session (null for free-form sessions). */
  sessionBrief: SessionBrief | null;

  // ---- project tree (O2) ----
  /** Node to focus when the Projects page opens (null = overview). */
  projectFocus: string | null;
  openProject: (pid: string | null) => void;
  /** "Start a subproblem here": a new draft prefilled from a node → onboarding. */
  startBriefFromNode: (pid: string) => Promise<void>;
  downloadLibrary: (name: string) => void;

  events: Ev[];
  pending: HitlPending[];
  hasPending: boolean;

  mainView: MainView;
  setMainView: (v: MainView) => void;
  rightTab: RightTab;
  setRightTab: (t: RightTab) => void;

  sendNotice: string | null;
  clearNotice: () => void;
  send: (text: string) => Promise<boolean>;
  stop: () => Promise<void>;
  answer: (requestId: string, decision: "approve" | "reject", note?: string) => Promise<void>;

  // ---- evolution channel: independent session + stream, so it runs alongside
  // (and never disturbs) an active research session. Its log lives on the backend
  // under state/sessions/<evo-sid>; these are just the client-side mirror.
  evoActiveId: string | null;
  evoEvents: Ev[];
  evoPending: HitlPending[];
  evoRunning: boolean;
  evolutionCommand: string;
  setEvolutionCommand: (command: string) => void;
  startEvolution: (command: string, base?: string) => Promise<void>;
  answerEvo: (requestId: string, decision: "approve" | "reject", note?: string) => Promise<void>;

  // ---- R16: reflection for the active research session. `reflection` seeds the
  // Evolution-tab suggestion cards; `reflectionNudge` is the same but null once
  // dismissed / when there's nothing to suggest (drives the in-conversation card).
  reflection: Reflection | null;
  reflectionNudge: Reflection | null;
  dismissReflection: () => void;

  files: SessionFile[];
  loadFiles: () => void;
  downloadFile: (path: string) => void;
  library: LibraryFile[];
  libraryHealth: LibraryHealth | null;
  loadLibrary: () => void;
  /** Uploads to the library; resolves with the library paths that actually landed. */
  uploadFiles: (files: FileList | File[]) => Promise<string[]>;
  deleteLibrary: (name: string) => Promise<void>;

  roles: RoleSummary[];
  memory: MemoryHit[];
  loadContext: () => void;
  loadRoles: () => Promise<void>;
  searchMemory: (q: string) => Promise<void>;

  git: GitHistory | null;
  loadGit: () => void;

  // ---- R17 version DAG + switching ----
  versions: VersionList | null;
  loadVersions: () => void;
  activateVersion: (id: string) => Promise<VersionList>;

  // ---- hypothesis session (persists across page switches + reload) ----
  hyp: HypSession | null;
  setHyp: (h: HypSession | null) => void;
  workingHyp: HypCard | null; // the selected card in `hyp`, or null
  clearWorkingHyp: () => void;
}

const Ctx = createContext<AppCtx | null>(null);
export const useApp = (): AppCtx => {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp must be used within <AppProvider>");
  return v;
};

const NOTABLE = new Set([
  "hitl.pending",
  "hitl.answer",
  "session.idle",
  "session.end",
  "session.crashed",
  "session.interrupted",
  "research.complete",
  "turn.start",
]);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem(THEME_KEY) as Theme) || "dark",
  );
  const [authed, setAuthed] = useState(false);
  const [user, setUser] = useState<AuthMe | null>(null);
  const [loggingIn, setLoggingIn] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [events, setEvents] = useState<Ev[]>([]);
  const eventsRef = useRef<Ev[]>([]);
  eventsRef.current = events;
  const [pending, setPending] = useState<HitlPending[]>([]);
  const [draftNew, setDraftNew] = useState(false); // "New session" compose view, no backend session yet
  const [queue, setQueue] = useState<string[]>([]); // messages queued while the agent is busy

  const [reflection, setReflection] = useState<Reflection | null>(null);
  // Per-session nudge dismissals, persisted so a closed card stays closed.
  const [dismissed, setDismissed] = useState<Set<string>>(
    () => new Set(JSON.parse(localStorage.getItem(REFL_DISMISS_KEY) || "[]") as string[]),
  );

  const [evoActiveId, setEvoActiveId] = useState<string | null>(null);
  const [evoEvents, setEvoEvents] = useState<Ev[]>([]);
  const [evoPending, setEvoPending] = useState<HitlPending[]>([]);
  const [evolutionCommand, setEvolutionCommand] = useState("");
  const evoStreamRef = useRef<StreamHandle | null>(null);
  const evoActiveIdRef = useRef<string | null>(null);
  evoActiveIdRef.current = evoActiveId;
  const evoEventsRef = useRef<Ev[]>([]);
  evoEventsRef.current = evoEvents;

  const [mainView, setMainView] = useState<MainView>("session");
  const [rightTab, setRightTab] = useState<RightTab>("hitl");
  const [sendNotice, setSendNotice] = useState<string | null>(null);

  const [files, setFiles] = useState<SessionFile[]>([]);
  const [library, setLibrary] = useState<LibraryFile[]>([]);
  const [libraryHealth, setLibraryHealth] = useState<LibraryHealth | null>(null);
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [memory, setMemory] = useState<MemoryHit[]>([]);
  const [git, setGit] = useState<GitHistory | null>(null);
  const [versions, setVersions] = useState<VersionList | null>(null);
  const [hyp, setHypState] = useState<HypSession | null>(null);
  const [onboardingBriefId, setOnboardingBriefIdState] = useState<string | null>(
    () => localStorage.getItem(BRIEF_KEY),
  );
  const [sessionBrief, setSessionBrief] = useState<SessionBrief | null>(null);
  const [projectFocus, setProjectFocus] = useState<string | null>(null);

  const setOnboardingBriefId = useCallback((bid: string | null) => {
    setOnboardingBriefIdState(bid);
    if (bid) localStorage.setItem(BRIEF_KEY, bid);
    else localStorage.removeItem(BRIEF_KEY);
  }, []);

  // Lives in the store (not the view) so the open hypothesis session survives
  // switching away from the Hypotheses page; the id is persisted so it also
  // survives a reload.
  const setHyp = useCallback((h: HypSession | null) => {
    setHypState(h);
    if (h) localStorage.setItem(HYP_KEY, h.id);
    else localStorage.removeItem(HYP_KEY);
  }, []);
  const workingHyp = useMemo(
    () => (hyp?.selected_id ? hyp.rounds.flatMap((r) => r.hypotheses).find((c) => c.id === hyp.selected_id) ?? null : null),
    [hyp],
  );
  const clearWorkingHyp = useCallback(() => {
    setHypState((cur) => (cur ? { ...cur, selected_id: null } : cur));
  }, []);

  const tokenRef = useRef<string | null>(localStorage.getItem(TOKEN_KEY));
  const streamRef = useRef<StreamHandle | null>(null);
  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeId;
  const workingHypRef = useRef<HypCard | null>(null);
  workingHypRef.current = workingHyp;
  // A session launched from a brief already carries its working hypothesis in
  // the brief itself — never append the page-level one to its messages.
  const activeHasBriefRef = useRef(false);

  const logout = useCallback(() => {
    tokenRef.current = null;
    localStorage.removeItem(TOKEN_KEY);
    streamRef.current?.close();
    streamRef.current = null;
    setAuthed(false);
    setUser(null);
    setSessions([]);
    setEvents([]);
    setPending([]);
  }, []);

  const api: Api = useMemo(
    () =>
      USE_MOCK
        ? createMockApi()
        : createApi({
            getToken: () => tokenRef.current,
            onUnauthorized: () => logout(), // §7.5
          }),
    [logout],
  );

  // ---- theme ----
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);
  const toggleTheme = useCallback(() => setTheme((t) => (t === "dark" ? "light" : "dark")), []);

  // ---- session list ----
  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions());
    } catch {
      /* handled by onUnauthorized */
    }
  }, [api]);

  const refreshPending = useCallback(
    async (sid: string) => {
      try {
        setPending(await api.getPending(sid));
      } catch {
        setPending([]);
      }
    },
    [api],
  );

  // ---- auth ----
  const bootstrap = useCallback(async () => {
    try {
      const me = await api.authMe();
      setUser(me);
      setAuthed(true);
      setAuthError(null);
    } catch (e) {
      if (e instanceof HttpError && e.status === 401) logout();
    }
  }, [api, logout]);

  useEffect(() => {
    if (tokenRef.current || USE_MOCK) void bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = useCallback(
    async (key: string) => {
      const k = key.trim();
      if (!k) return;
      setLoggingIn(true);
      setAuthError(null);
      tokenRef.current = k;
      try {
        const me = await api.authMe();
        localStorage.setItem(TOKEN_KEY, k);
        setUser(me);
        setAuthed(true);
      } catch {
        tokenRef.current = null;
        setAuthError("Invalid API key.");
      } finally {
        setLoggingIn(false);
      }
    },
    [api],
  );

  // ---- load sessions once authed; pick an initial active session ----
  useEffect(() => {
    if (!authed) return;
    (async () => {
      try {
        const list = await api.listSessions();
        setSessions(list);
        const research = list.filter((s) => !isEvo(s.session_id));
        if (!activeIdRef.current && research.length) {
          const blocked = research.find((s) => s.blocked);
          setActiveId((blocked ?? research[0]).session_id);
        }
        // Restore/adopt an evolution session: persisted id if still present, else
        // the newest evo-* session on the server.
        if (!evoActiveIdRef.current) {
          const stored = localStorage.getItem(EVO_KEY);
          const evo = list.filter((s) => isEvo(s.session_id));
          const pick = evo.find((s) => s.session_id === stored) ?? evo[evo.length - 1];
          if (pick) setEvoActiveId(pick.session_id);
        }
        // First-time user: there is nothing to look at yet, so open the
        // onboarding phase directly instead of an empty timeline.
        if (!list.length) setMainView("onboarding");
      } catch {
        /* ignore */
      }
    })();
  }, [authed, api]);

  // ---- restore the persisted hypothesis session on auth ----
  useEffect(() => {
    if (!authed) return;
    const id = localStorage.getItem(HYP_KEY);
    if (!id) return;
    (async () => {
      try {
        setHypState(await api.getHypothesis(id));
      } catch {
        localStorage.removeItem(HYP_KEY); // gone on the server — drop the stale id
      }
    })();
  }, [authed, api]);

  // ---- open/close the event stream for the active session ----
  const openStreamFor = useCallback(
    (sid: string, sinceId?: string) => {
      streamRef.current?.close();
      streamRef.current = api.openStream(
        sid,
        sinceId,
        (e) => {
          if (activeIdRef.current !== sid) return;
          setEvents((prev) => (prev.some((p) => p.id === e.id) ? prev : [...prev, e]));
          if (NOTABLE.has(e.kind)) {
            void refreshSessions();
            void refreshPending(sid);
          }
          // R16: reflection finished for this session → pull the proposals so the
          // nudge card / Evolution-tab suggestions can show them.
          if (e.kind === "reflection.ready") {
            void api.getReflection(sid).then((r) => {
              if (activeIdRef.current === sid) setReflection(r);
            });
          }
        },
        () => {
          /* stream error — the timeline keeps its backfilled history */
        },
      );
    },
    [api, refreshSessions, refreshPending],
  );

  // ---- when the active session changes: backfill, then stream ----
  useEffect(() => {
    if (!authed || !activeId) return;
    let cancelled = false;
    setReflection(null); // clear stale suggestions until this session's load
    (async () => {
      try {
        const [backfill] = await Promise.all([
          api.getEvents(activeId, 200),
          refreshPending(activeId),
        ]);
        if (cancelled) return;
        setEvents(backfill);
        const lastId = backfill.length ? backfill[backfill.length - 1].id : undefined;
        openStreamFor(activeId, lastId);
        // R16: load any existing reflection for this (research) session.
        if (!isEvo(activeId)) {
          void api.getReflection(activeId).then((r) => {
            if (!cancelled && activeIdRef.current === activeId) setReflection(r);
          });
        }
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
      streamRef.current?.close();
      streamRef.current = null;
    };
  }, [authed, activeId, api, openStreamFor, refreshPending]);

  useEffect(() => () => streamRef.current?.close(), []);

  // ---- evolution channel: its own stream, fully independent of the research
  // stream above (switching tabs never touches either). ----
  const refreshEvoPending = useCallback(
    async (sid: string) => {
      try {
        setEvoPending(await api.getPending(sid));
      } catch {
        setEvoPending([]);
      }
    },
    [api],
  );

  const openEvoStreamFor = useCallback(
    (sid: string, sinceId?: string) => {
      evoStreamRef.current?.close();
      evoStreamRef.current = api.openStream(
        sid,
        sinceId,
        (e) => {
          if (evoActiveIdRef.current !== sid) return;
          setEvoEvents((prev) => (prev.some((p) => p.id === e.id) ? prev : [...prev, e]));
          if (NOTABLE.has(e.kind) || e.kind.startsWith("evolution") || e.kind.startsWith("skill")) {
            void refreshSessions();
            void refreshEvoPending(sid);
          }
        },
        () => {},
      );
    },
    [api, refreshSessions, refreshEvoPending],
  );

  useEffect(() => {
    if (!authed || !evoActiveId) return;
    let cancelled = false;
    (async () => {
      try {
        const [backfill] = await Promise.all([
          api.getEvents(evoActiveId, 200),
          refreshEvoPending(evoActiveId),
        ]);
        if (cancelled) return;
        setEvoEvents(backfill);
        const lastId = backfill.length ? backfill[backfill.length - 1].id : undefined;
        openEvoStreamFor(evoActiveId, lastId);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
      evoStreamRef.current?.close();
      evoStreamRef.current = null;
    };
  }, [authed, evoActiveId, api, openEvoStreamFor, refreshEvoPending]);

  useEffect(() => () => evoStreamRef.current?.close(), []);

  // ---- derived ----
  const active = useMemo(() => {
    const a = sessions.find((s) => s.session_id === activeId) ?? null;
    if (!a) return null;
    // The live pending list is fresher than the session-list snapshot: any
    // unanswered HITL request means the agent is blocked on the human.
    return pending.length > 0 ? { ...a, blocked: true } : a;
  }, [sessions, activeId, pending]);
  const status = active ? statusOf(active) : null;
  const hasPending = pending.length > 0;
  activeHasBriefRef.current = Boolean(active?.has_brief);

  // ---- the active session's frozen brief (O1) ----
  useEffect(() => {
    if (!authed || !activeId || !active?.has_brief) {
      setSessionBrief(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const b = await api.getSessionBrief(activeId);
        if (!cancelled) setSessionBrief(b);
      } catch {
        if (!cancelled) setSessionBrief(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authed, activeId, active?.has_brief, api]);
  // The research rail must not show evo-* sessions (they live in the Evolution tab).
  const researchSessions = useMemo(() => sessions.filter((s) => !isEvo(s.session_id)), [sessions]);
  const evoRunning = useMemo(() => {
    const s = evoActiveId ? sessions.find((x) => x.session_id === evoActiveId) : null;
    return !!s?.running;
  }, [sessions, evoActiveId]);

  // ---- actions ----
  const select = useCallback((sid: string) => {
    setMainView("session");
    // Selecting a real session exits draft mode — otherwise send() would still
    // take the draft branch and spawn a *new* session instead of replying here.
    setDraftNew(false);
    // Only clear the timeline when actually switching sessions. Re-selecting the
    // active session must not wipe events, since the backfill effect is keyed on
    // activeId changing and would not re-run to repopulate them.
    setActiveId((cur) => {
      if (cur !== sid) setEvents([]);
      return sid;
    });
  }, []);

  // Prepend the working hypothesis so the agent stays anchored to it. `full`
  // frames a whole new session; the compact form reminds an ongoing one.
  const withHyp = useCallback(
    (text: string, full: boolean): string => {
      const w = workingHypRef.current;
      if (!w) return text;
      // Follow-ups in a brief-launched session: the brief already anchors the
      // agent on its own working hypothesis; don't append the page-level one.
      if (!full && activeHasBriefRef.current) return text;
      // Append the hypothesis AFTER the user's text so the session title (which
      // is the task's first line) stays clean; the agent still gets the context.
      const ctx = full
        ? `[Working hypothesis to steer this research toward testing/developing: "${w.statement}"${w.rationale ? ` (${w.rationale})` : ""}]`
        : `[Keep focusing on the working hypothesis: "${w.statement}"]`;
      return `${text}\n\n${ctx}`;
    },
    [],
  );

  const createSession = useCallback(
    async (task: string) => {
      try {
        const { session_id } = await api.createSession(withHyp(task, true));
        setDraftNew(false);
        await refreshSessions();
        select(session_id);
      } catch {
        /* ignore */
      }
    },
    [api, refreshSessions, select, withHyp],
  );

  // R17: fork a read-only session (created by a newer version) into a fresh one
  // continuable on the active version. The copy drops the schema stamp so the
  // next turn re-stamps it; the original is left untouched.
  const forkSession = useCallback(
    async (sid: string) => {
      try {
        const { session_id } = await api.forkSession(sid);
        await refreshSessions();
        select(session_id);
      } catch {
        /* ignore */
      }
    },
    [api, refreshSessions, select],
  );

  // Quick start: an empty compose view (no backend session yet); the first
  // message the user sends creates the real session from a free-form task.
  const startQuickSession = useCallback(() => {
    setDraftNew(true);
    setActiveId(null);
    setEvents([]);
    setQueue([]);
    setMainView("session");
  }, []);

  // "New session" = the onboarding phase (O1). The wizard owns the draft; the
  // store just routes there and remembers which draft is open.
  const startNewSession = useCallback(() => {
    setDraftNew(false);
    setMainView("onboarding");
  }, []);

  // O2: the Projects page (org-wide tree), optionally focused on one node.
  const openProject = useCallback((pid: string | null) => {
    setProjectFocus(pid);
    setMainView("projects");
  }, []);

  // O2: "Start a subproblem here" → the server prefills a draft from the node
  // (domain, keywords, prior work, parent link) and the wizard opens on it.
  const startBriefFromNode = useCallback(
    async (pid: string) => {
      try {
        const rec = await api.spawnBrief(pid);
        setOnboardingBriefId(rec.id);
        setDraftNew(false);
        setMainView("onboarding");
      } catch (e) {
        setSendNotice(e instanceof HttpError ? `Could not start from that problem: ${e.message}` : "Could not start from that problem.");
      }
    },
    [api, setOnboardingBriefId],
  );

  const downloadLibrary = useCallback(
    (name: string) => {
      void api.downloadLibrary(name).catch(() => setSendNotice("Download failed."));
    },
    [api],
  );

  // Launch a brief → the server creates the session and starts its first turn
  // with the whole brief; we then open that session like any other.
  const launchBrief = useCallback(
    async (bid: string): Promise<string | null> => {
      try {
        const { session_id } = await api.launchBrief(bid);
        setOnboardingBriefId(null);
        await refreshSessions();
        select(session_id);
        return session_id;
      } catch (e) {
        setSendNotice(e instanceof HttpError ? `Launch failed: ${e.message}` : "Launch failed.");
        return null;
      }
    },
    [api, refreshSessions, select, setOnboardingBriefId],
  );

  const send = useCallback(
    async (text: string): Promise<boolean> => {
      if (!text.trim()) return false;
      // Draft: the first message creates the session.
      if (draftNew || !activeId) {
        if (!activeId && !draftNew) return false;
        await createSession(text);
        return true;
      }
      const sid = activeId;
      const msg = withHyp(text, false);
      // Blocked = agent mid-run waiting on HITL → interject; else resume the turn.
      if (active?.blocked) {
        try {
          await api.interject(sid, msg);
          return true;
        } catch {
          setSendNotice("Could not reach the agent.");
          return false;
        }
      }
      // Busy (a turn is running) → queue it; the queue-flush effect sends it
      // once the session goes idle, so the user can line up prompts.
      if (active?.running) {
        setQueue((q) => [...q, text]);
        return true;
      }
      const r = await api.sendMessage(sid, msg);
      if (!r.ok) {
        if (r.status === 409) {
          // No resumable turn yet, or a race with "running" — queue and retry on
          // idle. This is an accepted enqueue, not a rejection: return true so the
          // composer clears the box (else the text lingers and re-enqueues on the
          // next Enter, duplicating the prompt).
          setQueue((q) => [...q, text]);
          return true;
        }
        if (r.status !== 401) setSendNotice(r.error ?? "Message failed.");
        return false;
      }
      void refreshSessions();
      return true;
    },
    [activeId, active, api, refreshSessions, withHyp, draftNew, createSession],
  );

  // Flush queued prompts one at a time when the active session goes idle. The
  // flushing guard prevents a status-refresh race from sending two at once.
  const queueRef = useRef<string[]>([]);
  queueRef.current = queue;
  const flushingRef = useRef(false);
  useEffect(() => {
    if (!activeId || !active || active.running || active.blocked) return;
    if (flushingRef.current || queueRef.current.length === 0) return;
    flushingRef.current = true;
    const next = queueRef.current[0];
    void (async () => {
      try {
        const r = await api.sendMessage(activeId, withHyp(next, false));
        if (r.ok) {
          // Remove only after a confirmed send. On failure we leave the item at
          // the front so it isn't silently lost; the effect retries it the next
          // time the session is observed idle. (Guard against the queue having
          // changed underneath us — only drop the head if it's still `next`.)
          setQueue((q) => (q[0] === next ? q.slice(1) : q));
          await refreshSessions();
        }
      } finally {
        flushingRef.current = false;
      }
    })();
  }, [activeId, active, active?.running, active?.blocked, api, refreshSessions, withHyp]);

  const stop = useCallback(async () => {
    if (!activeId) return;
    try {
      await api.stop(activeId);
      await refreshSessions();
    } catch {
      /* ignore */
    }
  }, [activeId, api, refreshSessions]);

  const answer = useCallback(
    async (requestId: string, decision: "approve" | "reject", note?: string) => {
      if (!activeId) return;
      const sid = activeId;
      try {
        await api.answerHitl(sid, requestId, decision, note);
        await refreshSessions();
        await refreshPending(sid);
        // Reconnect so the resumed run's new events stream in. Read the last id
        // from a ref (not inside a setState updater — updaters must be pure and
        // run twice under StrictMode, which would double-open the stream).
        const cur = eventsRef.current;
        const lastId = cur.length ? cur[cur.length - 1].id : undefined;
        openStreamFor(sid, lastId);
      } catch {
        /* ignore */
      }
    },
    [activeId, api, refreshSessions, refreshPending, openStreamFor],
  );

  // ---- evolution actions ----
  const startEvolution = useCallback(
    // `base` (R17): branch the evolution from a specific node instead of the
    // active tip. Either way the new evo-* session is adopted as the drawer's
    // channel — it is NEVER routed into the research session rail.
    async (command: string, base?: string) => {
      const cmd = command.trim();
      if (!cmd) return;
      try {
        const { session_id } = await api.spawnEvolution(cmd, base);
        localStorage.setItem(EVO_KEY, session_id);
        setEvoEvents([]);
        setEvoPending([]);
        setEvoActiveId(session_id);
        await refreshSessions();
      } catch {
        /* ignore */
      }
    },
    [api, refreshSessions],
  );

  const answerEvo = useCallback(
    async (requestId: string, decision: "approve" | "reject", note?: string) => {
      if (!evoActiveId) return;
      const sid = evoActiveId;
      try {
        await api.answerHitl(sid, requestId, decision, note);
        await refreshEvoPending(sid);
        const cur = evoEventsRef.current;
        const lastId = cur.length ? cur[cur.length - 1].id : undefined;
        openEvoStreamFor(sid, lastId);
      } catch {
        /* ignore */
      }
    },
    [evoActiveId, api, refreshEvoPending, openEvoStreamFor],
  );

  // ---- files / library ----
  const loadFiles = useCallback(async () => {
    if (!activeId) return;
    try {
      setFiles(await api.listFiles(activeId));
    } catch {
      setFiles([]);
    }
  }, [activeId, api]);

  const downloadFile = useCallback(
    (path: string) => {
      if (!activeId) return;
      void api.downloadFile(activeId, path).catch(() => setSendNotice("Download failed."));
    },
    [activeId, api],
  );

  const loadLibrary = useCallback(async () => {
    try {
      const [f, h] = await Promise.all([api.listLibrary(), api.libraryHealth()]);
      setLibrary(f);
      setLibraryHealth(h);
    } catch {
      /* ignore */
    }
  }, [api]);

  const uploadFiles = useCallback(
    async (fl: FileList | File[]): Promise<string[]> => {
      const arr = Array.from(fl);
      const landed: string[] = [];
      let failed = 0;
      let skipped = 0;
      for (const f of arr) {
        // A directory picker sets webkitRelativePath (e.g. "panel/ic50.csv");
        // pass it so the server preserves the folder structure. A plain file
        // picker leaves it empty → flat upload at the library root.
        const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath;
        const relpath = rel && rel.trim() ? rel : undefined;
        // The server rejects dotfiles (.DS_Store, .git/…) per segment; a macOS
        // folder upload always contains some. Skip them silently instead of
        // reporting the whole folder as failed.
        if ((relpath ?? f.name).split("/").some((seg) => seg.startsWith("."))) {
          skipped += 1;
          continue;
        }
        try {
          const r = await api.uploadLibrary(f, undefined, relpath);
          landed.push(r.name ?? relpath ?? f.name);
        } catch (e) {
          failed += 1;
          setSendNotice(e instanceof HttpError && e.status === 413 ? `File too large for the library: ${f.name}` : `Upload failed: ${f.name}`);
        }
      }
      if (!failed && skipped && !landed.length) setSendNotice("Only hidden files were selected; nothing uploaded.");
      await loadLibrary();
      return landed;
    },
    [api, loadLibrary],
  );

  const deleteLibrary = useCallback(
    async (name: string) => {
      try {
        await api.deleteLibrary(name);
        await loadLibrary();
      } catch {
        /* ignore */
      }
    },
    [api, loadLibrary],
  );

  // ---- context (roles + memory) ----
  // Refetch the agent roles only — so the Context panel can keep them live
  // (a merged evolution that adds an agent shows up on the next poll).
  const loadRoles = useCallback(async () => {
    try {
      setRoles(await api.listRoles());
    } catch {
      /* ignore */
    }
  }, [api]);

  const loadContext = useCallback(async () => {
    try {
      const [r, m] = await Promise.all([api.listRoles(), api.searchMemory("project", "", 5, activeId ?? undefined)]);
      setRoles(r);
      setMemory(m.hits);
    } catch {
      /* ignore */
    }
  }, [api, activeId]);

  const searchMemory = useCallback(
    async (q: string) => {
      try {
        const m = await api.searchMemory("project", q, 5, activeId ?? undefined);
        setMemory(m.hits);
      } catch {
        /* ignore */
      }
    },
    [api, activeId],
  );

  // Public endpoint (§5) — the system repo history for the Evolution activity log.
  const loadGit = useCallback(async () => {
    try {
      setGit(await api.gitHistory(200));
    } catch {
      /* ignore */
    }
  }, [api]);

  const loadVersions = useCallback(async () => {
    try {
      setVersions(await api.versions());
    } catch {
      /* ignore */
    }
  }, [api]);

  // Switch the active version, then refresh the DAG + versions so the graph and
  // the active marker update. Throws on 409 (busy / switch in progress) so the
  // caller can surface it.
  const activateVersion = useCallback(
    async (id: string) => {
      const vl = await api.activateVersion(id);
      setVersions(vl);
      void loadGit();
      return vl;
    },
    [api, loadGit],
  );

  // when a session becomes blocked, pull focus to the approvals tab
  useEffect(() => {
    if (active?.blocked) setRightTab("hitl");
  }, [active?.blocked, activeId]);

  const reflectionNudge = useMemo(
    () =>
      reflection && reflection.proposals.length > 0 && !dismissed.has(reflection.session_id)
        ? reflection
        : null,
    [reflection, dismissed],
  );
  const dismissReflection = useCallback(() => {
    const sid = reflection?.session_id;
    if (!sid) return;
    setDismissed((prev) => {
      const next = new Set(prev).add(sid);
      localStorage.setItem(REFL_DISMISS_KEY, JSON.stringify([...next]));
      return next;
    });
  }, [reflection]);

  const value: AppCtx = {
    mock: USE_MOCK,
    api,
    theme,
    toggleTheme,
    authed,
    user,
    loggingIn,
    authError,
    login,
    logout,
    sessions: researchSessions,
    activeId,
    active,
    status,
    select,
    createSession,
    forkSession,
    refreshSessions,
    draftNew,
    startNewSession,
    startQuickSession,
    queue,
    onboardingBriefId,
    setOnboardingBriefId,
    launchBrief,
    sessionBrief,
    projectFocus,
    openProject,
    startBriefFromNode,
    downloadLibrary,
    events,
    pending,
    hasPending,
    mainView,
    setMainView,
    rightTab,
    setRightTab,
    sendNotice,
    clearNotice: () => setSendNotice(null),
    send,
    stop,
    answer,
    evoActiveId,
    evoEvents,
    evoPending,
    evoRunning,
    evolutionCommand,
    setEvolutionCommand,
    startEvolution,
    answerEvo,
    reflection,
    reflectionNudge,
    dismissReflection,
    files,
    loadFiles,
    downloadFile,
    library,
    libraryHealth,
    loadLibrary,
    uploadFiles,
    deleteLibrary,
    roles,
    memory,
    loadContext,
    loadRoles,
    searchMemory,
    git,
    loadGit,
    versions,
    loadVersions,
    activateVersion,
    hyp,
    setHyp,
    workingHyp,
    clearWorkingHyp,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

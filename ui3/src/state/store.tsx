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
  LibraryFile,
  LibraryHealth,
  MemoryHit,
  RoleSummary,
  SessionFile,
  SessionSummary,
} from "../lib/types";

const TOKEN_KEY = "csk_token";
const THEME_KEY = "cs_theme";
const USE_MOCK = import.meta.env.VITE_MOCK === "1";

type MainView = "session" | "hypothesis" | "evolution";
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
  refreshSessions: () => Promise<void>;

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

  files: SessionFile[];
  loadFiles: () => void;
  downloadFile: (path: string) => void;
  library: LibraryFile[];
  libraryHealth: LibraryHealth | null;
  loadLibrary: () => void;
  uploadFiles: (files: FileList | File[]) => Promise<void>;
  deleteLibrary: (name: string) => Promise<void>;

  roles: RoleSummary[];
  memory: MemoryHit[];
  loadContext: () => void;
  searchMemory: (q: string) => Promise<void>;

  git: GitHistory | null;
  loadGit: () => void;
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
  const [pending, setPending] = useState<HitlPending[]>([]);

  const [mainView, setMainView] = useState<MainView>("session");
  const [rightTab, setRightTab] = useState<RightTab>("hitl");
  const [sendNotice, setSendNotice] = useState<string | null>(null);

  const [files, setFiles] = useState<SessionFile[]>([]);
  const [library, setLibrary] = useState<LibraryFile[]>([]);
  const [libraryHealth, setLibraryHealth] = useState<LibraryHealth | null>(null);
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [memory, setMemory] = useState<MemoryHit[]>([]);
  const [git, setGit] = useState<GitHistory | null>(null);

  const tokenRef = useRef<string | null>(localStorage.getItem(TOKEN_KEY));
  const streamRef = useRef<StreamHandle | null>(null);
  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeId;

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
        if (!activeIdRef.current && list.length) {
          const blocked = list.find((s) => s.blocked);
          setActiveId((blocked ?? list[0]).session_id);
        }
      } catch {
        /* ignore */
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

  // ---- actions ----
  const select = useCallback((sid: string) => {
    setMainView("session");
    // Only clear the timeline when actually switching sessions. Re-selecting the
    // active session must not wipe events, since the backfill effect is keyed on
    // activeId changing and would not re-run to repopulate them.
    setActiveId((cur) => {
      if (cur !== sid) setEvents([]);
      return sid;
    });
  }, []);

  const createSession = useCallback(
    async (task: string) => {
      try {
        const { session_id } = await api.createSession(task);
        await refreshSessions();
        select(session_id);
      } catch {
        /* ignore */
      }
    },
    [api, refreshSessions, select],
  );

  const send = useCallback(
    async (text: string): Promise<boolean> => {
      if (!activeId || !text.trim()) return false;
      const sid = activeId;
      // Blocked = agent mid-run waiting on HITL → interject; else resume the turn.
      if (active?.blocked) {
        try {
          await api.interject(sid, text);
          return true;
        } catch {
          setSendNotice("Could not reach the agent.");
          return false;
        }
      }
      const r = await api.sendMessage(sid, text);
      if (!r.ok) {
        if (r.status === 409) setSendNotice("Session is busy or has no resumable turn — your text was kept.");
        else if (r.status !== 401) setSendNotice(r.error ?? "Message failed.");
        return false;
      }
      void refreshSessions();
      return true;
    },
    [activeId, active, api, refreshSessions],
  );

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
        // Reconnect so the resumed run's new events stream in.
        setEvents((cur) => {
          const lastId = cur.length ? cur[cur.length - 1].id : undefined;
          openStreamFor(sid, lastId);
          return cur;
        });
      } catch {
        /* ignore */
      }
    },
    [activeId, api, refreshSessions, refreshPending, openStreamFor],
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
    async (fl: FileList | File[]) => {
      const arr = Array.from(fl);
      for (const f of arr) {
        try {
          // A directory picker sets webkitRelativePath (e.g. "panel/ic50.csv");
          // pass it so the server preserves the folder structure. A plain file
          // picker leaves it empty → flat upload at the library root.
          const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath;
          await api.uploadLibrary(f, undefined, rel && rel.trim() ? rel : undefined);
        } catch (e) {
          setSendNotice(e instanceof HttpError && e.status === 413 ? "File too large for the library." : "Upload failed.");
        }
      }
      await loadLibrary();
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

  // when a session becomes blocked, pull focus to the approvals tab
  useEffect(() => {
    if (active?.blocked) setRightTab("hitl");
  }, [active?.blocked, activeId]);

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
    sessions,
    activeId,
    active,
    status,
    select,
    createSession,
    refreshSessions,
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
    searchMemory,
    git,
    loadGit,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

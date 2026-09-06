import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../state/store";
import { HttpError } from "../lib/api";
import { fmtSize } from "../lib/format";
import HypothesisCards from "./HypothesisCards";
import type { BriefDataItem, BriefFields, BriefPreview, BriefRecord, BriefSummary, HypSession } from "../lib/types";

// Onboarding phase (O1). Every session starts here: the human defines the
// problem in a FIXED format, attaches the data, runs the hypothesis search,
// reviews the exact brief the supervisor will receive, and launches.
//
// The brief is server-authoritative (api/onboarding.py). This view edits a
// draft (autosaved), never composes the agent's message itself: the Review
// step shows the server's rendering, and Launch hands the id to the backend.
// Plan + contract: docs/plans/O1-onboarding.md.

const STEPS = ["Define the problem", "Data", "Hypothesis search", "Review & launch"] as const;

const EMPTY: BriefFields = {
  title: "",
  domain: "",
  background: "",
  research_question: "",
  objectives: [],
  data: [],
  data_notes: "",
  constraints: "",
  success_criteria: "",
  deliverables: [],
};

const fieldsOf = (b: BriefRecord): BriefFields => ({
  title: b.title ?? "",
  domain: b.domain ?? "",
  background: b.background ?? "",
  research_question: b.research_question ?? "",
  objectives: b.objectives ?? [],
  data: b.data ?? [],
  data_notes: b.data_notes ?? "",
  constraints: b.constraints ?? "",
  success_criteria: b.success_criteria ?? "",
  deliverables: b.deliverables ?? [],
});

// ---- small styled primitives (inline styles keep parity with the rest of ui3) ----
const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "10px 12px",
  background: "var(--bg0)",
  border: "1px solid var(--border)",
  borderRadius: 9,
  color: "var(--hi)",
  fontSize: 13.5,
  outline: "none",
  lineHeight: 1.5,
};
const labelStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  color: "var(--lo)",
  textTransform: "uppercase",
  letterSpacing: ".07em",
  display: "flex",
  alignItems: "center",
  gap: 8,
};
const hintStyle: React.CSSProperties = { fontSize: 12, color: "var(--mid)", marginTop: 3, lineHeight: 1.45 };
const primaryBtn = (on: boolean): React.CSSProperties => ({
  padding: "10px 18px",
  background: on ? "var(--accent)" : "var(--bg2)",
  color: on ? "#06121c" : "var(--lo)",
  fontWeight: 700,
  borderRadius: 9,
  fontSize: 13.5,
});
const ghostBtn: React.CSSProperties = {
  padding: "9px 14px",
  border: "1px solid var(--border)",
  color: "var(--mid)",
  borderRadius: 9,
  fontSize: 12.5,
  fontWeight: 600,
};

function Field({ label, required, hint, children }: { label: string; required?: boolean; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <label style={labelStyle}>
        {label}
        {required && <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, padding: "1px 5px", borderRadius: 4, background: "var(--warn-soft)", color: "var(--warn)", textTransform: "none", letterSpacing: 0 }}>required</span>}
      </label>
      {hint && <div style={hintStyle}>{hint}</div>}
      <div style={{ marginTop: 7 }}>{children}</div>
    </div>
  );
}

// A list edited as "one item per line" — robust, keyboard-friendly, no drag-drop.
function ListField({ value, onChange, placeholder, rows = 3 }: { value: string[]; onChange: (v: string[]) => void; placeholder: string; rows?: number }) {
  const [text, setText] = useState(value.join("\n"));
  const lastValue = useRef(value.join("\n"));
  useEffect(() => {
    const joined = value.join("\n");
    if (joined !== lastValue.current) {
      lastValue.current = joined;
      setText(joined);
    }
  }, [value]);
  return (
    <textarea
      value={text}
      rows={rows}
      placeholder={placeholder}
      onChange={(e) => {
        setText(e.target.value);
        const items = e.target.value.split("\n").map((s) => s.trim()).filter(Boolean);
        lastValue.current = items.join("\n");
        onChange(items);
      }}
      style={{ ...inputStyle, resize: "vertical", fontFamily: "var(--ui)" }}
    />
  );
}

export default function OnboardingView() {
  const {
    api,
    onboardingBriefId,
    setOnboardingBriefId,
    launchBrief,
    startQuickSession,
    library,
    loadLibrary,
    uploadFiles,
    setHyp,
    setMainView,
    sendNotice,
    clearNotice,
  } = useApp();

  const [brief, setBrief] = useState<BriefRecord | null>(null);
  const [fields, setFields] = useState<BriefFields>(EMPTY);
  const [drafts, setDrafts] = useState<BriefSummary[]>([]);
  const [step, setStep] = useState(0);
  const [save, setSave] = useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // hypothesis step
  const [hypSession, setHypSession] = useState<HypSession | null>(null);
  const [hypBusy, setHypBusy] = useState<string | null>(null);

  // review step
  const [preview, setPreview] = useState<BriefPreview | null>(null);
  const [launching, setLaunching] = useState(false);

  // ---- load the open draft (or the drafts list) ----
  const loadDrafts = useCallback(async () => {
    try {
      const all = await api.listBriefs();
      setDrafts(all.filter((d) => d.status === "draft"));
    } catch {
      setDrafts([]);
    }
  }, [api]);

  useEffect(() => {
    // The first edit creates the draft and sets the id — that record is
    // already in hand, so don't refetch (it would flash "Loading…" and could
    // clobber keystrokes typed in the meantime).
    if (onboardingBriefId && briefRef.current?.id === onboardingBriefId) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      await loadDrafts();
      if (onboardingBriefId) {
        try {
          const b = await api.getBrief(onboardingBriefId);
          if (cancelled) return;
          if (b.status === "launched") {
            setOnboardingBriefId(null); // stale pointer to a launched brief
          } else {
            setBrief(b);
            setFields(fieldsOf(b));
          }
        } catch {
          if (!cancelled) setOnboardingBriefId(null);
        }
      }
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onboardingBriefId, api]);

  // ---- autosave (debounced). The first edit creates the draft. ----
  const fieldsRef = useRef(fields);
  fieldsRef.current = fields;
  const briefRef = useRef<BriefRecord | null>(null);
  briefRef.current = brief;
  const creating = useRef<Promise<BriefRecord> | null>(null);
  const timer = useRef<number | null>(null);

  const ensureBrief = useCallback(async (): Promise<BriefRecord> => {
    if (briefRef.current) return briefRef.current;
    if (!creating.current) {
      creating.current = api
        .createBrief(fieldsRef.current)
        .then((b) => {
          setBrief(b);
          briefRef.current = b;
          setOnboardingBriefId(b.id);
          return b;
        })
        .finally(() => {
          // Clear the in-flight guard on success AND failure, otherwise a
          // transient error would pin every later save to the same rejection.
          creating.current = null;
        });
    }
    return creating.current;
  }, [api, setOnboardingBriefId]);

  const flush = useCallback(async () => {
    if (timer.current) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    setSave("saving");
    try {
      const b = await ensureBrief();
      const updated = await api.updateBrief(b.id, fieldsRef.current);
      setBrief(updated);
      briefRef.current = updated;
      setSave("saved");
      return updated;
    } catch (e) {
      setSave("error");
      setError(e instanceof HttpError ? e.message : "Could not save the brief.");
      return null;
    }
  }, [api, ensureBrief]);

  const edit = useCallback(
    (patch: Partial<BriefFields>) => {
      setFields((f) => ({ ...f, ...patch }));
      setSave("dirty");
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => void flush(), 700);
    },
    [flush],
  );
  // Drop a pending autosave without firing it (switching/discarding drafts).
  const cancelSave = useCallback(() => {
    if (timer.current) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);
  // On unmount (e.g. the user clicks another view mid-edit) flush a pending
  // save instead of dropping it, so nothing typed is lost.
  const flushRef = useRef(flush);
  flushRef.current = flush;
  useEffect(
    () => () => {
      if (timer.current) {
        window.clearTimeout(timer.current);
        timer.current = null;
        void flushRef.current();
      }
    },
    [],
  );

  // ---- hypotheses ----
  useEffect(() => {
    const hid = brief?.hypothesis_session_id;
    if (!hid) {
      setHypSession(null);
      return;
    }
    let cancelled = false;
    api.getHypothesis(hid).then((s) => { if (!cancelled) setHypSession(s); }).catch(() => { if (!cancelled) setHypSession(null); });
    return () => { cancelled = true; };
  }, [brief?.hypothesis_session_id, api]);

  const generate = async () => {
    setError(null);
    setHypBusy("Generating hypotheses from your brief…");
    try {
      const b = (await flush()) ?? (await ensureBrief());
      const s = await api.briefHypotheses(b.id);
      setHypSession(s);
      const fresh = await api.getBrief(b.id);
      setBrief(fresh);
    } catch (e) {
      setError(e instanceof HttpError ? e.message : "Generation failed. Try sharpening the research question.");
    } finally {
      setHypBusy(null);
    }
  };
  const selectHyp = async (hypId: string) => {
    if (!hypSession || !brief) return;
    try {
      const s = await api.selectHypothesis(hypSession.id, hypId);
      setHypSession(s);
      setBrief(await api.getBrief(brief.id)); // the server synced the choice into the brief
    } catch {
      setError("Could not record the selection.");
    }
  };
  const refineHyp = async (parentId: string, feedback: string) => {
    if (!hypSession) return;
    setHypBusy("Generating a new set from your pick…");
    try {
      setHypSession(await api.refineHypothesis(hypSession.id, parentId, feedback || undefined));
    } catch {
      setError("Refinement failed. Try different feedback.");
    } finally {
      setHypBusy(null);
    }
  };

  // ---- review ----
  const loadPreview = useCallback(async () => {
    setPreview(null);
    const b = (await flush()) ?? briefRef.current;
    if (!b) return;
    try {
      setPreview(await api.previewBrief(b.id));
    } catch (e) {
      setError(e instanceof HttpError ? e.message : "Could not render the preview.");
    }
  }, [api, flush]);
  useEffect(() => {
    if (step === 3) void loadPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  const launch = async () => {
    const b = briefRef.current;
    if (!b) return;
    setLaunching(true);
    setError(null); // on failure the store puts the server's reason in sendNotice, which the banner shows
    await launchBrief(b.id);
    setLaunching(false);
  };

  // ---- navigation ----
  const canLeaveDefine = fields.title.trim().length > 0 && fields.research_question.trim().length > 0;
  const goto = async (i: number) => {
    setError(null);
    if (i > 0 && !briefRef.current) await ensureBrief();
    await flush();
    setStep(i);
  };
  const resume = async (d: BriefSummary) => {
    await flush(); // don't lose edits on the draft being left
    cancelSave();
    setStep(0);
    setBrief(null);
    briefRef.current = null;
    setFields(EMPTY);
    setHypSession(null);
    setPreview(null);
    setSave("idle");
    setOnboardingBriefId(d.id);
  };
  const fresh = () => {
    cancelSave();
    setStep(0);
    setBrief(null);
    briefRef.current = null;
    setFields(EMPTY);
    setHypSession(null);
    setPreview(null);
    setSave("idle");
    setOnboardingBriefId(null);
  };
  // Start another brief, keeping the current one as a resumable draft.
  const newBrief = async () => {
    await flush();
    fresh();
    void loadDrafts();
  };
  const discard = async () => {
    cancelSave(); // a queued autosave must not resurrect the deleted draft
    const b = briefRef.current;
    fresh();
    if (b) {
      try { await api.deleteBrief(b.id); } catch { /* ignore */ }
    }
    void loadDrafts();
  };

  // ---- data step helpers ----
  useEffect(() => {
    if (step === 1) loadLibrary();
  }, [step, loadLibrary]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const el = dirInputRef.current;
    if (el) {
      el.setAttribute("webkitdirectory", "");
      el.setAttribute("directory", "");
    }
  }, [step]);
  const [uploading, setUploading] = useState(false);
  const upload = async (fl: FileList | null) => {
    if (!fl || !fl.length) return;
    setUploading(true);
    try {
      // Only what the server confirmed landed gets attached (a folder upload
      // attaches its top-level folder once).
      const landed = await uploadFiles(fl);
      const names = landed.map((n) => (n.includes("/") ? n.split("/")[0] : n));
      const uniq = Array.from(new Set(names));
      const cur = fieldsRef.current.data;
      const add = uniq.filter((n) => !cur.some((d) => d.path === n)).map((n) => ({ path: n, description: "" }));
      if (add.length) edit({ data: [...cur, ...add] });
    } finally {
      setUploading(false);
    }
  };
  // Library entries the human can attach: top-level files + top-level folders
  // (a folder attaches its whole subtree, preserving structure) + nested files.
  const attachable = useMemo(() => {
    const folders = new Map<string, { count: number; size: number }>();
    const rows: { path: string; size: number; kind: "file" | "folder" }[] = [];
    for (const f of library) {
      const parts = f.name.split("/");
      if (parts.length > 1) {
        const top = parts[0];
        const cur = folders.get(top) ?? { count: 0, size: 0 };
        folders.set(top, { count: cur.count + 1, size: cur.size + f.size });
      }
      rows.push({ path: f.name, size: f.size, kind: "file" });
    }
    const out: { path: string; size: number; kind: "file" | "folder"; count?: number }[] = [];
    for (const [top, v] of folders) out.push({ path: top, size: v.size, kind: "folder", count: v.count });
    out.push(...rows.sort((a, b) => a.path.localeCompare(b.path)));
    return out;
  }, [library]);
  const attached = (p: string) => fields.data.some((d) => d.path === p);
  const toggleAttach = (p: string) => {
    if (attached(p)) edit({ data: fields.data.filter((d) => d.path !== p) });
    else edit({ data: [...fields.data, { path: p, description: "" }] });
  };
  const describe = (p: string, description: string) => edit({ data: fields.data.map((d) => (d.path === p ? { ...d, description } : d)) });

  const saveLabel = save === "saving" ? "saving…" : save === "saved" ? "saved ✓" : save === "dirty" ? "unsaved" : save === "error" ? "save failed" : "";
  const canLaunch =
    !launching && !!preview && preview.missing.length === 0 && preview.data_problems.length === 0 && !preview.too_large;

  // ============================================================ render
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      {/* header */}
      <div style={{ padding: "15px 24px 13px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "flex-start", gap: 14 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 9 }}>
            New research session
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--accent-soft)", color: "var(--accent)" }}>onboarding</span>
            {brief && <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{brief.id} · {saveLabel}</span>}
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: "var(--mid)" }}>
            Define the problem in a fixed format, attach your data, run the hypothesis search, then launch. The supervisor receives the whole brief as its first turn.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flex: "0 0 auto" }}>
          {brief && (
            <button onClick={() => void newBrief()} title="Keep this draft and start another" style={ghostBtn}>New brief</button>
          )}
          {brief && (
            <button onClick={() => void discard()} title="Delete this draft" style={ghostBtn}>Discard draft</button>
          )}
          <button onClick={startQuickSession} title="Skip onboarding: start from a one-line task" style={ghostBtn}>Quick start instead</button>
        </div>
      </div>

      {/* step pills */}
      <div style={{ display: "flex", gap: 6, padding: "12px 24px 0", flexWrap: "wrap" }}>
        {STEPS.map((s, i) => {
          const on = i === step;
          const done = i < step;
          return (
            <button
              key={s}
              onClick={() => void goto(i)}
              disabled={i > 0 && !canLeaveDefine}
              style={{ display: "flex", alignItems: "center", gap: 7, padding: "6px 12px", borderRadius: 8, border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`, background: on ? "var(--accent-soft)" : "transparent", color: on ? "var(--accent)" : done ? "var(--hi)" : "var(--mid)", fontSize: 12.5, fontWeight: 600, opacity: i > 0 && !canLeaveDefine ? 0.5 : 1 }}
            >
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, width: 16, height: 16, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", background: done ? "var(--ok)" : on ? "var(--accent)" : "var(--bg3)", color: done || on ? "#06121c" : "var(--lo)" }}>
                {done ? "✓" : i + 1}
              </span>
              {s}
            </button>
          );
        })}
      </div>

      {(error || sendNotice) && (
        <div style={{ margin: "12px 24px 0", padding: "9px 13px", background: "var(--err-soft)", border: "1px solid var(--err)", borderRadius: 9, fontSize: 12.5, color: "var(--err)", display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ flex: 1 }}>{error ?? sendNotice}</span>
          <button onClick={() => { setError(null); clearNotice(); }} style={{ color: "var(--err)" }}>✕</button>
        </div>
      )}

      {/* body */}
      <div style={{ flex: 1, overflowY: "auto", padding: "18px 24px 28px" }}>
        {loading ? (
          <div style={{ color: "var(--lo)", fontSize: 13 }}>Loading…</div>
        ) : (
          <div style={{ maxWidth: 820 }}>
            {/* resume / switch drafts (other drafts than the open one) */}
            {step === 0 && drafts.filter((d) => d.id !== brief?.id).length > 0 && (
              <div style={{ marginBottom: 20, padding: "12px 14px", border: "1px solid var(--border)", borderRadius: 11, background: "var(--bg1)" }}>
                <div style={labelStyle}>{brief ? "Switch to another draft" : "Resume a draft"}</div>
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                  {drafts.filter((d) => d.id !== brief?.id).map((d) => (
                    <button key={d.id} onClick={() => void resume(d)} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderRadius: 8, textAlign: "left", background: "var(--bg2)" }}>
                      <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: "var(--hi)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.title || d.research_question || "(untitled draft)"}</span>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{d.data_count} file{d.data_count === 1 ? "" : "s"}{d.hypothesis ? " · hypothesis ✓" : ""} · {d.updated ?? ""}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {step === 0 && (
              <DefineStep fields={fields} edit={edit} />
            )}

            {step === 1 && (
              <div>
                <div style={{ fontSize: 15, fontWeight: 600, color: "var(--hi)" }}>Upload and attach the data</div>
                <div style={{ ...hintStyle, marginBottom: 14 }}>
                  Files go to your persistent library (shared across sessions) and are attached to this brief with a short description so the agent knows what each one is. Any file type; folders keep their structure. Very large files (&gt;5 GB) can be copied straight into <span style={{ fontFamily: "var(--mono)" }}>state/library/</span> on the host and attached here.
                </div>
                <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                  <label style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 6, padding: "14px 8px", border: "1px dashed var(--border-hi)", borderRadius: 10, color: "var(--mid)", fontSize: 13, cursor: "pointer", background: "var(--bg1)" }}>
                    <span style={{ fontSize: 15 }}>↑</span> {uploading ? "Uploading…" : "Upload files"}
                    <input ref={fileInputRef} type="file" multiple style={{ display: "none" }} onChange={(e) => { void upload(e.target.files); if (fileInputRef.current) fileInputRef.current.value = ""; }} />
                  </label>
                  <label style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 6, padding: "14px 8px", border: "1px dashed var(--border-hi)", borderRadius: 10, color: "var(--mid)", fontSize: 13, cursor: "pointer", background: "var(--bg1)" }}>
                    <span style={{ fontSize: 15 }}>📁</span> Upload a folder
                    <input ref={dirInputRef} type="file" multiple style={{ display: "none" }} onChange={(e) => { void upload(e.target.files); if (dirInputRef.current) dirInputRef.current.value = ""; }} />
                  </label>
                </div>

                <Field label="Attached to this brief" hint="Tick what this session should use. Describe each item in a few words (what it is, units, how it was produced).">
                  {fields.data.length === 0 && <div style={{ fontSize: 12.5, color: "var(--lo)" }}>Nothing attached yet — upload above or tick library files below. A brief without data is fine for literature-style questions.</div>}
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {fields.data.map((d: BriefDataItem) => (
                      <div key={d.path} style={{ display: "flex", alignItems: "center", gap: 9, padding: "8px 10px", border: "1px solid var(--border)", borderRadius: 9, background: "var(--bg1)" }}>
                        <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--hi)", flex: "0 0 34%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.path}>{d.path}</span>
                        <input value={d.description} placeholder="what is this?" onChange={(e) => describe(d.path, e.target.value)} style={{ ...inputStyle, padding: "6px 9px", fontSize: 12.5, flex: 1 }} />
                        <button onClick={() => toggleAttach(d.path)} title="Detach" style={{ color: "var(--lo)", fontSize: 12, padding: "3px 6px" }}>✕</button>
                      </div>
                    ))}
                  </div>
                </Field>

                <Field label="Library" hint="Everything you have uploaded so far, across all sessions.">
                  {attachable.length === 0 && <div style={{ fontSize: 12.5, color: "var(--lo)" }}>Library is empty.</div>}
                  <div style={{ border: attachable.length ? "1px solid var(--border)" : "none", borderRadius: 9, background: "var(--bg2)", maxHeight: 260, overflowY: "auto" }}>
                    {attachable.map((r) => {
                      const on = attached(r.path);
                      return (
                        <label key={`${r.kind}:${r.path}`} style={{ display: "flex", alignItems: "center", gap: 9, padding: "7px 10px", cursor: "pointer", borderBottom: "1px solid var(--border)" }}>
                          <input type="checkbox" checked={on} onChange={() => toggleAttach(r.path)} />
                          <span style={{ fontSize: 13 }}>{r.kind === "folder" ? "📁" : "📄"}</span>
                          <span style={{ flex: 1, minWidth: 0, fontFamily: "var(--mono)", fontSize: 12, color: on ? "var(--hi)" : "var(--mid)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.path}{r.kind === "folder" ? "/" : ""}</span>
                          {r.kind === "folder" && <span style={{ fontSize: 10, color: "var(--lo)" }}>{r.count} files</span>}
                          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{fmtSize(r.size)}</span>
                        </label>
                      );
                    })}
                  </div>
                </Field>

                <Field label="Data notes" hint="Provenance, formats, known quirks, what to ignore.">
                  <textarea value={fields.data_notes} rows={3} placeholder="e.g. OD600 read every 15 min; columns after 'blank' are controls; wells B3–B5 contaminated." onChange={(e) => edit({ data_notes: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                </Field>
              </div>
            )}

            {step === 2 && (
              <div>
                <div style={{ fontSize: 15, fontWeight: 600, color: "var(--hi)", display: "flex", alignItems: "center", gap: 9 }}>
                  Hypothesis search
                  <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--bg2)", color: "var(--lo)" }} title="Interim generator; the Google co-scientist protocol is planned (docs/plans/H1)">interim</span>
                </div>
                <div style={{ ...hintStyle, marginBottom: 14 }}>
                  Seeded from the whole brief (question, background, data, constraints), the AI proposes a few <b style={{ color: "var(--hi)", fontWeight: 600 }}>parallel</b> hypotheses. Select one to make it the session's working hypothesis, or pick one and say what to do differently to get a fresh set. You can also continue without one.
                </div>

                {brief?.hypothesis && (
                  <div style={{ marginBottom: 14, padding: "11px 14px", background: "var(--ok-soft)", border: "1px solid var(--ok)", borderRadius: 10 }}>
                    <div style={{ fontSize: 10.5, fontWeight: 700, color: "var(--ok)", textTransform: "uppercase", letterSpacing: ".06em" }}>Working hypothesis</div>
                    <div style={{ marginTop: 4, fontSize: 13.5, color: "var(--hi)", lineHeight: 1.4 }}>{brief.hypothesis.statement}</div>
                  </div>
                )}

                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 14, flexWrap: "wrap" }}>
                  <button onClick={() => void generate()} disabled={!!hypBusy || !canLeaveDefine} style={primaryBtn(!hypBusy && canLeaveDefine)}>
                    {hypBusy ? "Generating…" : hypSession ? "Regenerate from scratch" : "Generate hypotheses from this brief"}
                  </button>
                  {hypSession && (
                    <button onClick={() => { setHyp(hypSession); setMainView("hypothesis"); }} style={ghostBtn} title="Open this search on the Hypotheses page">
                      Open on the Hypotheses page →
                    </button>
                  )}
                  {hypBusy && (
                    <span style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--mid)", fontSize: 12.5 }}>
                      <span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--accent)", animation: "pulse 1.4s infinite" }} />
                      {hypBusy} <span style={{ color: "var(--lo)" }}>(up to a minute)</span>
                    </span>
                  )}
                </div>

                {hypSession && (
                  <HypothesisCards session={hypSession} loading={hypBusy} onSelect={(id) => void selectHyp(id)} onRefine={(p, f) => void refineHyp(p, f)} />
                )}
              </div>
            )}

            {step === 3 && (
              <div>
                <div style={{ fontSize: 15, fontWeight: 600, color: "var(--hi)" }}>Review what the agent will receive</div>
                <div style={{ ...hintStyle, marginBottom: 14 }}>
                  This is the exact first message the research supervisor gets. Fix anything that reads wrong by going back a step — the text is rendered by the server from your brief, not editable here.
                </div>
                {preview?.missing.length ? (
                  <div style={{ marginBottom: 10, padding: "9px 13px", background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 9, fontSize: 12.5, color: "var(--hi)" }}>
                    Required before launch: <b>{preview.missing.join(", ").replace(/_/g, " ")}</b>.
                  </div>
                ) : null}
                {preview?.data_problems.length ? (
                  <div style={{ marginBottom: 10, padding: "9px 13px", background: "var(--err-soft)", border: "1px solid var(--err)", borderRadius: 9, fontSize: 12.5, color: "var(--err)", whiteSpace: "pre-wrap" }}>
                    Attached data that cannot be resolved:{"\n"}{preview.data_problems.map((p) => `• ${p}`).join("\n")}
                  </div>
                ) : null}
                {preview?.too_large ? (
                  <div style={{ marginBottom: 10, padding: "9px 13px", background: "var(--err-soft)", border: "1px solid var(--err)", borderRadius: 9, fontSize: 12.5, color: "var(--err)" }}>
                    The brief is too long to hand to the agent ({fmtSize(preview.size_bytes)} of {fmtSize(preview.max_bytes)} allowed). Shorten the free-text fields, or put long material in an attached file.
                  </div>
                ) : null}
                {!brief?.hypothesis && preview && (
                  <div style={{ marginBottom: 10, padding: "9px 13px", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 9, fontSize: 12.5, color: "var(--mid)" }}>
                    No working hypothesis selected — the agent will propose its own early and ask you to choose. <button onClick={() => void goto(2)} style={{ color: "var(--accent)", fontWeight: 600 }}>Run the hypothesis search →</button>
                  </div>
                )}
                <pre style={{ margin: 0, padding: "14px 16px", background: "var(--bg1)", border: "1px solid var(--border)", borderRadius: 11, fontFamily: "var(--mono)", fontSize: 12, color: "var(--hi)", whiteSpace: "pre-wrap", lineHeight: 1.55, minHeight: 120 }}>
                  {preview ? preview.text : "Rendering…"}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>

      {/* footer nav */}
      <div style={{ padding: "12px 24px 16px", borderTop: "1px solid var(--border)", background: "var(--bg1)", display: "flex", alignItems: "center", gap: 10 }}>
        <button onClick={() => void goto(Math.max(0, step - 1))} disabled={step === 0} style={{ ...ghostBtn, opacity: step === 0 ? 0.4 : 1 }}>← Back</button>
        <span style={{ flex: 1, fontSize: 11.5, color: "var(--lo)" }}>
          {step === 0 && (canLeaveDefine ? "Autosaved. Continue to attach data." : "Title and research question are required to continue.")}
          {step === 1 && `${fields.data.length} item${fields.data.length === 1 ? "" : "s"} attached.`}
          {step === 2 && (brief?.hypothesis ? "Working hypothesis set." : "Optional — you can launch without a hypothesis.")}
          {step === 3 && "Launching starts the supervisor's first turn immediately."}
        </span>
        {step < 3 ? (
          <button onClick={() => void goto(step + 1)} disabled={!canLeaveDefine} style={primaryBtn(canLeaveDefine)}>
            {step === 2 && !brief?.hypothesis ? "Continue without a hypothesis →" : "Continue →"}
          </button>
        ) : (
          <button
            onClick={() => void launch()}
            disabled={!canLaunch}
            style={{ ...primaryBtn(canLaunch), background: canLaunch ? "var(--ok)" : "var(--bg2)" }}
          >
            {launching ? "Launching…" : "Launch research session"}
          </button>
        )}
      </div>
    </div>
  );
}

function DefineStep({ fields, edit }: { fields: BriefFields; edit: (p: Partial<BriefFields>) => void }) {
  return (
    <div>
      <div style={{ fontSize: 15, fontWeight: 600, color: "var(--hi)" }}>Define the problem</div>
      <div style={{ ...hintStyle, marginBottom: 16 }}>
        The same fixed format for every session, so the agent always knows where to look for the question, the data, and the definition of done.
      </div>
      <Field label="Title" required hint="One line. Becomes the session's name.">
        <input value={fields.title} placeholder="e.g. Antibiotic tolerance in stationary-phase E. coli" onChange={(e) => edit({ title: e.target.value })} style={inputStyle} />
      </Field>
      <Field label="Research question" required hint="The one question this session must answer. Specific enough that an answer could be wrong.">
        <textarea value={fields.research_question} rows={2} placeholder="e.g. Which physiological state at the time of exposure best predicts survival without resistance mutations?" onChange={(e) => edit({ research_question: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
      </Field>
      <Field label="Domain" hint="Field or discipline. Helps the hypothesis search and the choice of methods.">
        <input value={fields.domain} placeholder="e.g. microbiology / pharmacology / materials" onChange={(e) => edit({ domain: e.target.value })} style={inputStyle} />
      </Field>
      <Field label="Background" hint="Context and motivation: what is already known, what has been tried, why this matters now.">
        <textarea value={fields.background} rows={4} placeholder="What do we know? What was tried before and what happened?" onChange={(e) => edit({ background: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
      </Field>
      <Field label="Objectives" hint="Concrete aims, one per line.">
        <ListField value={fields.objectives} onChange={(v) => edit({ objectives: v })} placeholder={"Quantify survival vs growth phase\nIdentify a predictive marker"} />
      </Field>
      <Field label="Constraints" hint="Scope limits, time, compute, ethics, things explicitly out of bounds.">
        <textarea value={fields.constraints} rows={2} placeholder="e.g. analysis only, no new experiments; finish within one session; no external data downloads" onChange={(e) => edit({ constraints: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
      </Field>
      <Field label="Success criteria" hint="What a convincing answer looks like. The agent treats this as the definition of done.">
        <textarea value={fields.success_criteria} rows={2} placeholder="e.g. a marker with AUC > 0.8 on held-out strains, with the analysis reproducible from the attached CSVs" onChange={(e) => edit({ success_criteria: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
      </Field>
      <Field label="Deliverables" hint="Expected outputs, one per line. Written under the session's results/ folder.">
        <ListField value={fields.deliverables} onChange={(v) => edit({ deliverables: v })} placeholder={"Report (PDF)\nMarker table (CSV)\nFigures"} />
      </Field>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../state/store";
import { HttpError } from "../lib/api";
import { fmtSize } from "../lib/format";
import HypothesisCards from "./HypothesisCards";
import AdvisorCards, { AdvisorPill, useAdvisor } from "./AdvisorCards";
import NearTree from "./NearTree";
import type { BriefDataItem, BriefFields, BriefPreview, BriefRecord, BriefSummary, HypSession } from "../lib/types";

// Onboarding (O1 → O2). Every session starts here. Five steps that adapt to
// what the researcher has answered:
//   1 Problem statement — the five questions we ask every researcher, with a
//     completeness dot per question, keywords, and the share toggle. Leaving
//     this step with questions 1–3 answered registers the problem on the org
//     project tree and starts the background advisor.
//   2 Data — upload/attach library files (retrievable any time) + question 4.
//   3 Project tree & advisor — the near-tree, the advisor's cards, one-click
//     human-gated harness / tool imports.
//   4 Hypotheses (optional) — the parallel-set search seeded from the brief.
//   5 Review & launch — the exact opening message, gaps by question, warnings.
// The brief is server-authoritative (api/onboarding.py, api/projects.py).

const STEPS = ["Problem statement", "Data", "Project tree & advisor", "Hypotheses", "Review & launch"] as const;

const EMPTY: BriefFields = {
  title: "", domain: "", research_question: "", objectives: [],
  significance: "", prior_work: "", open_gap: "", evaluation_protocol: "", success_criteria: "",
  data: [], task_definition: "", existing_results: "", data_access: "", data_notes: "",
  constraints: "", deliverables: [], keywords: [], visibility: "org", parent_node: null,
};
const fieldsOf = (b: BriefRecord): BriefFields => ({
  title: b.title ?? "", domain: b.domain ?? "", research_question: b.research_question ?? "", objectives: b.objectives ?? [],
  significance: b.significance ?? "", prior_work: b.prior_work ?? "", open_gap: b.open_gap ?? "", evaluation_protocol: b.evaluation_protocol ?? "", success_criteria: b.success_criteria ?? "",
  data: b.data ?? [], task_definition: b.task_definition ?? "", existing_results: b.existing_results ?? "", data_access: b.data_access ?? "", data_notes: b.data_notes ?? "",
  constraints: b.constraints ?? "", deliverables: b.deliverables ?? [], keywords: b.keywords ?? [], visibility: b.visibility ?? "org", parent_node: b.parent_node ?? null,
});
const has = (s: string) => s.trim().length > 0;
type Dot = "empty" | "partial" | "full";
// Mirrors api/onboarding.py::completeness — Q4 is answered on the Data step.
function dots(f: BriefFields): Dot[] {
  const d = (parts: boolean[]): Dot => (parts.every(Boolean) ? "full" : parts.some(Boolean) ? "partial" : "empty");
  return [
    d([has(f.title), has(f.research_question)]),
    d([has(f.significance)]),
    d([has(f.prior_work), has(f.open_gap)]),
    d([has(f.evaluation_protocol)]),
    d([has(f.task_definition), has(f.existing_results), has(f.data_access)]),
  ];
}
const canLaunchOf = (f: BriefFields) => has(f.title) && has(f.research_question);
const registrableOf = (f: BriefFields) => canLaunchOf(f) && has(f.significance) && has(f.prior_work) && has(f.open_gap) && has(f.evaluation_protocol);
const QLABEL = [
  "Definition of the problem",
  "Why is it scientifically important?",
  "What has existing work achieved, and what remains genuinely open?",
  "How is progress evaluated objectively — no hackable proxy, shortcut or subjective judgement?",
  "The exact dataset, metadata, task definition, evaluation protocol, existing results and permissions",
];

// ---- styled primitives (inline styles keep parity with the rest of ui3) ----
const inputStyle: React.CSSProperties = { width: "100%", padding: "10px 12px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 9, color: "var(--hi)", fontSize: 13.5, outline: "none", lineHeight: 1.5 };
const labelStyle: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", display: "flex", alignItems: "center", gap: 8 };
const hintStyle: React.CSSProperties = { fontSize: 12, color: "var(--mid)", marginTop: 3, lineHeight: 1.45 };
const primaryBtn = (on: boolean): React.CSSProperties => ({ padding: "10px 18px", background: on ? "var(--accent)" : "var(--bg2)", color: on ? "#06121c" : "var(--lo)", fontWeight: 700, borderRadius: 9, fontSize: 13.5 });
const ghostBtn: React.CSSProperties = { padding: "9px 14px", border: "1px solid var(--border)", color: "var(--mid)", borderRadius: 9, fontSize: 12.5, fontWeight: 600 };
const DOT_COLOR: Record<Dot, string> = { empty: "var(--bg3)", partial: "var(--warn)", full: "var(--ok)" };

function Field({ label, required, hint, children }: { label: string; required?: boolean; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 16 }}>
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
    if (joined !== lastValue.current) { lastValue.current = joined; setText(joined); }
  }, [value]);
  return (
    <textarea value={text} rows={rows} placeholder={placeholder}
      onChange={(e) => { setText(e.target.value); const items = e.target.value.split("\n").map((s) => s.trim()).filter(Boolean); lastValue.current = items.join("\n"); onChange(items); }}
      style={{ ...inputStyle, resize: "vertical", fontFamily: "var(--ui)" }} />
  );
}

function KeywordField({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const items = draft.split(/[,\n;]/).map((s) => s.trim()).filter(Boolean);
    if (!items.length) return;
    const merged = [...value];
    for (const k of items) if (!merged.some((x) => x.toLowerCase() === k.toLowerCase())) merged.push(k);
    onChange(merged);
    setDraft("");
  };
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", padding: "7px 9px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 9 }}>
      {value.map((k) => (
        <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "3px 8px", borderRadius: 7, background: "var(--accent-soft)", color: "var(--accent)", fontSize: 12 }}>
          {k}<button onClick={() => onChange(value.filter((x) => x !== k))} style={{ color: "var(--accent)", fontSize: 11 }}>✕</button>
        </span>
      ))}
      <input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }} onBlur={add} placeholder={value.length ? "add…" : "e.g. transmon, coherence, T2 (Enter to add)"} style={{ flex: 1, minWidth: 160, background: "transparent", border: "none", outline: "none", color: "var(--hi)", fontSize: 13 }} />
    </div>
  );
}

function QBlock({ q, dot, title, hint, children, blockRef }: { q: number; dot: Dot; title: string; hint: string; children: React.ReactNode; blockRef?: (el: HTMLDivElement | null) => void }) {
  return (
    <div ref={blockRef} style={{ marginBottom: 18, padding: "14px 16px 4px", border: "1px solid var(--border)", borderLeft: `3px solid ${DOT_COLOR[dot]}`, borderRadius: "0 12px 12px 0", background: "var(--bg1)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 4 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, fontWeight: 700, padding: "1px 7px", borderRadius: 5, background: "var(--bg3)", color: "var(--mid)" }}>Q{q}</span>
        <span style={{ fontSize: 14, fontWeight: 600, color: "var(--hi)" }}>{title}</span>
        <span style={{ flex: 1 }} />
        <span style={{ width: 9, height: 9, borderRadius: "50%", background: DOT_COLOR[dot] }} title={dot} />
      </div>
      <div style={{ ...hintStyle, marginBottom: 12 }}>{hint}</div>
      {children}
    </div>
  );
}

export default function OnboardingView() {
  const { api, onboardingBriefId, setOnboardingBriefId, launchBrief, startQuickSession, library, loadLibrary, uploadFiles, downloadLibrary, setHyp, setMainView, sendNotice, clearNotice, openProject, roles, loadRoles, user } = useApp();

  const [brief, setBrief] = useState<BriefRecord | null>(null);
  const [fields, setFields] = useState<BriefFields>(EMPTY);
  const [drafts, setDrafts] = useState<BriefSummary[]>([]);
  const [step, setStep] = useState(0);
  const [save, setSave] = useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [nearKey, setNearKey] = useState(0);
  const [parentTitle, setParentTitle] = useState<string | null>(null);
  const [hypSession, setHypSession] = useState<HypSession | null>(null);
  const [hypBusy, setHypBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<BriefPreview | null>(null);
  const [launching, setLaunching] = useState(false);
  const advisor = useAdvisor(brief?.node_id ?? null);
  const qRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => { void loadRoles(); }, [loadRoles]);

  // ---- load the open draft (or the drafts list) ----
  const loadDrafts = useCallback(async () => {
    try { setDrafts((await api.listBriefs()).filter((d) => d.status === "draft")); } catch { setDrafts([]); }
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
          if (b.status === "launched") setOnboardingBriefId(null);
          else { setBrief(b); setFields(fieldsOf(b)); }
        } catch {
          if (!cancelled) setOnboardingBriefId(null);
        }
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onboardingBriefId, api]);

  // "Derives from" chip: the parent's title.
  useEffect(() => {
    const pn = fields.parent_node;
    if (!pn) { setParentTitle(null); return; }
    let cancelled = false;
    api.getProject(pn).then((n) => { if (!cancelled) setParentTitle(n.title); }).catch(() => { if (!cancelled) setParentTitle(pn); });
    return () => { cancelled = true; };
  }, [fields.parent_node, api]);

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
      creating.current = api.createBrief(fieldsRef.current)
        .then((b) => { setBrief(b); briefRef.current = b; setOnboardingBriefId(b.id); return b; })
        .finally(() => { creating.current = null; });
    }
    return creating.current;
  }, [api, setOnboardingBriefId]);

  const flush = useCallback(async () => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null; }
    // Nothing typed yet and no draft in flight: there is nothing to save, and
    // creating an empty draft here would litter the "Resume a draft" list.
    if (!briefRef.current && !creating.current) { setSave("idle"); return null; }
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

  const edit = useCallback((patch: Partial<BriefFields>) => {
    setFields((f) => ({ ...f, ...patch }));
    setSave("dirty");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void flush(), 700);
  }, [flush]);
  const cancelSave = useCallback(() => { if (timer.current) { window.clearTimeout(timer.current); timer.current = null; } }, []);
  const flushRef = useRef(flush);
  flushRef.current = flush;
  useEffect(() => () => { if (timer.current) { window.clearTimeout(timer.current); timer.current = null; void flushRef.current(); } }, []);

  // ---- register on the project tree (idempotent; the server dedupes by statement hash) ----
  const register = useCallback(async (explicit = false): Promise<boolean> => {
    if (!registrableOf(fieldsRef.current)) return false;
    setRegistering(true);
    try {
      const b = (await flush()) ?? (await ensureBrief());
      const out = await api.registerBrief(b.id, fieldsRef.current.visibility);
      setBrief(out.brief);
      briefRef.current = out.brief;
      setNearKey((k) => k + 1);
      if (out.advisor_started || explicit) {
        advisor.expectRun();
        window.setTimeout(() => void advisor.refresh(), 800);
      }
      return true;
    } catch (e) {
      setError(e instanceof HttpError ? e.message : "Could not register on the project tree.");
      return false;
    } finally {
      setRegistering(false);
    }
  }, [api, flush, ensureBrief, advisor]);

  // ---- hypotheses ----
  useEffect(() => {
    const hid = brief?.hypothesis_session_id;
    if (!hid) { setHypSession(null); return; }
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
      setBrief(await api.getBrief(b.id));
    } catch (e) {
      setError(e instanceof HttpError ? e.message : "Generation failed. Try sharpening the research question.");
    } finally {
      setHypBusy(null);
    }
  };
  const selectHyp = async (hypId: string) => {
    if (!hypSession || !brief) return;
    try { setHypSession(await api.selectHypothesis(hypSession.id, hypId)); setBrief(await api.getBrief(brief.id)); } catch { setError("Could not record the selection."); }
  };
  const refineHyp = async (parentId: string, feedback: string) => {
    if (!hypSession) return;
    setHypBusy("Generating a new set from your pick…");
    try { setHypSession(await api.refineHypothesis(hypSession.id, parentId, feedback || undefined)); } catch { setError("Refinement failed. Try different feedback."); } finally { setHypBusy(null); }
  };

  // ---- review ----
  const loadPreview = useCallback(async () => {
    setPreview(null);
    const b = (await flush()) ?? briefRef.current;
    if (!b) return;
    try { setPreview(await api.previewBrief(b.id)); } catch (e) { setError(e instanceof HttpError ? e.message : "Could not render the preview."); }
  }, [api, flush]);
  useEffect(() => {
    if (step === 4) void loadPreview();
    if (step === 1) loadLibrary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  const launch = async () => {
    const b = briefRef.current;
    if (!b) return;
    setLaunching(true);
    setError(null);
    await launchBrief(b.id);
    setLaunching(false);
  };

  // ---- navigation (dynamic: leaving step 1 with Q1–Q3 answered registers the problem) ----
  const canLeaveDefine = canLaunchOf(fields);
  const registrable = registrableOf(fields);
  const goto = async (i: number) => {
    setError(null);
    if (i > 0 && !briefRef.current) await ensureBrief();
    await flush();
    if (i > 0 && registrable && (step === 0 || i === 2)) await register();
    setStep(i);
  };
  const jumpTo = (q: number) => {
    setStep(q === 4 ? 1 : 0);
    window.setTimeout(() => qRefs.current[q]?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
  };
  const resume = async (d: BriefSummary) => {
    await flush();
    cancelSave();
    setStep(0); setBrief(null); briefRef.current = null; setFields(EMPTY); setHypSession(null); setPreview(null); setSave("idle");
    setOnboardingBriefId(d.id);
  };
  const fresh = () => {
    cancelSave();
    setStep(0); setBrief(null); briefRef.current = null; setFields(EMPTY); setHypSession(null); setPreview(null); setSave("idle");
    setOnboardingBriefId(null);
  };
  const newBrief = async () => { await flush(); fresh(); void loadDrafts(); };
  const discard = async () => {
    cancelSave();
    const b = briefRef.current;
    fresh();
    if (b) { try { await api.deleteBrief(b.id); } catch { /* ignore */ } }
    void loadDrafts();
  };

  // ---- data step helpers ----
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const el = dirInputRef.current;
    if (el) { el.setAttribute("webkitdirectory", ""); el.setAttribute("directory", ""); }
  }, [step]);
  const [uploading, setUploading] = useState(false);
  const upload = async (fl: FileList | null) => {
    if (!fl || !fl.length) return;
    setUploading(true);
    try {
      const landed = await uploadFiles(fl);
      const names = Array.from(new Set(landed.map((n) => (n.includes("/") ? n.split("/")[0] : n))));
      const cur = fieldsRef.current.data;
      const add = names.filter((n) => !cur.some((d) => d.path === n)).map((n) => ({ path: n, description: "" }));
      if (add.length) edit({ data: [...cur, ...add] });
    } finally {
      setUploading(false);
    }
  };
  const attachable = useMemo(() => {
    const folders = new Map<string, { count: number; size: number }>();
    const rows: { path: string; size: number; kind: "file" | "folder" }[] = [];
    for (const f of library) {
      const parts = f.name.split("/");
      if (parts.length > 1) { const top = parts[0]; const cur = folders.get(top) ?? { count: 0, size: 0 }; folders.set(top, { count: cur.count + 1, size: cur.size + f.size }); }
      rows.push({ path: f.name, size: f.size, kind: "file" });
    }
    const out: { path: string; size: number; kind: "file" | "folder"; count?: number }[] = [];
    for (const [top, v] of folders) out.push({ path: top, size: v.size, kind: "folder", count: v.count });
    out.push(...rows.sort((a, b) => a.path.localeCompare(b.path)));
    return out;
  }, [library]);
  const attached = (p: string) => fields.data.some((d) => d.path === p);
  const toggleAttach = (p: string) => { if (attached(p)) edit({ data: fields.data.filter((d) => d.path !== p) }); else edit({ data: [...fields.data, { path: p, description: "" }] }); };
  const describe = (p: string, description: string) => edit({ data: fields.data.map((d) => (d.path === p ? { ...d, description } : d)) });

  const saveLabel = save === "saving" ? "saving…" : save === "saved" ? "saved ✓" : save === "dirty" ? "unsaved" : save === "error" ? "save failed" : "";
  const canLaunch = !launching && !!preview && preview.missing.length === 0 && preview.data_problems.length === 0 && !preview.too_large;
  const qdots = dots(fields);
  const answered = qdots.filter((d) => d === "full").length;
  const pendingImport = advisor.pending_imports.length > 0;
  const rec = advisor.latest;
  const roleNames = roles.map((r) => r.name);

  const stepHint = (): string => {
    switch (step) {
      case 0: return !canLeaveDefine ? "Title and research question unlock the next steps." : registrable ? (brief?.node_id ? `Registered on the project tree (${fields.visibility}). Continue when you are done editing.` : `Questions 1–3 answered — continuing registers this problem on the project tree (${fields.visibility}) and starts the advisor.`) : `${answered}/5 questions answered. Questions 1–3 are needed to register on the project tree; launch needs only the title and question.`;
      case 1: return `${fields.data.length} item${fields.data.length === 1 ? "" : "s"} attached. ${qdots[4] === "full" ? "Question 4 answered." : "Question 4 (task definition, existing results, access) is still open — optional."}`;
      case 2: return brief?.node_id ? (advisor.running ? "The advisor is running; you can continue and come back." : pendingImport ? "An import is awaiting your approval above." : "Everything on this step is optional.") : registrable ? "Registering…" : "Answer questions 1–3 to register; or continue without the tree.";
      case 3: return brief?.hypothesis ? "Working hypothesis set." : rec?.hypothesis_seed?.suggested ? "The advisor recommends running the search — still optional." : "Optional — you can launch without a hypothesis.";
      default: return pendingImport ? "An import approval is pending: decide it first so the session runs on the harness you intend." : "Launching starts the supervisor's first turn immediately.";
    }
  };

  // ============================================================ render
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      {/* header */}
      <div style={{ padding: "15px 24px 13px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "flex-start", gap: 14 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 9, flexWrap: "wrap" }}>
            New research session
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--accent-soft)", color: "var(--accent)" }}>onboarding</span>
            {brief && <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{brief.id} · {saveLabel}</span>}
            <AdvisorPill nodeId={brief?.node_id ?? null} state={advisor} />
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: "var(--mid)" }}>
            State the problem in the fixed five-question format, attach the data, see where it sits on the org's project tree, take the advisor's harness and tool recommendations, optionally search hypotheses, then launch. The supervisor receives the whole brief as its first turn.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flex: "0 0 auto" }}>
          {brief && <button onClick={() => void newBrief()} title="Keep this draft and start another" style={ghostBtn}>New brief</button>}
          {brief && <button onClick={() => void discard()} title="Delete this draft" style={ghostBtn}>Discard draft</button>}
          <button onClick={() => openProject(null)} title="The org-wide project tree" style={ghostBtn}>Projects</button>
          <button onClick={startQuickSession} title="Skip onboarding: start from a one-line task" style={ghostBtn}>Quick start instead</button>
        </div>
      </div>

      {/* step pills */}
      <div style={{ display: "flex", gap: 6, padding: "12px 24px 0", flexWrap: "wrap" }}>
        {STEPS.map((s, i) => {
          const on = i === step;
          const done = i < step;
          const locked = i > 0 && !canLeaveDefine;
          const optional = i === 2 || i === 3;
          return (
            <button key={s} onClick={() => void goto(i)} disabled={locked} style={{ display: "flex", alignItems: "center", gap: 7, padding: "6px 12px", borderRadius: 8, border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`, background: on ? "var(--accent-soft)" : "transparent", color: on ? "var(--accent)" : done ? "var(--hi)" : "var(--mid)", fontSize: 12.5, fontWeight: 600, opacity: locked ? 0.5 : 1 }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, width: 16, height: 16, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", background: done ? "var(--ok)" : on ? "var(--accent)" : "var(--bg3)", color: done || on ? "#06121c" : "var(--lo)" }}>{done ? "✓" : i + 1}</span>
              {s}
              {optional && <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--lo)", fontWeight: 500 }}>optional</span>}
              {i === 2 && pendingImport && <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--warn)", animation: "pulse 1.6s infinite" }} title="import approval pending" />}
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
          <div style={{ maxWidth: step === 2 ? 1180 : 900 }}>
            {step === 0 && drafts.filter((d) => d.id !== brief?.id).length > 0 && (
              <div style={{ marginBottom: 20, padding: "12px 14px", border: "1px solid var(--border)", borderRadius: 11, background: "var(--bg1)" }}>
                <div style={labelStyle}>{brief ? "Switch to another draft" : "Resume a draft"}</div>
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                  {drafts.filter((d) => d.id !== brief?.id).map((d) => (
                    <button key={d.id} onClick={() => void resume(d)} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderRadius: 8, textAlign: "left", background: "var(--bg2)" }}>
                      <span style={{ display: "flex", gap: 3 }}>{(d.completeness ?? []).map((q) => <span key={q.q} style={{ width: 6, height: 6, borderRadius: "50%", background: q.filled ? "var(--ok)" : "var(--bg3)" }} />)}</span>
                      <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: "var(--hi)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.title || d.research_question || "(untitled draft)"}</span>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{d.node_id ? "on tree · " : ""}{d.data_count} file{d.data_count === 1 ? "" : "s"}{d.hypothesis ? " · hypothesis ✓" : ""} · {d.updated ?? ""}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {step === 0 && (
              <div style={{ display: "flex", gap: 22, alignItems: "flex-start" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  {fields.parent_node && (
                    <div style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", background: "var(--lav)", color: "#0f0a1c", borderRadius: 9, fontSize: 12.5 }}>
                      <span style={{ fontWeight: 700 }}>Derives from</span>
                      <button onClick={() => openProject(fields.parent_node)} style={{ flex: 1, textAlign: "left", fontWeight: 600, textDecoration: "underline" }}>{parentTitle ?? fields.parent_node}</button>
                      <button onClick={() => edit({ parent_node: null })} title="Detach from the parent" style={{ fontSize: 12 }}>✕</button>
                    </div>
                  )}
                  <QBlock q={0} dot={qdots[0]} title={QLABEL[0]} hint="One line for the title; the one question this session must answer — specific enough that an answer could be wrong." blockRef={(el) => { qRefs.current[0] = el; }}>
                    <Field label="Title" required hint="Becomes the session's name and the node's label on the project tree.">
                      <input value={fields.title} placeholder="e.g. Coherence limits of transmon qubits under two-tone drive" onChange={(e) => edit({ title: e.target.value })} style={inputStyle} />
                    </Field>
                    <Field label="Research question" required>
                      <textarea value={fields.research_question} rows={2} placeholder="e.g. Does two-tone driving extend T2 beyond the single-tone dynamical-decoupling limit in fixed-frequency transmons?" onChange={(e) => edit({ research_question: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                    </Field>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                      <Field label="Domain" hint="Clusters the tree; helps the advisor and the hypothesis search.">
                        <input value={fields.domain} placeholder="e.g. quantum physics / regenerative medicine" onChange={(e) => edit({ domain: e.target.value })} style={inputStyle} />
                      </Field>
                      <Field label="Objectives" hint="Concrete aims, one per line.">
                        <ListField value={fields.objectives} onChange={(v) => edit({ objectives: v })} placeholder={"Fit T2 vs drive detuning\nCompare against the CPMG baseline"} rows={2} />
                      </Field>
                    </div>
                  </QBlock>
                  <QBlock q={1} dot={qdots[1]} title={QLABEL[1]} hint="What changes if this is answered — for the field, for the org, for what can be built next. Two to five sentences." blockRef={(el) => { qRefs.current[1] = el; }}>
                    <Field label="Significance">
                      <textarea value={fields.significance} rows={3} placeholder="Coherence time bounds gate fidelity; a drive-based extension would apply to existing hardware without fabrication changes…" onChange={(e) => edit({ significance: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                    </Field>
                  </QBlock>
                  <QBlock q={2} dot={qdots[2]} title={QLABEL[2]} hint="The state of the art from human work, then the gap that is genuinely unresolved (not just unpublished by us)." blockRef={(el) => { qRefs.current[2] = el; }}>
                    <Field label="What existing work has achieved">
                      <textarea value={fields.prior_work} rows={3} placeholder="Dynamical decoupling (CPMG, XY8) reaches T2 ≈ 2·T1 in transmons; two-tone protocols were demonstrated for NV centres only…" onChange={(e) => edit({ prior_work: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                    </Field>
                    <Field label="What remains genuinely open">
                      <textarea value={fields.open_gap} rows={2} placeholder="Whether the two-tone advantage survives transmon charge noise and the 1/f flux-noise spectrum is unknown." onChange={(e) => edit({ open_gap: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                    </Field>
                  </QBlock>
                  <QBlock q={3} dot={qdots[3]} title={QLABEL[3]} hint="The measurement and the comparison that decide progress. The agent may only report progress against this — name the baseline, the metric, the held-out data, the statistical test." blockRef={(el) => { qRefs.current[3] = el; }}>
                    <Field label="Evaluation protocol">
                      <textarea value={fields.evaluation_protocol} rows={3} placeholder="Ramsey and echo decay curves fitted with a fixed model; report T2 with bootstrap CIs; the baseline is the CPMG sequence run on the same qubit in the same cooldown." onChange={(e) => edit({ evaluation_protocol: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                    </Field>
                    <Field label="Success criteria" hint="What a convincing answer looks like — the definition of done.">
                      <textarea value={fields.success_criteria} rows={2} placeholder="T2 improvement > 20% with non-overlapping CIs on at least two qubits." onChange={(e) => edit({ success_criteria: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                    </Field>
                  </QBlock>
                  <div style={{ marginBottom: 18, padding: "12px 16px", border: "1px dashed var(--border)", borderLeft: `3px solid ${DOT_COLOR[qdots[4]]}`, borderRadius: "0 12px 12px 0", display: "flex", alignItems: "center", gap: 10 }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, fontWeight: 700, padding: "1px 7px", borderRadius: 5, background: "var(--bg3)", color: "var(--mid)" }}>Q4</span>
                    <span style={{ flex: 1, fontSize: 12.5, color: "var(--mid)" }}><b style={{ color: "var(--hi)", fontWeight: 600 }}>{QLABEL[4]}</b> — answered on the next step, together with the uploads.</span>
                    <button onClick={() => void goto(1)} disabled={!canLeaveDefine} style={{ fontSize: 12, color: "var(--accent)", fontWeight: 600, opacity: canLeaveDefine ? 1 : 0.5 }}>Data step →</button>
                  </div>

                  <div style={{ padding: "14px 16px", border: "1px solid var(--border)", borderRadius: 12, background: "var(--bg1)" }}>
                    <Field label="Keywords" hint="Short terms colleagues would search for. The advisor proposes more once the problem is registered.">
                      <KeywordField value={fields.keywords} onChange={(v) => edit({ keywords: v })} />
                    </Field>
                    <Field label="Constraints" hint="Scope limits, time, compute, ethics, things explicitly out of bounds (session-private, never shared).">
                      <textarea value={fields.constraints} rows={2} placeholder="e.g. analysis only, no new experiments; finish within one session; no external data downloads" onChange={(e) => edit({ constraints: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                    </Field>
                    <Field label="Deliverables" hint="Expected outputs, one per line, written under the session's results/ folder (session-private).">
                      <ListField value={fields.deliverables} onChange={(v) => edit({ deliverables: v })} placeholder={"Report (PDF)\nFit table (CSV)\nFigures"} rows={2} />
                    </Field>
                  </div>
                </div>

                {/* sticky guide */}
                <div style={{ width: 250, flex: "0 0 250px", position: "sticky", top: 0 }}>
                  <div style={{ padding: "13px 14px", border: "1px solid var(--border)", borderRadius: 12, background: "var(--bg1)" }}>
                    <div style={labelStyle}>Statement · {answered}/5</div>
                    <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 5 }}>
                      {QLABEL.map((l, q) => (
                        <button key={q} onClick={() => jumpTo(q)} style={{ display: "flex", gap: 8, alignItems: "flex-start", textAlign: "left", fontSize: 11.5, color: qdots[q] === "full" ? "var(--hi)" : "var(--mid)", lineHeight: 1.35 }}>
                          <span style={{ width: 8, height: 8, borderRadius: "50%", background: DOT_COLOR[qdots[q]], flex: "0 0 auto", marginTop: 4 }} />
                          <span><span style={{ fontFamily: "var(--mono)", color: "var(--lo)" }}>Q{q}</span> {l.length > 44 ? l.slice(0, 43) + "…" : l}</span>
                        </button>
                      ))}
                    </div>
                    <div style={{ marginTop: 12, fontSize: 11.5, color: "var(--mid)", lineHeight: 1.45 }}>
                      {!canLeaveDefine ? "Title + question unlock data, hypotheses and launch." : registrable ? "Q1–Q3 answered: this problem goes on the project tree and the advisor reads it." : "Q1–Q3 put this problem on the project tree, where the advisor can recommend a colleague's harness and tools."}
                    </div>
                  </div>
                  <div style={{ marginTop: 10, padding: "13px 14px", border: `1px solid ${fields.visibility === "org" ? "var(--accent-dim)" : "var(--border)"}`, borderRadius: 12, background: fields.visibility === "org" ? "var(--accent-soft)" : "var(--bg1)" }}>
                    <label style={{ display: "flex", alignItems: "center", gap: 9, cursor: "pointer" }}>
                      <input type="checkbox" checked={fields.visibility === "org"} onChange={(e) => edit({ visibility: e.target.checked ? "org" : "private" })} />
                      <span style={{ fontSize: 13, fontWeight: 600, color: "var(--hi)" }}>Share on the project tree</span>
                    </label>
                    <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--mid)", lineHeight: 1.45 }}>
                      {fields.visibility === "org"
                        ? "Colleagues see: the five answers, keywords, your name, status, session ids with outcome one-liners, harness version summaries and tool names. Never your files, events, memory, hypotheses or approvals."
                        : "Private: only you see this node. You still get advisor recommendations; nobody can build on your problem or import your harness."}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {step === 1 && (
              <div>
                <div style={{ fontSize: 15, fontWeight: 600, color: "var(--hi)" }}>Upload and attach the data</div>
                <div style={{ ...hintStyle, marginBottom: 14 }}>
                  Files go to your persistent library (shared across sessions, downloadable at any time from here or the session's Files panel) and are attached to this brief with a short description so the agent knows what each one is. Any file type; folders keep their structure. Very large files (&gt;5 GB) can be copied straight into <span style={{ fontFamily: "var(--mono)" }}>state/library/</span> on the host and attached here.
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
                        {!library.some((f) => f.name === d.path) ? null : <button onClick={() => downloadLibrary(d.path)} title="Download" style={{ fontSize: 11, color: "var(--accent)", fontFamily: "var(--mono)", padding: "3px 8px", borderRadius: 6, border: "1px solid var(--border)" }}>get</button>}
                        <button onClick={() => toggleAttach(d.path)} title="Detach" style={{ color: "var(--lo)", fontSize: 12, padding: "3px 6px" }}>✕</button>
                      </div>
                    ))}
                  </div>
                </Field>

                <Field label="Library" hint="Everything you have uploaded so far, across all sessions. Files are downloadable at any time.">
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
                          {r.kind === "file" && <button onClick={(e) => { e.preventDefault(); downloadLibrary(r.path); }} title="Download" style={{ fontSize: 11, color: "var(--accent)", fontFamily: "var(--mono)", padding: "2px 7px", borderRadius: 6, border: "1px solid var(--border)" }}>get</button>}
                        </label>
                      );
                    })}
                  </div>
                </Field>

                <div ref={(el) => { qRefs.current[4] = el; }} style={{ marginTop: 6, padding: "14px 16px 4px", border: "1px solid var(--border)", borderLeft: `3px solid ${DOT_COLOR[qdots[4]]}`, borderRadius: "0 12px 12px 0", background: "var(--bg1)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 4 }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, fontWeight: 700, padding: "1px 7px", borderRadius: 5, background: "var(--bg3)", color: "var(--mid)" }}>Q4</span>
                    <span style={{ fontSize: 14, fontWeight: 600, color: "var(--hi)" }}>{QLABEL[4]}</span>
                  </div>
                  <div style={{ ...hintStyle, marginBottom: 12 }}>Exactly what the agent will compute on. The evaluation protocol itself is Q3; here: the task on this data, what is already known about it, and who may use it.</div>
                  <Field label="Task definition" hint="Inputs → outputs on the attached files, with column/field names.">
                    <textarea value={fields.task_definition} rows={2} placeholder="Fit decay curves in decay.csv; each row is (delay_us, p_excited, qubit_id, sequence); output T2 per (qubit_id, sequence)." onChange={(e) => edit({ task_definition: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                  </Field>
                  <Field label="Existing results / baselines" hint="Numbers already in hand that the agent must reproduce or beat.">
                    <textarea value={fields.existing_results} rows={2} placeholder="CPMG baseline T2 = 41 µs on Q1 (same cooldown)." onChange={(e) => edit({ existing_results: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                  </Field>
                  <Field label="Data access and permissions" hint="Licence, embargo, consent, what may leave the machine.">
                    <textarea value={fields.data_access} rows={2} placeholder="Internal lab data; analysis only; do not upload to external services." onChange={(e) => edit({ data_access: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                  </Field>
                  <Field label="Data notes" hint="Provenance, formats, known quirks, what to ignore.">
                    <textarea value={fields.data_notes} rows={2} placeholder="e.g. columns after 'blank' are controls; run 3 was aborted." onChange={(e) => edit({ data_notes: e.target.value })} style={{ ...inputStyle, resize: "vertical" }} />
                  </Field>
                </div>
              </div>
            )}

            {step === 2 && (
              <div>
                {!brief?.node_id ? (
                  registering ? (
                    <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: "var(--mid)" }}>
                      <span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--accent)", animation: "pulse 1.4s infinite" }} /> Registering on the project tree…
                    </div>
                  ) : registrable ? (
                    <div style={{ padding: "14px 16px", border: "1px solid var(--border)", borderRadius: 12, background: "var(--bg1)", display: "flex", alignItems: "center", gap: 12 }}>
                      <span style={{ flex: 1, fontSize: 13, color: "var(--mid)" }}>Questions 1–3 are answered. Register this problem on the project tree ({fields.visibility}) to see its neighbours and get recommendations.</span>
                      <button onClick={() => void register(true)} style={primaryBtn(true)}>Register →</button>
                    </div>
                  ) : (
                    <div style={{ padding: "14px 16px", border: "1px solid var(--border)", borderRadius: 12, background: "var(--bg1)" }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--hi)" }}>Not on the project tree yet</div>
                      <div style={{ ...hintStyle, marginBottom: 10 }}>The tree and the advisor read the complete statement. Answer these to register (launching does not require it):</div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                        {[1, 2, 3].filter((q) => qdots[q] !== "full").map((q) => (
                          <button key={q} onClick={() => jumpTo(q)} style={{ display: "flex", gap: 8, alignItems: "center", textAlign: "left", fontSize: 12.5, color: "var(--accent)", fontWeight: 600 }}>
                            <span style={{ width: 8, height: 8, borderRadius: "50%", background: DOT_COLOR[qdots[q]] }} /> Q{q} · {QLABEL[q]} →
                          </button>
                        ))}
                      </div>
                    </div>
                  )
                ) : (
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
                      <span style={{ fontSize: 15, fontWeight: 600, color: "var(--hi)" }}>Where this problem sits</span>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{brief.node_id} · {fields.visibility === "org" ? "shared with the org" : "private"}</span>
                      <span style={{ flex: 1 }} />
                      <button onClick={() => openProject(brief.node_id)} style={ghostBtn}>Open on the Projects page →</button>
                    </div>
                    <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
                      <div style={{ flex: "0 0 440px", minWidth: 0 }}>
                        <NearTree nodeId={brief.node_id} refreshKey={nearKey + (rec?.rec_id ? 1 : 0)} onOpenFull={(pid) => openProject(pid)} />
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <AdvisorCards
                          nodeId={brief.node_id}
                          brief={brief}
                          roles={roleNames}
                          state={advisor}
                          onJumpToQuestion={jumpTo}
                          onAddKeywords={(kws) => edit({ keywords: [...fields.keywords, ...kws.filter((k) => !fields.keywords.some((x) => x.toLowerCase() === k.toLowerCase()))] })}
                          onOpenNode={(pid) => openProject(pid)}
                          onGoHypotheses={() => void goto(3)}
                        />
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {step === 3 && (
              <div>
                <div style={{ fontSize: 15, fontWeight: 600, color: "var(--hi)", display: "flex", alignItems: "center", gap: 9 }}>
                  Hypothesis search
                  <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--bg2)", color: "var(--lo)" }} title="Interim generator; the Google co-scientist protocol is planned (docs/plans/H1)">interim</span>
                </div>
                <div style={{ ...hintStyle, marginBottom: 14 }}>
                  Seeded from the whole statement (significance, prior work, open gap, evaluation protocol, data), the AI proposes a few <b style={{ color: "var(--hi)", fontWeight: 600 }}>parallel</b> hypotheses. Select one to make it the session's working hypothesis, or pick one and say what to do differently. You can also continue without one.
                </div>
                {rec?.hypothesis_seed && (
                  <div style={{ marginBottom: 12, padding: "9px 13px", background: rec.hypothesis_seed.suggested ? "var(--accent-soft)" : "var(--bg2)", border: `1px solid ${rec.hypothesis_seed.suggested ? "var(--accent-dim)" : "var(--border)"}`, borderRadius: 9, fontSize: 12.5, color: "var(--mid)" }}>
                    <b style={{ color: "var(--hi)", fontWeight: 600 }}>Advisor: {rec.hypothesis_seed.suggested ? "worth running." : "optional here."}</b> {rec.hypothesis_seed.why}
                  </div>
                )}
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
                  {hypSession && <button onClick={() => { setHyp(hypSession); setMainView("hypothesis"); }} style={ghostBtn} title="Open this search on the Hypotheses page">Open on the Hypotheses page →</button>}
                  {hypBusy && (
                    <span style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--mid)", fontSize: 12.5 }}>
                      <span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--accent)", animation: "pulse 1.4s infinite" }} />
                      {hypBusy} <span style={{ color: "var(--lo)" }}>(up to a minute)</span>
                    </span>
                  )}
                </div>
                {hypSession && <HypothesisCards session={hypSession} loading={hypBusy} onSelect={(id) => void selectHyp(id)} onRefine={(p, f) => void refineHyp(p, f)} />}
              </div>
            )}

            {step === 4 && (
              <div>
                <div style={{ fontSize: 15, fontWeight: 600, color: "var(--hi)" }}>Review what the agent will receive</div>
                <div style={{ ...hintStyle, marginBottom: 14 }}>
                  This is the exact first message the research supervisor gets — including the harness it runs on and the project-tree context. Fix anything that reads wrong by going back a step; the text is rendered by the server from your brief, not editable here.
                </div>
                {preview?.missing.length ? (
                  <div style={{ marginBottom: 10, padding: "9px 13px", background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 9, fontSize: 12.5, color: "var(--hi)" }}>
                    Required before launch: <b>{preview.missing.join(", ").replace(/_/g, " ")}</b>. <button onClick={() => jumpTo(0)} style={{ color: "var(--accent)", fontWeight: 600 }}>Fix →</button>
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
                {preview && preview.pending_imports.length > 0 && (
                  <div style={{ marginBottom: 10, padding: "9px 13px", background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 9, fontSize: 12.5, color: "var(--hi)", display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ flex: 1 }}><b>An import approval is pending.</b> Decide it first so the session runs on the harness you intend; launching now uses your current version.</span>
                    <button onClick={() => void goto(2)} style={{ color: "var(--accent)", fontWeight: 600 }}>Review →</button>
                  </div>
                )}
                {preview && preview.completeness.some((q) => !q.filled) && preview.missing.length === 0 && (
                  <div style={{ marginBottom: 10, padding: "9px 13px", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 9, fontSize: 12.5, color: "var(--mid)" }}>
                    <span style={{ color: "var(--hi)", fontWeight: 600 }}>Unanswered (optional):</span>{" "}
                    {preview.completeness.filter((q) => !q.filled).map((q) => (
                      <button key={q.q} onClick={() => jumpTo(q.q)} style={{ color: "var(--accent)", fontWeight: 600, marginRight: 10 }}>Q{q.q} {q.missing.join(", ").replace(/_/g, " ")} →</button>
                    ))}
                    {preview.missing_for_registration.length > 0 && <span style={{ color: "var(--lo)" }}> — not on the project tree until Q1–Q3 are answered.</span>}
                  </div>
                )}
                {!brief?.hypothesis && preview && (
                  <div style={{ marginBottom: 10, padding: "9px 13px", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 9, fontSize: 12.5, color: "var(--mid)" }}>
                    No working hypothesis selected — the agent will propose its own early and ask you to choose. <button onClick={() => void goto(3)} style={{ color: "var(--accent)", fontWeight: 600 }}>Run the hypothesis search →</button>
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
        <span style={{ flex: 1, fontSize: 11.5, color: "var(--lo)" }}>{stepHint()}</span>
        {user && <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{user.display_name}</span>}
        {step < 4 ? (
          <button onClick={() => void goto(step + 1)} disabled={!canLeaveDefine || registering} style={primaryBtn(canLeaveDefine && !registering)}>
            {registering ? "Registering…" : step === 0 && registrable && !brief?.node_id ? "Continue — register on the tree →" : step === 3 && !brief?.hypothesis ? "Continue without a hypothesis →" : step === 2 && !brief?.node_id ? "Continue without the tree →" : "Continue →"}
          </button>
        ) : (
          <button onClick={() => void launch()} disabled={!canLaunch} style={{ ...primaryBtn(canLaunch), background: canLaunch ? "var(--ok)" : "var(--bg2)" }}>
            {launching ? "Launching…" : "Launch research session"}
          </button>
        )}
      </div>
    </div>
  );
}

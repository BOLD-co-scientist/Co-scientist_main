import { useApp } from "../state/store";

export default function TopBar() {
  const { status, user, theme, toggleTheme, logout } = useApp();
  const dot = status?.color ?? "var(--accent)";
  return (
    <div
      style={{
        height: 52,
        flex: "0 0 52px",
        display: "flex",
        alignItems: "center",
        padding: "0 16px",
        background: "var(--bg1)",
        borderBottom: "1px solid var(--border)",
        gap: 14,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <div style={{ width: 19, height: 19, borderRadius: 5, background: "var(--accent)", boxShadow: "0 0 14px var(--accent-dim)" }} />
        <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-.01em" }}>coscientist</div>
      </div>
      <div style={{ width: 1, height: 20, background: "var(--border)" }} />
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", flex: "0 0 auto", background: dot, boxShadow: `0 0 8px ${dot}` }} />
        <span style={{ fontSize: 12.5, color: "var(--mid)", fontWeight: 500, whiteSpace: "nowrap" }}>
          {status?.text ?? "Ready"}
        </span>
      </div>
      <div style={{ flex: 1 }} />
      <button
        onClick={toggleTheme}
        title="Toggle theme"
        style={{ width: 32, height: 32, borderRadius: 8, border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--mid)", fontSize: 14 }}
      >
        {theme === "dark" ? "\u25D0" : "\u25D1"}
      </button>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 6px 5px 11px", border: "1px solid var(--border)", borderRadius: 8 }}>
        <span style={{ fontSize: 12.5, color: "var(--mid)" }}>{user?.display_name ?? "\u2014"}</span>
        <button onClick={logout} style={{ fontSize: 11, color: "var(--lo)", fontFamily: "var(--mono)", padding: "2px 7px", borderRadius: 5, background: "var(--bg2)" }}>
          exit
        </button>
      </div>
    </div>
  );
}

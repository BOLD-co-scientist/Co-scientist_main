import { useState } from "react";
import { useApp } from "../state/store";

export default function LoginGate() {
  const { login, loggingIn, authError, mock } = useApp();
  const [key, setKey] = useState("");

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "radial-gradient(120% 90% at 50% -10%, var(--bg2), var(--bg0) 60%)",
      }}
    >
      <div style={{ width: 380, padding: "40px 36px 34px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 30 }}>
          <div style={{ width: 26, height: 26, borderRadius: 7, background: "var(--accent)", boxShadow: "0 0 22px var(--accent-dim)" }} />
          <div style={{ fontSize: 19, fontWeight: 600, letterSpacing: "-.01em" }}>BOLD Co-scientist</div>
        </div>
        <div style={{ fontSize: 13, color: "var(--mid)", marginBottom: 22 }}>
          A workbench for AI&#8209;assisted science. Enter your API key to continue.
        </div>
        <label style={{ fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".08em" }}>
          API key
        </label>
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") login(key);
          }}
          placeholder="csk_u_&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;"
          style={{
            width: "100%",
            marginTop: 7,
            padding: "11px 13px",
            background: "var(--bg1)",
            border: "1px solid var(--border)",
            borderRadius: 9,
            color: "var(--hi)",
            fontFamily: "var(--mono)",
            fontSize: 13,
            outline: "none",
          }}
        />
        {authError && (
          <div style={{ marginTop: 10, fontSize: 12, color: "var(--err)", display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontFamily: "var(--mono)" }}>401</span> {authError}
          </div>
        )}
        <button
          onClick={() => login(key)}
          disabled={loggingIn}
          style={{
            width: "100%",
            marginTop: 16,
            padding: 11,
            background: "var(--accent)",
            color: "#06121c",
            fontWeight: 600,
            borderRadius: 9,
            fontSize: 14,
            opacity: loggingIn ? 0.7 : 1,
          }}
        >
          {loggingIn ? "Validating\u2026" : "Log in"}
        </button>
        <div style={{ marginTop: 16, fontSize: 11, color: "var(--lo)", fontFamily: "var(--mono)" }}>
          {mock ? "mock mode \u00b7 any csk_ key works" : "GET /auth/me \u00b7 key persists locally"}
        </div>
      </div>
    </div>
  );
}

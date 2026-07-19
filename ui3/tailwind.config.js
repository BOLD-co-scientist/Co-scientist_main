/** @type {import('tailwindcss').Config} */
// Semantic tokens are defined as CSS variables in src/index.css (the source of
// truth for the dark/light themes). They are mapped here so Tailwind utilities
// like `bg-bg1`, `text-mid`, `border-line` resolve to the live theme variable.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg0: "var(--bg0)",
        bg1: "var(--bg1)",
        bg2: "var(--bg2)",
        bg3: "var(--bg3)",
        line: "var(--border)",
        "line-hi": "var(--border-hi)",
        hi: "var(--hi)",
        mid: "var(--mid)",
        lo: "var(--lo)",
        accent: "var(--accent)",
        "accent-dim": "var(--accent-dim)",
        "accent-soft": "var(--accent-soft)",
        ok: "var(--ok)",
        "ok-soft": "var(--ok-soft)",
        warn: "var(--warn)",
        "warn-soft": "var(--warn-soft)",
        err: "var(--err)",
        "err-soft": "var(--err-soft)",
        evo: "var(--evo)",
        "evo-soft": "var(--evo-soft)",
        cyan: "var(--cyan)",
        lav: "var(--lav)",
        grn: "var(--grn)",
      },
      fontFamily: {
        ui: "var(--ui)",
        mono: "var(--mono)",
      },
    },
  },
  plugins: [],
};

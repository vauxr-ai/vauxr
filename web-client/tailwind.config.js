/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        zinc: Object.fromEntries(
          [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((n) => [
            n,
            `rgb(var(--neutral-${n}) / <alpha-value>)`,
          ]),
        ),
        indigo: Object.fromEntries(
          [100, 200, 300, 400, 500, 600, 700, 800, 900, 950].map((n) => [
            n,
            `rgb(var(--accent-${n}) / <alpha-value>)`,
          ]),
        ),
        bg: "var(--canvas)",
        surface: "var(--panel)",
        "surface-2": "var(--panel-raised)",
        "surface-3": "#22222a",
        "border-subtle": "rgba(255,255,255,0.06)",
        "border-soft": "rgba(255,255,255,0.10)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        "inner-border": "inset 0 0 0 1px rgba(255,255,255,0.05)",
        "indigo-glow": "0 0 30px 5px rgba(99, 102, 241, 0.18)",
        "violet-glow": "0 0 30px 5px rgba(124, 58, 237, 0.20)",
        "red-glow": "0 0 30px 5px rgba(239, 68, 68, 0.20)",
        "teal-glow": "0 0 30px 5px rgba(20, 184, 166, 0.20)",
      },
    },
  },
  plugins: [],
};

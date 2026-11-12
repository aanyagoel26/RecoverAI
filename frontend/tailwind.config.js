/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ['"Manrope Variable"', "system-ui", "sans-serif"], mono: ['"JetBrains Mono"', "ui-monospace", "monospace"] },
      colors: {
        ink: {950: "rgb(var(--ink-950) / <alpha-value>)", 900: "rgb(var(--ink-900) / <alpha-value>)", 850: "rgb(var(--ink-850) / <alpha-value>)", 800: "rgb(var(--ink-800) / <alpha-value>)", 700: "rgb(var(--ink-700) / <alpha-value>)", 600: "rgb(var(--ink-600) / <alpha-value>)", 500: "rgb(var(--ink-500) / <alpha-value>)", 400: "rgb(var(--ink-400) / <alpha-value>)", 300: "rgb(var(--ink-300) / <alpha-value>)", 200: "rgb(var(--ink-200) / <alpha-value>)", 100: "rgb(var(--ink-100) / <alpha-value>)"},
        volt: { 300: "#8FB8FF", 400: "#5C97FF", 500: "#3B7BFF", 600: "#2A62E0" },
        mint: { 300: "#6EE7B7", 400: "#34D399", 500: "#10B981", 600: "#059669" },
        amberx: { 400: "#FBBF24", 500: "#F59E0B" },
        rose: { 400: "#FB7185", 500: "#F43F5E" },
      },
      opacity: { 8: "0.08", 12: "0.12" },
      boxShadow: { card: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 8px 24px -12px rgba(0,0,0,0.5)" },
    },
  },
  plugins: [],
};

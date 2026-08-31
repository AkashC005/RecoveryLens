/** @type {import('tailwindcss').Config} */

/**
 * RecoveryLens — design tokens
 *
 * A light clinical surface. The previous dark theme read as a developer tool;
 * this one is built to look like software a hospital would buy, which is a
 * different and harder target than looking impressive.
 *
 * THE ONE RULE THAT SURVIVED THE REDESIGN
 * ---------------------------------------
 * Colour is signal, not decoration. `warn` and `danger` appear ONLY where there
 * is genuine clinical meaning — a raised tier, an escalation, a blocked send.
 * They never appear on a button, a hover, a chart accent or a heading. Use them
 * decoratively once and a red risk tier stops meaning anything, which is the
 * failure mode that matters more than any amount of visual polish.
 *
 * Everything else — depth, typography, motion, the risk visualisation — is
 * where the design is allowed to be ambitious, because none of it can be
 * misread as clinical information.
 *
 * Token names are semantic, not descriptive: `surface`, not `grey-50`. A
 * component asking for `bg-surface` keeps working when the value changes; one
 * asking for `bg-grey-50` has to be found and edited.
 */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // ---------------------------------------------------------- surfaces
        canvas: "#FFFFFF",
        // Page background behind cards. Very slightly cool so white cards lift
        // off it without needing a shadow.
        wash: "#F6F8FB",
        surface: "#FFFFFF",
        // A panel that should recede — collapsed sections, secondary rows.
        sunken: "#F1F4F8",
        // Hairlines. Two weights: `line` for structure, `line-soft` inside a
        // card where a full-strength border would fight the content.
        line: "#E3E8EF",
        "line-soft": "#EEF1F6",

        // ------------------------------------------------------------- text
        ink: "#0B1524",
        "ink-soft": "#33455C",
        muted: "#64748B",
        faint: "#94A3B8",

        // ----------------------------------------------------------- accent
        // Deep teal. Dark enough to pass AA on white as text, which matters
        // because it is used for links and controls, not just fills.
        accent: "#0B7A6E",
        "accent-strong": "#075E55",
        "accent-soft": "#D3EDE9",
        "accent-wash": "#F0FAF8",

        // ---------------------------------------------- clinical signal only
        warn: "#B45309",
        "warn-soft": "#FDF3E4",
        danger: "#B42318",
        "danger-soft": "#FDF0EE",
        // Reserved for the lowest tier and confirmed-good states.
        calm: "#1E7A4B",
        "calm-soft": "#E9F6EF",
      },
      fontFamily: {
        sans: ["Inter", "Noto Sans Tamil", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
      fontSize: {
        // Fixed scale — set once, never improvised per component. Wider at the
        // top than the old one: the previous scale stopped at 40px, which left
        // no size that could carry a page on its own.
        "2xs": ["11px", { lineHeight: "1.45", letterSpacing: "0.02em" }],
        xs: ["12px", { lineHeight: "1.5" }],
        sm: ["14px", { lineHeight: "1.55" }],
        base: ["16px", { lineHeight: "1.65" }],
        lg: ["19px", { lineHeight: "1.5" }],
        xl: ["24px", { lineHeight: "1.3", letterSpacing: "-0.01em" }],
        "2xl": ["32px", { lineHeight: "1.2", letterSpacing: "-0.02em" }],
        "3xl": ["44px", { lineHeight: "1.1", letterSpacing: "-0.025em" }],
        display: ["60px", { lineHeight: "1.03", letterSpacing: "-0.035em" }],
      },
      boxShadow: {
        // Shadows are soft and low-contrast on purpose. A clinical surface
        // should feel like paper on a desk, not like floating glass.
        card: "0 1px 2px rgba(11,21,36,0.04), 0 1px 3px rgba(11,21,36,0.03)",
        lift: "0 4px 12px rgba(11,21,36,0.06), 0 1px 3px rgba(11,21,36,0.04)",
        pop: "0 12px 32px rgba(11,21,36,0.10), 0 2px 8px rgba(11,21,36,0.05)",
      },
      maxWidth: {
        prose: "68ch",
      },
      keyframes: {
        rise: {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        grow: {
          from: { transform: "scaleX(0)" },
          to: { transform: "scaleX(1)" },
        },
      },
      animation: {
        rise: "rise 420ms cubic-bezier(0.22, 1, 0.36, 1) both",
        grow: "grow 700ms cubic-bezier(0.22, 1, 0.36, 1) both",
      },
    },
  },
  plugins: [],
};

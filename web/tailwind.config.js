/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // Colour is signal, not decoration. `amber` and `signal` appear ONLY
        // where there is genuine clinical meaning — never on buttons, hovers
        // or chart accents. Use them decoratively once and they stop meaning
        // anything.
        ink:    "#0F1922",
        slate:  "#1C2A35",
        raised: "#243542",
        bone:   "#E8EDF0",
        muted:  "#8CA0AE",
        teal:   "#2B9C8F",
        "teal-dim": "#1F7268",
        amber:  "#D68A2E",
        signal: "#C4483C",
      },
      fontFamily: {
        sans: ["Inter", "Noto Sans Tamil", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
      fontSize: {
        // Fixed scale — set once, never improvised per component.
        xs:   ["12px", { lineHeight: "1.5" }],
        sm:   ["14px", { lineHeight: "1.55" }],
        base: ["16px", { lineHeight: "1.6" }],
        lg:   ["20px", { lineHeight: "1.45" }],
        xl:   ["28px", { lineHeight: "1.25" }],
        "2xl":["40px", { lineHeight: "1.15" }],
      },
      maxWidth: {
        prose: "68ch",
      },
    },
  },
  plugins: [],
};

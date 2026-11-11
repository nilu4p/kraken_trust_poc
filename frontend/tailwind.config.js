/** @type {import('tailwindcss').Config} */
// Design tokens: dark tactical palette, one accent (teal "cyan"), status colours,
// IBM Plex Sans for the interface and IBM Plex Mono for figures and identifiers.
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        tactical: {
          bg: "#070b10",
          panel: "#0d141c",
          line: "#1c2a38",
          cyan: "#2dd4bf",
          red: "#ff3b3b",
          amber: "#f5c542",
          green: "#3dff8a",
        },
      },
      fontFamily: {
        sans: ["IBM Plex Sans", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};

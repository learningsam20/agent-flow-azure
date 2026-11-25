/** @type {import('tailwindcss').Config} */

/** Allow sand/20-style opacity on CSS-variable colors. */
function withAlpha(cssVar) {
  return ({ opacityValue }) =>
    opacityValue === undefined
      ? `var(${cssVar})`
      : `color-mix(in srgb, var(${cssVar}) ${Number(opacityValue) * 100}%, transparent)`;
}

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: withAlpha("--ink"),
        sea: withAlpha("--sea"),
        mist: withAlpha("--mist"),
        sand: withAlpha("--sand"),
        ember: withAlpha("--ember"),
        moss: withAlpha("--moss"),
      },
      fontFamily: {
        display: ['"Fraunces"', "Georgia", "serif"],
        sans: ['"Source Sans 3"', "Segoe UI", "sans-serif"],
      },
    },
  },
  plugins: [],
};

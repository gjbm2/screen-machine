// Writes src/palette.css: Tailwind's colours as CSS variables, one set per use (background, text, border), so dark
// mode can turn the app's light pages dark without touching their classes (owner, 23 Oct: "make sure our various
// screens in this app all respond properly to dark mode"). tailwind.config.ts points each utility at its set:
//   bg-*  (and gradients, ring offsets)   --bg-<family>-<shade>   light tints (50-400) go dark, solids stay
//   text-* (and fill, stroke, caret ...)  --fg-<family>-<shade>   dark text (500-950) goes light
//   border-* (and ring, divide, outline)  --bd-<family>-<shade>   light lines (50-400) go dark
//   bg-white                              --bg-white              a card: the dark surface (text-white stays white)
// :root holds Tailwind's own values (light mode exactly as before); while the device prefers dark (the media query
// itself, so a change of the setting turns the page at once) the dark ones, except under html.light-only (the TVs'
// /display pages, src/darkMode.ts); a .light-island inside is light again.
//
//   node scripts/gen_palette.mjs        (again after a Tailwind upgrade)

import { createRequire } from "node:module";
import { writeFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const colors = require("tailwindcss/colors");

const NEUTRALS = ["slate", "gray", "zinc", "neutral", "stone"];
const ACCENTS = ["red", "orange", "amber", "yellow", "lime", "green", "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose"];
const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];

const rgb = (hex) => {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
};
const mix = (a, b, t) => a.map((x, i) => Math.round(x + (b[i] - x) * t));
const css = (c) => c.join(" ");

// dark mode, per use: which light shade each shade shows as (a number), or a colour of its own (rgb array)
function darkOf(family, role, shade) {
  const p = colors[family];
  const at = (s) => rgb(p[s]);
  const neutral = NEUTRALS.includes(family);
  if (role === "bg") {
    if (neutral) {
      const m = { 50: mix(at(900), at(800), 0.45), 100: at(800), 200: mix(at(800), at(700), 0.55), 300: at(700), 400: at(600) };
      return m[shade] ?? at(shade);
    }
    const m = { 50: mix(at(950), at(900), 0.35), 100: at(900), 200: at(800), 300: at(700), 400: at(600) };
    return m[shade] ?? at(shade);
  }
  if (role === "bd") {
    if (neutral) {
      const m = { 50: at(800), 100: mix(at(800), at(700), 0.3), 200: mix(at(800), at(700), 0.6), 300: at(700), 400: at(600) };
      return m[shade] ?? at(shade);
    }
    const m = { 50: at(900), 100: at(900), 200: at(800), 300: at(700), 400: at(600) };
    return m[shade] ?? at(shade);
  }
  // text
  if (neutral) {
    const m = { 500: at(400), 600: at(300), 700: at(200), 800: at(100), 900: at(50), 950: [255, 255, 255] };
    return m[shade] ?? at(shade);
  }
  const m = { 600: at(400), 700: at(300), 800: at(200), 900: at(100), 950: at(50) };
  return m[shade] ?? at(shade);
}

const ROLES = ["bg", "fg", "bd"];
const light = [];
const dark = [];
for (const f of [...NEUTRALS, ...ACCENTS]) {
  for (const s of SHADES) {
    for (const r of ROLES) {
      light.push(`  --${r}-${f}-${s}: ${css(rgb(colors[f][s]))};`);
      dark.push(`  --${r}-${f}-${s}: ${css(darkOf(f, r, s))};`);
    }
  }
}
// the surfaces: a white card is the dark card; the page behind is darker still (index.css, the Room theme)
light.push("  --bg-white: 255 255 255;");
dark.push(`  --bg-white: ${css(mix(rgb(colors.stone[900]), rgb(colors.stone[950]), 0.15))};`);

const out = `/* Written by scripts/gen_palette.mjs: don't edit by hand. */
:root {
${light.join("\n")}
}
@media (prefers-color-scheme: dark) {
  html:not(.light-only) {
    color-scheme: dark;
${dark.map((l) => "  " + l).join("\n")}
  }
  html:not(.light-only) .light-island {
    color-scheme: light;
${light.map((l) => "  " + l).join("\n")}
  }
}
`;
writeFileSync(new URL("../src/palette.css", import.meta.url), out);
console.log(`src/palette.css: ${light.length} variables`);

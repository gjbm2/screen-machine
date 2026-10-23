// The Room page's light, warm theme.
//
// The page overrides the shadcn colour variables on its own root element (and on
// each portal it opens: sheets, dialogs, select menus), so the shared components
// pick up the theme without touching src/index.css or <html>. The <style> element
// is part of the page, so it goes away when the person leaves /room.

/** Put on the page root and on every portal's content element. */
export const THEME_CLASS = "room-theme";

/** A white card with a gentle, warm shadow. */
export const CARD_CLASS =
  "rounded-2xl border border-stone-200/80 bg-card text-card-foreground shadow-[0_1px_2px_rgba(68,48,28,0.05),0_6px_20px_-6px_rgba(68,48,28,0.10)]";

/** Big, friendly tap target for primary and secondary buttons (≥ 44 px). */
export const TAP_BUTTON_CLASS = "h-11 rounded-xl px-5 text-base";

const LIGHT = `
  --background: 40 33% 97%;
  --foreground: 24 14% 15%;
  --card: 0 0% 100%;
  --card-foreground: 24 14% 15%;
  --popover: 0 0% 100%;
  --popover-foreground: 24 14% 15%;
  --primary: 175 70% 27%;
  --primary-foreground: 0 0% 100%;
  --secondary: 36 24% 93%;
  --secondary-foreground: 24 14% 15%;
  --muted: 36 24% 94%;
  --muted-foreground: 25 8% 38%;
  --accent: 170 30% 94%;
  --accent-foreground: 176 60% 18%;
  --destructive: 350 62% 42%;
  --destructive-foreground: 0 0% 100%;
  --border: 32 20% 88%;
  --input: 30 14% 80%;
  --ring: 175 60% 32%;
  --radius: 0.875rem;
  color-scheme: light;`;

// dark mode (the device's setting, as a media query, so it turns at once; src/darkMode.ts: owner, 23 Oct "make sure our various screens in this app all respond
// properly to dark mode"): the same warm skin at night; a .light-island inside (the score's paper) stays light
const DARK = `
  --background: 24 10% 6%;
  --foreground: 30 12% 90%;
  --card: 24 9% 10%;
  --card-foreground: 30 12% 90%;
  --popover: 24 9% 11%;
  --popover-foreground: 30 12% 90%;
  --primary: 174 62% 36%;
  --primary-foreground: 0 0% 100%;
  --secondary: 26 8% 16%;
  --secondary-foreground: 30 12% 90%;
  --muted: 26 8% 15%;
  --muted-foreground: 30 7% 64%;
  --accent: 172 30% 15%;
  --accent-foreground: 170 40% 84%;
  --destructive: 350 62% 52%;
  --destructive-foreground: 0 0% 100%;
  --border: 26 8% 20%;
  --input: 26 8% 28%;
  --ring: 174 55% 45%;
  color-scheme: dark;`;

const CSS = `
.${THEME_CLASS} {${LIGHT}
  color: hsl(var(--foreground));
  -webkit-tap-highlight-color: transparent;
}
@media (prefers-color-scheme: dark) {
  html:not(.light-only) .${THEME_CLASS} {${DARK}
  }
  html:not(.light-only) .${THEME_CLASS} .light-island, html:not(.light-only) .light-island.${THEME_CLASS} {${LIGHT}
    color: hsl(var(--foreground));
  }
}
`;

export function RoomThemeStyle() {
  return <style data-room-theme="">{CSS}</style>;
}

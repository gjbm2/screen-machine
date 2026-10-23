// Dark mode follows the device's own setting (owner, 23 Oct: "make sure our various screens in this app all respond
// properly to dark mode"; "so css changes immediately and responsively if user changes dark mode toggle", the iPad in
// a performance). The colours turn by CSS alone: src/palette.css (every Tailwind colour a variable), index.css's and
// the Room theme's own variables and Tailwind's dark: variants are all under @media (prefers-color-scheme: dark), so
// a change of the setting turns the page at once, with no script in the way. Only html.light-only opts out: the TVs'
// /display pages (set before the first paint by index.html, kept right here as the route changes). Canvases read
// isDark() as they draw each frame.

import { useEffect } from "react";
import { useLocation } from "react-router-dom";

const LIGHT_ONLY = /^\/display(\/|$)/;
const QUERY = "(prefers-color-scheme: dark)";

/** The page is dark now (the device prefers dark and this isn't a light-only page). */
export function isDark(): boolean {
  return !!window.matchMedia?.(QUERY).matches && !document.documentElement.classList.contains("light-only");
}

/** Keeps html.light-only right as the route changes. Mount once inside the router. */
export function DarkMode(): null {
  const { pathname } = useLocation();
  useEffect(() => {
    document.documentElement.classList.toggle("light-only", LIGHT_ONLY.test(pathname));
  }, [pathname]);
  return null;
}

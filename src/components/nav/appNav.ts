// Where things are, for every page's menu and header (one menu and one header for the whole app).
//
// Links are same-origin relative paths where the page is the app itself (the generator, Schedules, Room, Music,
// Lights setup, Settings, a screen's TV page), so they work on whichever address the page was opened from (the LAN,
// the friendly HTTPS name, the public address) and stay inside the app (AppLink.tsx: no reload). The renderer's
// Library and the DSP monitor are LAN-only tools and come from the room link (roomLink.ts).

import { rendererConsoleUrl, roomApi, roomPageUrl, roomStatusUrl } from "@/components/room/roomLink";

export type AppPage = "generator" | "room" | "music" | "lights" | "settings" | "scheduler" | "other";

/** The generator (home page), opened on a screen's tab with ?publish=<id>. */
export function generatorUrl(screen?: string | null): string {
  return screen ? `/?publish=${encodeURIComponent(screen)}` : "/";
}

export function schedulerUrl(screen?: string | null): string {
  return screen ? `/scheduler?destination=${encodeURIComponent(screen)}` : "/scheduler";
}

/** What the TV itself loads (a phone joins as a viewer, so it doesn't take over the TV). */
export function displayUrl(screen: string): string {
  return `/display/${encodeURIComponent(screen)}`;
}

export { roomPageUrl, rendererConsoleUrl };

/** The Music page (piano pieces, playlists, recordings), next to the Room console (same address rules). */
export function musicPageUrl(tab?: "browse" | "playlists" | "mine" | "web" | null): string {
  return `${roomPageUrl().replace(/\/room$/, "/music")}${tab ? `?tab=${tab}` : ""}`;
}

/** Lights setup: add, name and find lights. */
export function lightsPageUrl(tab?: "map" | null): string {
  return tab ? `/lights?tab=${tab}` : "/lights";
}

/** Settings, opened on one group (/settings#<group>, e.g. "modes", "screen-north", "listening"). */
export function settingsUrl(group?: string | null): string {
  return group ? `/settings#${encodeURIComponent(group)}` : "/settings";
}

/** The DSP monitor (show_service's levels and spectrum page, through room_service's /show pass-through). */
export function dspMonitorUrl(): string {
  return `${roomApi().baseUrl}/show/dev/dsp`;
}

/** The places row under every page's title (PageHeader.tsx), in this order. `lan`: shown only while the room
 *  answers (or on that page itself). */
export const PLACES: Array<{ page: AppPage; label: string; href: () => string; lan?: boolean }> = [
  { page: "generator", label: "Generator", href: () => generatorUrl() },
  { page: "room", label: "Room", href: () => roomPageUrl(), lan: true },
  { page: "music", label: "Music", href: () => musicPageUrl(), lan: true },
  { page: "lights", label: "Lights", href: () => lightsPageUrl(), lan: true },
];

/** Tools for the house PC, LAN only, each in a new tab. */
export const TOOL_LINKS: Array<{ label: string; hint: string; href: () => string }> = [
  { label: "Scene library", hint: "Live scenes, effects and ratings", href: rendererConsoleUrl },
  { label: "DSP monitor", hint: "What the room's ears hear, in detail", href: dspMonitorUrl },
];

/** Developer pages. `lan` ones live on room_service's LAN door, so they show only when it answers. */
export const DEV_LINKS: Array<{ label: string; href: () => string; lan?: boolean }> = [
  { label: "Room status (JSON)", href: roomStatusUrl, lan: true },
  { label: "LightSense", href: () => "/lightsense" },
  { label: "Scheduler simulator", href: () => "/simulate-scheduler" },
  { label: "Bucket test UI", href: () => "/test-buckets/" },
  { label: "Schema form playground", href: () => "/schema-dynamic-form" },
  { label: "Admin", href: () => "/adminadmin" },
  { label: "App health (JSON)", href: () => "/api/health" },
];

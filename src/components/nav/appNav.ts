// Where things are, for every page's menu (the generator home page and the Room console share one menu).
//
// Links are same-origin where the page is the app itself (the generator, the scheduler, a screen's tab), so
// they work on whichever address the page was opened from (the LAN, the friendly HTTPS name, the public
// address). Room and the renderer's Library are LAN-only and come from the room link (roomLink.ts).

import { rendererConsoleUrl, roomPageUrl, roomStatusUrl } from "@/components/room/roomLink";

export type AppPage = "generator" | "room" | "music" | "scheduler" | "other";

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

/** The Music page (piano pieces, playlists, recordings): LAN-only like Room, on the same address as the Room console. */
export function musicPageUrl(): string {
  return roomPageUrl().replace(/\/room$/, "/music");
}

/** Developer pages. `lan` ones live on room_service's LAN door, so they show only when it answers. */
export const DEV_LINKS: Array<{ label: string; href: () => string; lan?: boolean }> = [
  { label: "Renderer console & Library", href: rendererConsoleUrl, lan: true },
  { label: "Room status (JSON)", href: roomStatusUrl, lan: true },
  { label: "Spectre (ops)", href: () => "http://192.168.1.205/", lan: true },
  { label: "LightSense", href: () => "/lightsense" },
  { label: "Scheduler simulator", href: () => "/simulate-scheduler" },
  { label: "Bucket test UI", href: () => "/test-buckets/" },
  { label: "Schema form playground", href: () => "/schema-dynamic-form" },
  { label: "Admin", href: () => "/adminadmin" },
  { label: "App health (JSON)", href: () => "/api/health" },
];

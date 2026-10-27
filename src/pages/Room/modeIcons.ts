// The room's mode icons (room_service's ICONS by name; the mode editor's picker draws these), and a mode's icon: its
// own, else one guessed from its name. Shared by the Room page's mode tiles, the mode menu in the app's header and the
// app menu.

import {
  Clapperboard,
  Disc3,
  FlaskConical,
  House,
  Image as ImageIcon,
  Lamp,
  LayoutGrid,
  Moon,
  Music,
  PartyPopper,
  Piano,
  Power,
  Sparkles,
  Sun,
  Waves,
  type LucideIcon,
} from "lucide-react";

/** The room's mode icons by their modes.json name (room_service's ICONS; the editor's picker draws these). */
export const MODE_ICONS: Record<string, LucideIcon> = {
  home: House,
  music: Music,
  piano: Piano,
  party: PartyPopper,
  disco: Disc3,
  film: Clapperboard,
  moon: Moon,
  sun: Sun,
  sparkles: Sparkles,
  power: Power,
  lamp: Lamp,
  image: ImageIcon,
  waves: Waves,
  grid: LayoutGrid,
  flask: FlaskConical,
};

/** A mode's icon: its own ("icon" in modes.json), else a fitting one guessed from its name and label. */
export function modeIcon(name: string, def?: { label?: string; icon?: unknown } | null): LucideIcon {
  const own = typeof def?.icon === "string" ? MODE_ICONS[def.icon] : undefined;
  return own ?? guessIcon(name, def?.label);
}

/** The guess, from words in the mode's name and label, else a neutral one. */
function guessIcon(name: string, label?: string): LucideIcon {
  const s = `${name} ${label ?? ""}`.toLowerCase().replace(/[-_]/g, " ");
  if (/\b(disco|party|dance|club)\b/.test(s)) return PartyPopper;
  if (/\b(music|sonos|piano|tracks?|songs?|listen|radio)\b/.test(s)) return Music;
  if (/\b(night|sleep|off|rest|quiet|bed)\b/.test(s)) return Moon;
  if (/\b(movies?|films?|cinema|watch)\b/.test(s)) return Clapperboard;
  if (/\b(gallery|art|frames?|pictures?|photos?)\b/.test(s)) return ImageIcon;
  if (/\b(live|show|perform|set|visuals?)\b/.test(s)) return Sparkles;
  return LayoutGrid;
}


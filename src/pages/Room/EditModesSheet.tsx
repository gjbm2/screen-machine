// "Edit modes" (the pencil at the end of the Room page's mode row): every mode in one list, each with Edit (the mode
// editor), Duplicate, Start here, Move up/down and Delete, and "Add a mode" at the foot. It only draws: the saves are
// ModeSwitcher's (the same ones as each tile's ⋯), so both always do the same thing. Modes under "More modes" are listed
// too, marked as such. The foot links to Settings › Room & modes for the rest (Auto's wait, the switching order).

import { ArrowDown, ArrowUp, Copy, Pencil, Plus, Trash2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { settingsUrl } from "@/components/nav/appNav";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { THEME_CLASS } from "./theme";

export interface EditModesRow {
  name: string;
  label: string;
  icon: LucideIcon;
  /** The room starts in it. */
  isDefault: boolean;
  /** The room is in it now. */
  isCurrent: boolean;
  /** Its tile sits under "More modes" (on_room_page "more"). */
  more: boolean;
}

const ICON_BTN =
  "grid h-11 w-10 shrink-0 place-items-center rounded-full text-stone-600 hover:bg-stone-200/60 hover:text-stone-900 disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function EditModesSheet({
  open,
  onOpenChange,
  rows,
  addFrom,
  onEdit,
  onDuplicate,
  onStartHere,
  onMove,
  onDelete,
  onAdd,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rows: EditModesRow[];
  /** The label of the mode "Add a mode" copies (modes are flat: a new one is a full copy). */
  addFrom: string | null;
  onEdit: (name: string) => void;
  onDuplicate: (name: string) => void;
  onStartHere: (name: string) => void;
  onMove: (name: string, by: -1 | 1) => void;
  onDelete: (name: string) => void;
  onAdd: () => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className={cn(THEME_CLASS, "w-full overflow-y-auto bg-background sm:max-w-md")}>
        <SheetHeader className="text-left">
          <SheetTitle>Edit modes</SheetTitle>
          <SheetDescription>Change, copy, order or delete the room's modes.</SheetDescription>
        </SheetHeader>

        <ul className="mt-4 divide-y divide-stone-200/80 rounded-2xl border border-stone-200/80 bg-card" data-testid="edit-modes">
          {rows.map((r, i) => {
            const Icon = r.icon;
            const notes = [r.isCurrent && "On now", r.isDefault && "The room starts here", r.more && "Under More modes"].filter(Boolean);
            return (
              <li key={r.name} className="px-3 py-2.5">
                <div className="flex items-center gap-3">
                  <span
                    aria-hidden
                    className={cn(
                      "grid h-9 w-9 shrink-0 place-items-center rounded-full",
                      r.isCurrent ? "bg-teal-700 text-white" : "bg-stone-100 text-stone-600",
                    )}
                  >
                    <Icon className="h-[18px] w-[18px]" />
                  </span>
                  <button
                    type="button"
                    onClick={() => onEdit(r.name)}
                    className="min-h-11 min-w-0 flex-1 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <span className="block text-base font-semibold leading-snug break-words">{r.label}</span>
                    {notes.length > 0 && <span className="block text-sm leading-snug text-muted-foreground">{notes.join(" · ")}</span>}
                  </button>
                  <button type="button" className={ICON_BTN} aria-label={`Edit ${r.label}`} title="Edit" onClick={() => onEdit(r.name)}>
                    <Pencil className="h-[18px] w-[18px]" aria-hidden />
                  </button>
                </div>
                <div className="mt-0.5 flex items-center gap-0.5 pl-9">
                  <button type="button" className={ICON_BTN} aria-label={`Move ${r.label} up`} title="Move up" disabled={i === 0} onClick={() => onMove(r.name, -1)}>
                    <ArrowUp className="h-[18px] w-[18px]" aria-hidden />
                  </button>
                  <button
                    type="button"
                    className={ICON_BTN}
                    aria-label={`Move ${r.label} down`}
                    title="Move down"
                    disabled={i === rows.length - 1}
                    onClick={() => onMove(r.name, 1)}
                  >
                    <ArrowDown className="h-[18px] w-[18px]" aria-hidden />
                  </button>
                  <button type="button" className={ICON_BTN} aria-label={`Duplicate ${r.label}`} title="Duplicate" onClick={() => onDuplicate(r.name)}>
                    <Copy className="h-[18px] w-[18px]" aria-hidden />
                  </button>
                  {!r.isDefault && (
                    <button
                      type="button"
                      onClick={() => onStartHere(r.name)}
                      className="min-h-11 whitespace-nowrap rounded-full px-2.5 text-[15px] text-teal-800 underline underline-offset-2 hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      Start here
                    </button>
                  )}
                  <button
                    type="button"
                    className={cn(ICON_BTN, "ml-auto text-rose-700 hover:bg-rose-50 hover:text-rose-800")}
                    aria-label={`Delete ${r.label}`}
                    title="Delete"
                    onClick={() => onDelete(r.name)}
                  >
                    <Trash2 className="h-[18px] w-[18px]" aria-hidden />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>

        <button
          type="button"
          onClick={onAdd}
          className="mt-3 flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl border border-dashed border-stone-300 text-[15px] text-stone-700 hover:border-stone-400 hover:bg-stone-50"
        >
          <Plus className="h-4 w-4" aria-hidden /> Add a mode
          {addFrom && <span className="text-sm text-muted-foreground">(a copy of {addFrom})</span>}
        </button>

        <p className="mt-4 text-[15px]">
          <AppLink
            href={settingsUrl("modes")}
            state={{ from: "Room" }}
            onClick={() => onOpenChange(false)}
            className="font-medium text-teal-800 underline underline-offset-2"
          >
            Mode settings ›
          </AppLink>{" "}
          <span className="text-muted-foreground">Auto's wait, the switching order, what each mode does.</span>
        </p>
      </SheetContent>
    </Sheet>
  );
}

// The setup key (room token) prompt, after the admin panel's pattern
// (src/pages/AdminPanel.tsx): paste once, kept in localStorage (sm_room_token),
// asked for again on a 401. Operating the room doesn't need it (LIVE_V1 §3).

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { Unroll } from "./bits";
import { Modal } from "./overlays";
import { TAP_BUTTON_CLASS } from "./theme";

export function TokenDialog({
  open,
  onOpenChange,
  baseUrl,
  required,
  foreign,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  baseUrl: string;
  /** True when room_service refused the current token, or there is none. */
  required: boolean;
  /** Set when a token is saved but for another server, so it wasn't sent here. */
  foreign?: string | null;
  onSave: (token: string) => void;
}) {
  const [value, setValue] = useState("");
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={required ? "This room needs a key" : "Setup key"}
      description={
        required
          ? "Ask whoever runs the room for it, then paste it here."
          : "Only needed for changing the room's setup. Switching modes and screens works without it."
      }
    >
      <form
        data-testid="token-prompt"
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onSave(value.trim());
          setValue("");
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor="room-token" className="text-sm font-normal text-muted-foreground">
            Setup key
          </Label>
          <Input
            id="room-token"
            type="password"
            autoComplete="off"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Paste the key"
            className="h-11 rounded-xl bg-white font-mono text-base"
            autoFocus
          />
        </div>
        <Unroll title="Where's the key?" testId="token-where">
          <p className="pb-1 text-sm leading-relaxed text-muted-foreground">
            It's the room token, in <code className="text-[13px]">~/.config/screen-machine/room_service.token</code> on the
            workstation, for <span className="break-all font-mono text-[13px]">{baseUrl}</span>. It's kept in this browser
            only.
          </p>
        </Unroll>
        {foreign && (
          <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            The saved key belongs to <span className="break-all font-mono text-[13px]">{foreign}</span>, so it wasn't sent to
            this server. If you didn't choose this server yourself, open the menu and use <strong>Use default</strong> under
            the room service address instead.
          </p>
        )}
        <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:justify-end">
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)} className={TAP_BUTTON_CLASS}>
            {required ? "Not now" : "Cancel"}
          </Button>
          <Button type="submit" disabled={!value.trim()} className={cn(TAP_BUTTON_CLASS)}>
            Save key
          </Button>
        </div>
      </form>
    </Modal>
  );
}

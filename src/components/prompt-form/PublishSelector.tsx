// Where a new picture goes: "Show on: North screen". One tap-opened popover at every width (a hover card ignores
// touch, so the iPad couldn't choose), a tick on each chosen screen, open while you toggle, and Done.
// Anything on the page can open it with the OPEN_SHOW_ON window event (the screen tabs' "change").

import React, { useEffect, useState } from 'react';
import { Check, MonitorUp, MonitorOff, Share2 } from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';
import { cn } from '@/lib/utils';

/** window event: open the "Show on" chooser. */
export const OPEN_SHOW_ON = 'generator:open-show-on';

interface PublishSelectorProps {
  selectedPublish: string;
  onPublishChange: (publishId: string) => void;
  isCompact?: boolean;
}

const toList = (v: string) => (v && v !== 'none' ? v.split(',').filter(Boolean) : []);

const PublishSelector: React.FC<PublishSelectorProps> = ({ selectedPublish, onPublishChange }) => {
  const { destinations } = usePublishDestinations();
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState<string[]>(toList(selectedPublish));

  useEffect(() => setChosen(toList(selectedPublish)), [selectedPublish]);
  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener(OPEN_SHOW_ON, onOpen);
    return () => window.removeEventListener(OPEN_SHOW_ON, onOpen);
  }, []);

  const icon = (name?: string, cls = 'h-4 w-4') => {
    const C = name ? (LucideIcons as unknown as Record<string, React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>>)[name] : null;
    return C ? <C className={cls} aria-hidden /> : <Share2 className={cls} aria-hidden />;
  };

  const commit = (next: string[]) => {
    setChosen(next);
    onPublishChange(next.length ? next.join(',') : 'none');
  };
  const toggle = (id: string) => commit(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);

  const names = chosen.map((id) => destinations.find((d) => d.id === id)?.name || id);
  const label = names.length === 0 ? 'Nowhere yet' : names.length === 1 ? names[0] : `${names.length} screens`;

  const row = 'flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

  return (
    <div className="flex items-center">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="outline"
            className={cn(
              'h-9 max-w-full shrink-0 gap-1.5 rounded-full border-input bg-card px-3 text-sm font-normal',
              open && 'bg-accent',
            )}
            aria-label={`Show on: ${label}. Choose screens`}
            data-testid="show-on-chip"
          >
            {names.length ? <MonitorUp className="h-4 w-4 shrink-0 text-teal-700" aria-hidden /> : <MonitorOff className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />}
            <span className="text-muted-foreground">Show on:</span>
            <span className="min-w-0 truncate font-medium">{label}</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" side="bottom" sideOffset={6} collisionPadding={8} className="w-72 max-h-[70vh] overflow-y-auto p-2">
          <div className="px-3 pb-1 pt-1 text-sm font-semibold">Show on</div>
          <p className="px-3 pb-2 text-[13px] leading-snug text-muted-foreground">New pictures go to the screens you tick.</p>
          <button type="button" className={cn(row, chosen.length === 0 && 'font-medium')} onClick={() => commit([])} aria-pressed={chosen.length === 0}>
            <MonitorOff className="h-4 w-4 shrink-0" aria-hidden />
            <span className="flex-1">Nowhere (just make it)</span>
            {chosen.length === 0 && <Check className="h-4 w-4 shrink-0 text-teal-700" aria-hidden />}
          </button>
          {destinations.map((d) => {
            const on = chosen.includes(d.id);
            return (
              <button key={d.id} type="button" className={cn(row, on && 'font-medium')} onClick={() => toggle(d.id)} aria-pressed={on}>
                {icon(d.icon)}
                <span className="min-w-0 flex-1">{d.name}</span>
                {on && <Check className="h-4 w-4 shrink-0 text-teal-700" aria-hidden />}
              </button>
            );
          })}
          <div className="flex justify-end px-1 pb-1 pt-2">
            <Button type="button" size="sm" className="h-10 rounded-xl px-5" onClick={() => setOpen(false)}>
              Done
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
};

export default PublishSelector;

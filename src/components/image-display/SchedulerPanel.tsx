// A screen tab's schedule row: "Schedule: Running · nothing queued" with Pause / Stop (or Start / Resume) in words,
// and, opened, the real status (the next action), "Open North screen's schedule ›" and "Unload top script".
// Runs the full width of the screen's header.

import { useState } from 'react';
import { ChevronDown, ChevronUp, CircleStop, Pause, Play, RefreshCw, Square, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { schedulerApi } from '@/shared/api';
import { schedulerUrl } from '@/components/nav/appNav';
import { cn } from '@/lib/utils';

// Next Action interface
interface NextAction {
  has_next_action: boolean;
  next_time: string | null;
  description: string | null;
  minutes_until_next: number | null;
  timestamp: string;
  time_until_display?: string;
}

const inWords = (m: number) => (m < 60 ? `${Math.round(m)} minutes from now` : `${Math.floor(m / 60)}h ${Math.round(m % 60)}m from now`);

/** "Running · nothing queued", "Running · next 14:00", "Paused", "Stopped". */
function statusWords(isRunning: boolean, isPaused: boolean, next: NextAction | null): string {
  if (!isRunning) return 'Stopped';
  if (isPaused) return 'Paused';
  if (!next || !next.has_next_action) return 'Running · nothing queued';
  return next.next_time ? `Running · next ${next.next_time}` : 'Running';
}

const StateIcon = ({ isRunning, isPaused }: { isRunning: boolean; isPaused: boolean }) =>
  !isRunning ? (
    <CircleStop className="h-4 w-4 shrink-0 text-rose-600" aria-hidden />
  ) : isPaused ? (
    <Pause className="h-4 w-4 shrink-0 text-amber-600" aria-hidden />
  ) : (
    <RefreshCw className="h-4 w-4 shrink-0 text-emerald-600 motion-safe:animate-[spin_5s_linear_infinite]" aria-hidden />
  );

const WORD_BUTTON =
  'inline-flex h-9 items-center gap-1.5 rounded-full border border-stone-300 bg-card px-3 text-[14px] font-medium hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60';

// Main scheduler control component props
interface SchedulerControlProps {
  destination: string;
  /** The screen's name, for "Open North screen's schedule ›". */
  destinationName?: string;
  isRunning?: boolean;
  isPaused?: boolean;
  nextAction?: NextAction | null;
  refreshScheduler: (destination: string) => void;
}

export const SchedulerControl = ({
  destination,
  destinationName,
  isRunning = false,
  isPaused = false,
  nextAction = null,
  refreshScheduler,
}: SchedulerControlProps) => {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const name = destinationName || destination;

  const PAST: Record<string, string> = { start: 'started', stop: 'stopped', pause: 'paused', unpause: 'resumed' };
  const handleAction = async (action: 'start' | 'stop' | 'pause' | 'unpause') => {
    setBusy(action);
    try {
      switch (action) {
        case 'start':
          await schedulerApi.startScheduler(destination, {});
          break;
        case 'stop':
          await schedulerApi.stopScheduler(destination);
          break;
        case 'pause':
          await schedulerApi.pauseScheduler(destination);
          break;
        case 'unpause':
          await schedulerApi.unpauseScheduler(destination);
          break;
      }
      toast.success(`${name}'s schedule ${PAST[action]}`);
      refreshScheduler(destination);
    } catch (error) {
      console.error(`Error ${action}ing scheduler:`, error);
      toast.error(`Couldn't change ${name}'s schedule`);
    } finally {
      setBusy(null);
    }
  };

  const handleUnloadTopScript = async () => {
    setBusy('unload');
    try {
      const success = await schedulerApi.unloadSchedule(destination);
      if (success) {
        toast.success('Top script unloaded');
        refreshScheduler(destination);
      }
    } catch (error) {
      console.error('Error unloading top script:', error);
      toast.error("Couldn't unload the top script");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="w-full" data-testid="schedule-row">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="inline-flex min-h-9 min-w-0 items-center gap-2 rounded-full px-1 text-left text-[15px] hover:bg-stone-200/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <StateIcon isRunning={isRunning} isPaused={isPaused} />
          <span className="min-w-0">
            <span className="text-muted-foreground">Schedule: </span>
            <span className="font-medium">{statusWords(isRunning, isPaused, nextAction)}</span>
          </span>
          {open ? <ChevronUp className="h-4 w-4 shrink-0" aria-hidden /> : <ChevronDown className="h-4 w-4 shrink-0" aria-hidden />}
        </button>
        <span className="flex flex-wrap items-center gap-1.5">
          {!isRunning && (
            <button type="button" className={WORD_BUTTON} disabled={!!busy} onClick={() => void handleAction('start')}>
              <Play className="h-3.5 w-3.5" aria-hidden /> Start
            </button>
          )}
          {isRunning && !isPaused && (
            <button type="button" className={WORD_BUTTON} disabled={!!busy} onClick={() => void handleAction('pause')}>
              <Pause className="h-3.5 w-3.5" aria-hidden /> Pause
            </button>
          )}
          {isRunning && isPaused && (
            <button type="button" className={WORD_BUTTON} disabled={!!busy} onClick={() => void handleAction('unpause')}>
              <Play className="h-3.5 w-3.5" aria-hidden /> Resume
            </button>
          )}
          {isRunning && (
            <button type="button" className={WORD_BUTTON} disabled={!!busy} onClick={() => void handleAction('stop')}>
              <Square className="h-3 w-3 fill-current" aria-hidden /> Stop
            </button>
          )}
        </span>
      </div>

      {open && (
        <div className={cn('mt-2 w-full space-y-3 rounded-xl border border-stone-200/80 bg-card p-3')}>
          {nextAction && nextAction.has_next_action ? (
            <div className="border-l-2 border-teal-600 pl-3 text-[15px]">
              <div className="font-medium">Next: {nextAction.next_time}</div>
              {nextAction.description && <div className="text-muted-foreground">{nextAction.description}</div>}
              {nextAction.minutes_until_next !== null && (
                <div className="mt-0.5 text-muted-foreground">{nextAction.time_until_display || inWords(nextAction.minutes_until_next)}</div>
              )}
            </div>
          ) : (
            <p className="border-l-2 border-stone-300 pl-3 text-[15px] text-muted-foreground">
              {isRunning ? 'Nothing queued.' : 'The schedule is stopped.'}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={schedulerUrl(destination)}
              className="inline-flex h-9 items-center rounded-full px-2 text-[15px] font-medium text-teal-800 hover:bg-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Open {name}'s schedule ›
            </Link>
            <button type="button" className={WORD_BUTTON} disabled={!!busy} onClick={() => void handleUnloadTopScript()}>
              <X className="h-3.5 w-3.5" aria-hidden /> Unload top script
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

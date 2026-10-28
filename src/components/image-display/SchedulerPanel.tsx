// A screen's schedule, for the generator's screen strip (src/components/room/ScreenStrip.tsx): its state in a word
// ("Schedule on / paused / off"), the actions (start, pause, resume, stop, unload the top script) and the details that
// unroll under it (what it does next, and the way to the Schedules page).

import { useCallback } from 'react';
import { RefreshCw, Pause, CircleStop, X, Square } from 'lucide-react';
import { toast } from 'sonner';
import { schedulerApi } from '@/shared/api';
import { cn } from '@/lib/utils';

export type SchedulerAction = 'start' | 'stop' | 'pause' | 'unpause';

// Next Action interface
export interface NextAction {
  has_next_action: boolean;
  next_time: string | null;
  description: string | null;
  minutes_until_next: number | null;
  timestamp: string;
  time_until_display?: string;
}

export type ScheduleState = 'on' | 'paused' | 'off';

export function scheduleState(isRunning?: boolean, isPaused?: boolean): ScheduleState {
  return isRunning ? (isPaused ? 'paused' : 'on') : 'off';
}

const DONE_WORDS: Record<SchedulerAction, string> = {
  start: 'Schedule started',
  stop: 'Schedule stopped',
  pause: 'Schedule paused',
  unpause: 'Schedule resumed',
};

/** The schedule's actions; each one re-reads its status (refreshScheduler) when it's done. */
export function useSchedulerActions(destination: string, refreshScheduler: (destination: string) => void) {
  const run = useCallback(
    async (action: SchedulerAction) => {
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
        toast.success(DONE_WORDS[action]);
        refreshScheduler(destination);
      } catch (error) {
        console.error(`Error ${action}ing scheduler:`, error);
        toast.error(`Couldn't ${action === 'unpause' ? 'resume' : action} the schedule`);
      }
    },
    [destination, refreshScheduler],
  );

  const unloadTop = useCallback(async () => {
    try {
      const success = await schedulerApi.unloadSchedule(destination);
      if (success) {
        toast.success('Top script unloaded');
        refreshScheduler(destination);
      }
    } catch (error) {
      console.error('Error unloading top script:', error);
      toast.error('Failed to unload top script');
    }
  }, [destination, refreshScheduler]);

  return { run, unloadTop };
}

/** The schedule's state as an icon: turning (on), paused, or stopped. */
export const SchedulerStateIcon = ({ state, className }: { state: ScheduleState; className?: string }) => {
  if (state === 'paused') return <Pause aria-hidden className={cn('h-4 w-4 text-amber-600', className)} />;
  if (state === 'on')
    return <RefreshCw aria-hidden className={cn('h-4 w-4 text-teal-700 motion-safe:animate-[spin_5s_linear_infinite]', className)} />;
  return <CircleStop aria-hidden className={cn('h-4 w-4 text-stone-500', className)} />;
};

function nextWords(nextAction: NextAction): string | null {
  if (nextAction.minutes_until_next === null) return null;
  if (nextAction.time_until_display) return nextAction.time_until_display;
  const m = nextAction.minutes_until_next;
  return m < 60 ? `${Math.round(m)} minutes from now` : `${Math.floor(m / 60)}h ${Math.round(m % 60)}m from now`;
}

const DETAIL_BUTTON =
  'inline-flex h-10 items-center gap-1.5 rounded-xl border border-stone-300 bg-white px-3 text-[14px] font-medium hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

/** What unrolls under the strip's schedule chip: what it does next, stop, and unload the top script. */
export const SchedulerDetails = ({
  destination,
  isRunning = false,
  isPaused = false,
  nextAction = null,
  actions,
}: {
  destination: string;
  isRunning?: boolean;
  isPaused?: boolean;
  nextAction?: NextAction | null;
  actions: ReturnType<typeof useSchedulerActions>;
}) => {
  const state = scheduleState(isRunning, isPaused);
  const when = nextAction?.has_next_action ? nextWords(nextAction) : null;
  return (
    <div className="space-y-2 text-[14px]" data-testid={`schedule-details-${destination}`}>
      <div className="flex items-start gap-2">
        <SchedulerStateIcon state={state} className="mt-0.5 shrink-0" />
        <div className="min-w-0">
          <div className="font-semibold">
            {state === 'on' ? 'Schedule on' : state === 'paused' ? 'Schedule paused' : 'No schedule running'}
          </div>
          {nextAction?.has_next_action ? (
            <div className="text-muted-foreground">
              Next: {nextAction.next_time}
              {nextAction.description ? ` · ${nextAction.description}` : ''}
              {when ? ` · ${when}` : ''}
            </div>
          ) : (
            <div className="text-muted-foreground">Nothing coming up: the schedule is empty or waiting for its settings.</div>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {isRunning && (
          <button type="button" className={DETAIL_BUTTON} onClick={() => void actions.run('stop')}>
            <Square className="h-4 w-4" aria-hidden /> Stop schedule
          </button>
        )}
        <button type="button" className={DETAIL_BUTTON} onClick={() => void actions.unloadTop()}>
          <X className="h-4 w-4" aria-hidden /> Unload top script
        </button>
      </div>
    </div>
  );
};

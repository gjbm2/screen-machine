import { useState } from 'react';
import { RefreshCw, Pause, CircleStop, Play, Square, ChevronUp, ChevronDown, Plus, X, Settings } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import apiService from '@/utils/api';

// Action icons component
interface ActionIconButtonProps {
  action: 'start' | 'pause' | 'unpause' | 'stop';
  onAction: (action: 'start' | 'stop' | 'pause' | 'unpause') => Promise<void>;
}

const ActionIconButton = ({ action, onAction }: ActionIconButtonProps) => {
  if (action === 'start') {
    return (
      <Button variant="ghost" size="icon" onClick={() => onAction('start')} className="h-6 w-6">
        <Play className="h-3 w-3" />
      </Button>
    );
  }
  if (action === 'pause') {
    return (
      <Button variant="ghost" size="icon" onClick={() => onAction('pause')} className="h-6 w-6">
        <Pause className="h-3 w-3" />
      </Button>
    );
  }
  if (action === 'unpause') {
    return (
      <Button variant="ghost" size="icon" onClick={() => onAction('unpause')} className="h-6 w-6">
        <Play className="h-3 w-3" />
      </Button>
    );
  }
  // stop
  return (
    <Button variant="ghost" size="icon" onClick={() => onAction('stop')} className="h-6 w-6">
      <Square className="h-3 w-3" />
    </Button>
  );
};

// Status icon component
const SchedulerStateIcon = ({ isRunning, isPaused }: { isRunning?: boolean; isPaused?: boolean }) => {
  if (isRunning) {
    if (isPaused) {
      return <Pause className="h-4 w-4 text-amber-500" />;
    }
    return <RefreshCw className="h-4 w-4 text-green-500 animate-[spin_5s_linear_infinite]" />;
  }
  return <CircleStop className="h-4 w-4 text-red-500" />;
};

// Next Action interface
interface NextAction {
  has_next_action: boolean;
  next_time: string | null;
  description: string | null;
  minutes_until_next: number | null;
  timestamp: string;
  time_until_display?: string;
}

// Format next scheduled action component
const FormatNextAction = ({ nextAction }: { nextAction: NextAction | null }) => {
  if (!nextAction || !nextAction.has_next_action) {
    return (
      <div className="border-l-2 border-muted-foreground pl-3 py-1 text-sm">
        <div className="font-medium">No upcoming actions</div>
        <div className="text-muted-foreground">
          Schedule is empty or waiting for configuration
        </div>
      </div>
    );
  }

  return (
    <div className="border-l-2 border-primary pl-3 py-1 text-sm">
      <div className="font-medium">Next action: {nextAction.next_time}</div>
      <div className="text-muted-foreground">
        {nextAction.description}
      </div>
      {nextAction.minutes_until_next !== null && (
        <div className="text-muted-foreground mt-1">
          {nextAction.time_until_display || 
            (nextAction.minutes_until_next < 60 
              ? `${Math.round(nextAction.minutes_until_next)} minutes from now`
              : `${Math.floor(nextAction.minutes_until_next / 60)}h ${Math.round(nextAction.minutes_until_next % 60)}m from now`)
          }
        </div>
      )}
    </div>
  );
};

// Main scheduler control component props
interface SchedulerControlProps {
  destination: string;
  isRunning?: boolean;
  isPaused?: boolean;
  nextAction?: NextAction | null;
  refreshScheduler: (destination: string) => void;
}

export const SchedulerControl = ({
  destination,
  isRunning = false,
  isPaused = false,
  nextAction = null,
  refreshScheduler
}: SchedulerControlProps) => {
  const [schedulerPanelOpen, setSchedulerPanelOpen] = useState<boolean>(false);

  // Handle scheduler actions
  const handleAction = async (action: 'start' | 'stop' | 'pause' | 'unpause') => {
    try {
      let result;
      switch (action) {
        case 'start':
          result = await apiService.startScheduler(destination, {});
          break;
        case 'stop':
          result = await apiService.stopScheduler(destination);
          break;
        case 'pause':
          result = await apiService.pauseScheduler(destination);
          break;
        case 'unpause':
          result = await apiService.unpauseScheduler(destination);
          break;
      }
      
      toast.success(`Scheduler ${action}ed successfully`);
      refreshScheduler(destination);
    } catch (error) {
      console.error(`Error ${action}ing scheduler:`, error);
      toast.error(`Failed to ${action} scheduler`);
    }
  };

  // Handle unload top script action
  const handleUnloadTopScript = async () => {
    try {
      const success = await apiService.unloadSchedule(destination);
      if (success) {
        toast.success('Top script unloaded successfully');
        refreshScheduler(destination);
      }
    } catch (error) {
      console.error('Error unloading top script:', error);
      toast.error('Failed to unload top script');
    }
  };

  // Handle navigation to scheduler page
  const handleOpenSchedulerPage = () => {
    window.location.href = `/scheduler?destination=${destination}`;
  };

  return (
    <>
      {/* Scheduler controls */}
      <div className="flex justify-end pt-2">
        <div className="bg-muted-foreground/10 border border-border/70 rounded-md px-3 py-1 flex items-center gap-1 shadow-sm">
          <span className="text-sm mr-2">Scheduler:</span>
          <SchedulerStateIcon isRunning={isRunning} isPaused={isPaused} />
          <div className="flex items-center space-x-1">
            {!isRunning && <ActionIconButton action="start" onAction={handleAction} />}
            {isRunning && !isPaused && <ActionIconButton action="pause" onAction={handleAction} />}
            {isRunning && isPaused && <ActionIconButton action="unpause" onAction={handleAction} />}
            {isRunning && <ActionIconButton action="stop" onAction={handleAction} />}
          </div>
          <Button 
            variant="ghost" 
            size="icon" 
            onClick={() => setSchedulerPanelOpen(!schedulerPanelOpen)} 
            className="h-6 w-6 ml-1"
          >
            {schedulerPanelOpen ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </Button>
        </div>
      </div>
      
      {/* Expanded scheduler panel */}
      {schedulerPanelOpen && (
        <div className="w-full -mt-0 bg-muted-foreground/10 border-t border-x border-b border-border/70 rounded-b-md shadow-inner p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-medium">Scheduler status</span>
            <Badge variant={isRunning ? 
              (isPaused ? "outline" : "default") : 
              "secondary"} 
              className={`capitalize ${isPaused ? "bg-amber-100" : ""}`}>
              {isPaused ? 'paused' : isRunning ? 'running' : 'stopped'}
            </Badge>
          </div>

          <FormatNextAction nextAction={nextAction} />

          <div className="flex mt-2 gap-2">
            <Button size="sm" variant="default" className="gap-1" onClick={handleOpenSchedulerPage}>
              <Plus className="h-4 w-4" /> Create new script
            </Button>
            <Button size="sm" variant="outline" className="gap-1" onClick={handleUnloadTopScript}>
              <X className="h-4 w-4" /> Unload top script
            </Button>
          </div>

          {/* Collapsible layer info - simplified from scheduler.tsx */}
          <div className="mt-3 border rounded-md">
            <div className="flex justify-between items-center p-2 bg-background/60">
              <div className="font-medium">Layer 2</div>
              <ChevronUp className="h-4 w-4" />
            </div>
            <div className="p-3">
              <div className="flex border-b">
                <Button size="sm" variant="ghost" className="rounded-none border-b-2 border-primary">Context</Button>
                <Button size="sm" variant="ghost" className="rounded-none text-muted-foreground">Script</Button>
              </div>
              <div className="mt-3">
                <div className="font-medium">Variables:</div>
                <div className="flex flex-col mt-1 text-sm">
                  <div className="flex justify-between">
                    <span>Last generated:</span>
                    <span>15 May 2024</span>
                  </div>
                </div>
                <div className="flex justify-end mt-3">
                  <Button size="sm" variant="outline" className="gap-1" onClick={handleOpenSchedulerPage}>
                    <Settings className="h-4 w-4" /> Set variables
                  </Button>
                </div>
              </div>
            </div>
          </div>

          {/* Collapsed layers as examples */}
          <div className="border rounded-md">
            <div className="flex justify-between items-center p-2 bg-background/60">
              <div className="font-medium">Layer 1</div>
              <ChevronDown className="h-4 w-4" />
            </div>
          </div>

          <div className="border rounded-md">
            <div className="flex justify-between items-center p-2 bg-background/60">
              <div className="font-medium">Logs</div>
              <ChevronDown className="h-4 w-4" />
            </div>
          </div>
        </div>
      )}
    </>
  );
}; 
export interface Schedule {
  id: string;
  name: string;
  cron: string;
  is_running: boolean;
  is_paused?: boolean;
  last_run: string;
  next_run: string;
  error?: string;
}

export interface Context {
  vars: Record<string, any>;
  [key: string]: any;
}

export interface ScheduleStack {
  schedule: any;
  context: Context;
}

export interface Destination {
  id: string;
  name: string;
  schedules: Schedule[];
  isRunning?: boolean;
  isPaused?: boolean;
  logs?: string[];
  scheduleStack?: any[];
  contextStack?: Context[];
}

export interface NextAction {
  has_next_action: boolean;
  next_time: string | null;
  description: string | null;
  minutes_until_next: number | null;
  timestamp: string;
  time_until_display?: string;
}

export interface SchedulerStatus {
  is_running: boolean;
  next_action: NextAction | null;
}

export interface InstructionQueueItem {
  action: string;
  important: boolean;
  urgent: boolean;
  details: Record<string, any>;
}

export interface InstructionQueueResponse {
  status: string;
  destination: string;
  queue_size: number;
  instructions: InstructionQueueItem[];
}

export interface CollapsibleCardHeaderProps {
  title: string;
  isOpen: boolean;
  onToggle: () => void;
  count?: number;
} 
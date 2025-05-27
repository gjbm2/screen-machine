import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/components/ui/use-toast';
import { 
  Play, 
  Pause, 
  Plus, 
  X, 
  Settings,
  ChevronDown,
  ChevronRight,
  Zap,
  List,
  ArrowDownToLine,
  Eye,
  FileText,
  RefreshCcw
} from 'lucide-react';
import { useIsMobile } from '@/hooks/useIsMobile';
import apiService from '@/utils/api';
import { SetVarsButton } from './SetVarsButton';
import { extractEventTriggers } from '@/utils/scheduleUtils';
import { MobileActionSheet } from '@/components/ui/MobileActionSheet';
import { HierarchicalHeader } from './HierarchicalHeader';
import { HierarchicalContent } from './HierarchicalContent';

interface Destination {
  id: string;
  name: string;
  schedules: any[];
  isRunning?: boolean;
  isPaused?: boolean;
  logs?: string[];
  scheduleStack?: any[];
  contextStack?: any[];
}

interface NextAction {
  has_next_action: boolean;
  next_time: string | null;
  description: string | null;
  minutes_until_next: number | null;
  timestamp: string;
  time_until_display?: string;
}

interface SchedulerStatus {
  is_running: boolean;
  next_action: NextAction | null;
}

interface InstructionQueueItem {
  action: string;
  important: boolean;
  urgent: boolean;
  details: Record<string, any>;
}

interface InstructionQueueResponse {
  status: string;
  destination: string;
  queue_size: number;
  instructions: InstructionQueueItem[];
}

interface SchedulerCardProps {
  destination: Destination;
  onToggle: (destinationId: string, currentlyPaused: boolean) => Promise<void>;
  onCreate: (destinationId: string) => Promise<void>;
  onStart: (destinationId: string) => Promise<void>;
  onStop: (destinationId: string) => Promise<void>;
  onUnload: (destinationId: string) => Promise<void>;
  onEdit: (destinationId: string, layer: number) => Promise<void>;
  schedulerStatus?: Record<string, SchedulerStatus>;
  instructionQueue?: InstructionQueueResponse;
  showInstructionQueue?: boolean;
  onToggleQueueView: () => void;
  isMobile?: boolean;
}

export const SchedulerCard: React.FC<SchedulerCardProps> = ({ 
  destination, 
  onToggle, 
  onCreate, 
  onStart, 
  onStop, 
  onUnload,
  onEdit,
  schedulerStatus,
  instructionQueue,
  showInstructionQueue,
  onToggleQueueView,
  isMobile
}) => {
  const [showLogs, setShowLogs] = useState(false);
  const [showSchedule, setShowSchedule] = useState(!isMobile);
  const [activeTab, setActiveTab] = useState<'context' | 'script'>('context');
  const [logs, setLogs] = useState<string[]>(destination.logs || []);
  const [logUpdatesPaused, setLogUpdatesPaused] = useState(false);
  const logsContainerRef = useRef<HTMLDivElement>(null);
  const { toast } = useToast();
  const [mobileActionSheetOpen, setMobileActionSheetOpen] = useState(false);
  
  // Add state for level 3 hierarchical sections
  const [detailsSectionOpen, setDetailsSectionOpen] = useState(false);
  const [logsSectionOpen, setLogsSectionOpen] = useState(false);
  const [queueSectionOpen, setQueueSectionOpen] = useState(false);
  const [scheduleDetailsSectionOpen, setScheduleDetailsSectionOpen] = useState(false);
  
  // Extract event triggers from all schedule layers
  const eventTriggers = useMemo(() => {
    if (!destination.scheduleStack || destination.scheduleStack.length === 0) {
      return [];
    }
    
    // Extract event triggers from each layer and combine them
    const allTriggers = new Set<string>();
    destination.scheduleStack.forEach(layer => {
      const layerTriggers = extractEventTriggers(layer);
      layerTriggers.forEach(trigger => allTriggers.add(trigger));
    });
    
    return Array.from(allTriggers);
  }, [destination.scheduleStack]);
  
  // Handle throwing an event
  const handleThrowEvent = async (eventKey: string) => {
    try {
      await apiService.throwEvent({
        event: eventKey,
        scope: destination.id,
        ttl: "60s"
      });
      
      toast({
        title: 'Event Triggered',
        description: `Event "${eventKey}" sent to "${destination.name}"`,
      });
    } catch (error) {
      console.error('Error throwing event:', error);
      toast({
        title: 'Error',
        description: 'Failed to trigger event',
        variant: 'destructive',
      });
    }
  };
  
  // Set up polling for logs when they're visible
  useEffect(() => {
    if (!showLogs) return;
    
    const fetchLogs = async () => {
      try {
        const logs = await apiService.getSchedulerLogs(destination.id);
        if (logs && logs.log) {
          if (logsContainerRef.current && window.getSelection) {
            const selection = window.getSelection();
            if (selection && selection.toString().length > 0) {
              if (!logUpdatesPaused) {
                setLogUpdatesPaused(true);
              }
              return;
            } else {
              if (logUpdatesPaused) {
                setLogUpdatesPaused(false);
              }
              setLogs(logs.log);
            }
          } else {
            setLogs(logs.log);
          }
        }
      } catch (error) {
        console.error(`Error fetching logs for ${destination.id}:`, error);
      }
    };
    
    fetchLogs();
    const intervalId = setInterval(fetchLogs, 5000);
    
    return () => {
      clearInterval(intervalId);
    };
  }, [showLogs, destination.id, logUpdatesPaused]);
  
  // When destination.logs updates from parent, update our local state
  useEffect(() => {
    setLogs(destination.logs || []);
  }, [destination.logs]);
  
  // Scroll logs to bottom whenever they change or become visible
  useEffect(() => {
    if (showLogs && logsContainerRef.current) {
      requestAnimationFrame(() => {
        if (logsContainerRef.current) {
          logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
        }
      });
    }
  }, [logs, showLogs]);
  
  // Display status badge
  const statusBadge = () => {
    const badgeClass = isMobile ? "text-xs px-2 py-0.5" : "px-3 py-1";
    
    if (destination.isPaused) {
      return (
        <Badge variant="outline" className={`${badgeClass} bg-amber-100`}>
          Paused
        </Badge>
      );
    } else if (destination.isRunning) {
      return (
        <Badge variant="default" className={badgeClass}>
          Running
        </Badge>
      );
    } else {
      return (
        <Badge variant="secondary" className={badgeClass}>
          Stopped
        </Badge>
      );
    }
  };

  // Format next scheduled action time
  const formatNextAction = (nextAction: NextAction | null) => {
    if (!nextAction || !nextAction.has_next_action) {
      return <p className="text-sm text-muted-foreground">No upcoming actions</p>;
    }

    return (
      <div className="text-sm border-l-4 border-primary pl-2 mt-2">
        <p className="font-medium">Next action: {nextAction.next_time}</p>
        <p className="text-muted-foreground">{nextAction.description}</p>
        {nextAction.minutes_until_next !== null && (
          <p className="text-xs">
            {nextAction.time_until_display || 
              (nextAction.minutes_until_next < 60 
                ? `${Math.round(nextAction.minutes_until_next)} minutes from now`
                : `${Math.floor(nextAction.minutes_until_next / 60)}h ${Math.round(nextAction.minutes_until_next % 60)}m from now`)
            }
          </p>
        )}
      </div>
    );
  };

  // Render context variables
  const renderContextVariables = (context: any) => {
    if (!context) {
      return <p className="text-sm text-muted-foreground">No context available</p>;
    }
    
    if (!context.vars) {
      return <p className="text-sm text-muted-foreground">No variables in context</p>;
    }
    
    return (
      <div className="bg-accent/10 p-2 rounded-md">
        {Object.keys(context.vars).length === 0 ? (
          <p className="text-sm text-muted-foreground mb-2">No variables in context</p>
        ) : (
          <>
            <h5 className="text-sm font-medium mb-2">Variables:</h5>
            <ul className="text-xs space-y-1">
              {Object.entries(context.vars).map(([key, value]) => (
                <li key={key} className="flex items-start">
                  <span className="font-semibold mr-2 flex-shrink-0">{key}:</span>
                  <div className="text-muted-foreground min-w-0 flex-1">
                    <div className="overflow-auto max-h-32 text-xs font-mono bg-muted/50 p-1 rounded">
                      {typeof value === 'object' 
                        ? JSON.stringify(value, null, 2)
                        : String(value)
                      }
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
        {context.last_generated && (
          <div className={Object.keys(context.vars).length === 0 ? "" : "mt-2"}>
            <h5 className="text-sm font-medium">Last Generated:</h5>
            <p className="text-xs text-muted-foreground">{context.last_generated}</p>
          </div>
        )}
      </div>
    );
  };
  
  // Handle state updates after script edits
  const handleScriptEdit = async (layer: number) => {
    try {
      await onEdit(destination.id, layer);
      
      if ((window as any).fetchDestinations) {
        await (window as any).fetchDestinations();
      }
    } catch (error) {
      console.error('Error editing script:', error);
      toast({
        title: 'Error',
        description: 'Failed to edit script',
        variant: 'destructive',
      });
    }
  };
  
  // Render instruction queue
  const renderInstructionQueue = () => {
    if (!instructionQueue || instructionQueue.queue_size === 0) {
      return <div className="text-sm text-muted-foreground p-2">No active instructions in queue</div>;
    }

    return (
      <div className="space-y-2">
        {instructionQueue.instructions.map((instruction, index) => (
          <div 
            key={index} 
            className={`p-2 rounded border ${instruction.urgent ? 'border-red-500 bg-red-50 dark:bg-red-950' : 
              instruction.important ? 'border-amber-500 bg-amber-50 dark:bg-amber-950' : 
              'border-gray-200 dark:border-gray-700'}`}
          >
            <div className="flex items-center justify-between">
              <div className="font-medium">
                {instruction.action}
                {instruction.urgent && (
                  <Badge variant="destructive" className="ml-2">Urgent</Badge>
                )}
                {instruction.important && !instruction.urgent && (
                  <Badge variant="secondary" className="ml-2">Important</Badge>
                )}
              </div>
            </div>
            <div className="text-xs text-muted-foreground mt-1">
              {Object.entries(instruction.details).map(([key, value]) => (
                <div key={key} className="mt-1">
                  <span className="font-medium">{key}: </span>
                  <span className="text-xs">{typeof value === 'string' && value.length > 30 ? 
                    value.substring(0, 30) + '...' : 
                    String(value)}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  };

  return (
    <Card className="shadow-md">
      <HierarchicalHeader
        title={destination.name}
        level={2}
        isOpen={detailsSectionOpen}
        onToggle={() => setDetailsSectionOpen(!detailsSectionOpen)}
        icon={statusBadge()}
        actions={
          <>
            {/* Event trigger buttons - only show when scheduler is running and not on mobile */}
            {destination.isRunning && eventTriggers.length > 0 && !isMobile && (
              <div className="flex flex-wrap gap-1 mr-2">
                {eventTriggers.map(trigger => (
                  <Button
                    key={trigger}
                    size="sm"
                    variant="outline"
                    className="h-6 px-2 text-xs bg-amber-50 hover:bg-amber-100 border-amber-200"
                    onClick={() => handleThrowEvent(trigger)}
                    title={`Trigger "${trigger}" event`}
                  >
                    <Zap className="h-3 w-3 mr-1 text-amber-500" />
                    {trigger}
                  </Button>
                ))}
              </div>
            )}
            
            {/* Desktop buttons */}
            {!isMobile && (
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  onClick={() => onCreate(destination.id)}
                >
                  <Plus className="h-4 w-4 mr-2" />
                  Create
                </Button>
                {destination.isPaused ? (
                  <Button
                    size="sm"
                    variant="default"
                    onClick={() => onToggle(destination.id, true)}
                  >
                    <Play className="h-4 w-4 mr-2" />
                    Resume
                  </Button>
                ) : destination.isRunning ? (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => onToggle(destination.id, false)}
                  >
                    <Pause className="h-4 w-4 mr-2" />
                    Pause
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="default"
                    onClick={() => onStart(destination.id)}
                  >
                    <Play className="h-4 w-4 mr-2" />
                    Start
                  </Button>
                )}
                {(destination.isRunning || destination.isPaused) && (
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => onStop(destination.id)}
                  >
                    <X className="h-4 w-4 mr-2" />
                    Stop
                  </Button>
                )}
              </div>
            )}
            
            {/* Mobile action button */}
            {isMobile && (
              <div className="flex items-center gap-1">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    if ((window as any).fetchDestinations) {
                      (window as any).fetchDestinations();
                    }
                  }}
                  title="Refresh"
                >
                  <RefreshCcw className="h-4 w-4" />
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setMobileActionSheetOpen(true)}
                >
                  <Settings className="h-4 w-4" />
                  <span className="ml-1 hidden xs:inline">Actions</span>
                </Button>
              </div>
            )}
          </>
        }
      />
      {detailsSectionOpen && (
        <HierarchicalContent level={2}>
          {/* Next Scheduled Action */}
          {destination.isRunning && (
            <div className={`mb-4 p-3 bg-accent/30 rounded-md ${
              isMobile ? 'sticky top-0 z-10' : ''
            }`}>
              <h4 className="text-sm font-medium mb-1">Status</h4>
              {formatNextAction(schedulerStatus?.[destination.id]?.next_action || null)}
            </div>
          )}
        
          {/* Empty State Message */}
          {(!destination.scheduleStack || destination.scheduleStack.length === 0) && (
            <div className="text-center py-4 text-muted-foreground">
              <p>No schedules found for this destination.</p>
              <p className="text-sm mt-2">Click "Create" to add a schedule or "Start" to create a default schedule.</p>
            </div>
          )}
          
          {/* Schedule Stack - Only show if we have schedules */}
          {destination.scheduleStack && destination.scheduleStack.length > 0 && (
            <Card className="mb-4">
              <HierarchicalHeader
                title="Schedule Details"
                level={3}
                isOpen={scheduleDetailsSectionOpen}
                onToggle={() => setScheduleDetailsSectionOpen(!scheduleDetailsSectionOpen)}
                icon={<Eye className="h-4 w-4" />}
                actions={
                  <Button 
                    variant="outline" 
                    size="sm" 
                    onClick={() => onUnload(destination.id)}
                    className={isMobile ? "h-8 text-xs" : ""}
                  >
                    <ArrowDownToLine className="h-3 w-3 mr-1" />
                    {!isMobile && 'Unload'}
                    {isMobile && 'Unload'}
                  </Button>
                }
              />
              {scheduleDetailsSectionOpen && (
                <HierarchicalContent level={3}>
                  {destination.scheduleStack && destination.scheduleStack.length > 0 ? (
                    <div className="space-y-4">
                      {destination.scheduleStack.map((layer, index) => (
                        <div key={index} className="border rounded-lg p-3 bg-card">
                          <div className={`${isMobile ? 'flex flex-col gap-2' : 'flex justify-between items-center'} mb-2`}>
                            <h4 className="font-semibold text-sm">Layer {index + 1}</h4>
                            <div className="flex flex-wrap gap-2">
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => handleScriptEdit(index)}
                                className={isMobile ? "h-8 text-xs" : ""}
                              >
                                Edit
                              </Button>
                              <SetVarsButton
                                destinationId={destination.id}
                                contextVars={destination.contextStack && destination.contextStack[index] ? destination.contextStack[index].vars || {} : {}}
                                onVarsSaved={(updatedVars) => {
                                  if (destination.contextStack && destination.contextStack[index]) {
                                    destination.contextStack[index].vars = updatedVars;
                                  }
                                  if ((window as any).fetchDestinations) {
                                    (window as any).fetchDestinations();
                                  }
                                  toast({
                                    title: "Success",
                                    description: "Variable saved"
                                  });
                                }}
                              />
                            </div>
                          </div>
                          
                          {/* Tabs for Context and Script */}
                          <div className="border-b mb-3">
                            <div className="flex space-x-2">
                              <button
                                className={`pb-2 px-1 text-xs transition-colors relative ${
                                  activeTab === 'context' 
                                    ? 'font-medium text-primary' 
                                    : 'text-muted-foreground hover:text-foreground'
                                }`}
                                onClick={() => setActiveTab('context')}
                              >
                                Context
                                {activeTab === 'context' && (
                                  <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-primary" />
                                )}
                              </button>
                              <button
                                className={`pb-2 px-1 text-xs transition-colors relative ${
                                  activeTab === 'script' 
                                    ? 'font-medium text-primary' 
                                    : 'text-muted-foreground hover:text-foreground'
                                }`}
                                onClick={() => setActiveTab('script')}
                              >
                                Script
                                {activeTab === 'script' && (
                                  <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-primary" />
                                )}
                              </button>
                            </div>
                          </div>
                          
                          {/* Tab content */}
                          {activeTab === 'context' && (
                            <div className="mb-3">
                              {destination.contextStack && destination.contextStack[index] ? (
                                renderContextVariables(destination.contextStack[index])
                              ) : (
                                <p className="text-xs text-muted-foreground">No context available for this schedule layer</p>
                              )}
                            </div>
                          )}
                          
                          {activeTab === 'script' && (
                            <div className="overflow-auto max-w-full">
                              <pre className={`bg-muted p-2 rounded-md overflow-x-auto whitespace-pre-wrap break-words ${
                                isMobile ? 'text-[10px] leading-3 max-h-24' : 'text-xs max-h-32'
                              }`}>
                                {JSON.stringify(layer, null, 2)}
                              </pre>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-muted-foreground text-sm">No schedule stack found</p>
                  )}
                </HierarchicalContent>
              )}
            </Card>
          )}
          
          {/* Logs */}
          <Card className="mb-4">
            <HierarchicalHeader
              title="Logs"
              level={3}
              isOpen={logsSectionOpen}
              onToggle={() => setLogsSectionOpen(!logsSectionOpen)}
              icon={<FileText className="h-4 w-4" />}
              actions={
                logsSectionOpen && logUpdatesPaused && (
                  <Badge variant="outline" className="bg-yellow-100 text-xs">
                    Updates paused
                  </Badge>
                )
              }
            />
            {logsSectionOpen && (
              <HierarchicalContent level={3}>
                <div 
                  ref={logsContainerRef}
                  className={`bg-black text-green-400 p-3 rounded-lg font-mono overflow-auto ${
                    isMobile ? 'text-[10px] leading-3 max-h-32' : 'text-xs max-h-48'
                  }`}
                  onMouseUp={() => {
                    const selection = window.getSelection();
                    if (selection && selection.toString().length > 0) {
                      setLogUpdatesPaused(true);
                    } else {
                      setLogUpdatesPaused(false);
                    }
                  }}
                  onClick={(e) => {
                    if (window.getSelection()?.toString().length === 0) {
                      setLogUpdatesPaused(false);
                    }
                  }}
                >
                  {logs && logs.length > 0 ? (
                    logs.map((log, index) => (
                      <div key={index} className="break-all">{log}</div>
                    ))
                  ) : (
                    <p className="text-muted-foreground">No logs available</p>
                  )}
                </div>
              </HierarchicalContent>
            )}
          </Card>

          {/* Instruction Queue */}
          <Card>
            <HierarchicalHeader
              title="Instruction Queue"
              level={3}
              isOpen={showInstructionQueue || false}
              onToggle={onToggleQueueView}
              count={instructionQueue?.queue_size || 0}
              icon={<List className="h-4 w-4" />}
            />
            {showInstructionQueue && (
              <HierarchicalContent level={3}>
                {renderInstructionQueue()}
              </HierarchicalContent>
            )}
          </Card>
        </HierarchicalContent>
      )}
      
      {/* Mobile Action Sheet */}
      {isMobile && (
        <MobileActionSheet
          isOpen={mobileActionSheetOpen}
          onClose={() => setMobileActionSheetOpen(false)}
          title={`Actions for ${destination.name}`}
          actions={[
            {
              label: 'Create Schedule',
              icon: <Plus className="h-4 w-4" />,
              onClick: () => onCreate(destination.id),
            },
            ...(destination.isPaused ? [{
              label: 'Resume',
              icon: <Play className="h-4 w-4" />,
              onClick: () => onToggle(destination.id, true),
              variant: 'default' as const,
            }] : destination.isRunning ? [{
              label: 'Pause',
              icon: <Pause className="h-4 w-4" />,
              onClick: () => onToggle(destination.id, false),
              variant: 'outline' as const,
            }] : [{
              label: 'Start',
              icon: <Play className="h-4 w-4" />,
              onClick: () => onStart(destination.id),
              variant: 'default' as const,
            }]),
            ...((destination.isRunning || destination.isPaused) ? [{
              label: 'Stop',
              icon: <X className="h-4 w-4" />,
              onClick: () => onStop(destination.id),
              variant: 'destructive' as const,
            }] : []),
            ...(destination.isRunning && eventTriggers.length > 0 ? 
              eventTriggers.map(trigger => ({
                label: `Trigger "${trigger}" event`,
                icon: <Zap className="h-4 w-4" />,
                onClick: () => handleThrowEvent(trigger),
                variant: 'outline' as const,
              })) : []
            ),
          ]}
        />
      )}
    </Card>
  );
}; 
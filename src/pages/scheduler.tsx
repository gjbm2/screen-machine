import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/components/ui/use-toast';
import { Play, Pause, Plus, AlertCircle, RefreshCcw, ArrowDownToLine, X, Settings } from 'lucide-react';
import MainLayout from '@/components/layout/MainLayout';
import { useConsoleManagement } from '@/hooks/use-console-management';
import apiService from '@/utils/api';
import { getPublishDestinations } from '@/services/PublishService';
import { useNavigate } from 'react-router-dom';
import { SchemaEditModal } from '../components/scheduler/SchemaEditModal';
import { SetVarsButton } from '../components/scheduler/SetVarsButton';

interface Schedule {
  id: string;
  name: string;
  cron: string;
  is_running: boolean;
  is_paused?: boolean;
  last_run: string;
  next_run: string;
  error?: string;
}

interface Context {
  vars: Record<string, any>;
  [key: string]: any;
}

interface ScheduleStack {
  schedule: any;
  context: Context;
}

interface Destination {
  id: string;    // The destination ID used for API calls
  name: string;  // The display name shown in the UI
  schedules: Schedule[];
  isRunning?: boolean;
  isPaused?: boolean;
  logs?: string[];
  scheduleStack?: any[];
  contextStack?: Context[];  // Changed from Record<string, Context> to Context[]
}

interface NextAction {
  has_next_action: boolean;
  next_time: string | null;
  description: string | null;
  minutes_until_next: number | null;
  timestamp: string;
}

interface SchedulerStatus {
  is_running: boolean;
  next_action: NextAction | null;
}

const Scheduler = () => {
  const [destinations, setDestinations] = useState<Destination[]>([]);
  const [loading, setLoading] = useState(true);
  const { toast } = useToast();
  const navigate = useNavigate();
  const { 
    consoleVisible, 
    consoleLogs, 
    toggleConsole, 
    clearConsole,
    addLog 
  } = useConsoleManagement();
  
  // State for schema edit modal
  const [schemaEditModalOpen, setSchemaEditModalOpen] = useState(false);
  const [schemaEditData, setSchemaEditData] = useState<{
    destination: string;
    schema: any;
    initialData: any;
    saveEndpoint: string;
    saveMethod: string;
  } | null>(null);

  const [showAlert, setShowAlert] = useState(false);
  const [alertMessage, setAlertMessage] = useState('');
  const [alertSeverity, setAlertSeverity] = useState('success');
  const [localLogs, setLocalLogs] = useState<{[key: string]: string[]}>({});
  const [showLogs, setShowLogs] = useState<{[key: string]: boolean}>({});
  const [showSchedule, setShowSchedule] = useState<{[key: string]: boolean}>({});
  const [schedulerStatus, setSchedulerStatus] = useState<{[key: string]: SchedulerStatus}>({});

  const fetchDestinations = useCallback(async () => {
    setLoading(true);
    try {
      // Get list of running schedulers
      const response = await apiService.listSchedulers();
      const runningSchedulers = response.running || [];
      
      // Get publish destinations from the service
      const publishDestinations = getPublishDestinations();
      
      // Extract destination IDs from the publish destinations
      const destinationIds = publishDestinations.map(dest => dest.id);
      
      // Combine both lists to make sure we have all destinations
      const allDestinations = [...new Set([...destinationIds, ...runningSchedulers])];
      
      // Create empty destinations array to populate
      const enhancedDestinations: Destination[] = [];
      
      // Get all scheduler statuses in one batch request
      const allStatusesResponse = await apiService.getAllSchedulerStatuses();
      const allStatuses = allStatusesResponse.statuses || {};
      
      // Get details for each destination
      for (const destId of allDestinations) {
        try {
          // Find destination info from publish destinations if available
          const publishDestInfo = publishDestinations.find(d => d.id === destId);
          
          // Create destination object with default values
          const destination: Destination = {
            id: destId,
            name: publishDestInfo?.name || destId,
            schedules: [],
            isRunning: false, // Default to not running
            isPaused: false,
          };
          
          // Get status from the batch response
          const statusInfo = allStatuses[destId];
          if (statusInfo) {
            destination.isRunning = statusInfo.is_running || statusInfo.status === 'running' || statusInfo.status === 'paused';
            destination.isPaused = statusInfo.is_paused || statusInfo.status === 'paused';
          }
          
          // Check if this destination is in the running list
          if (runningSchedulers.includes(destId)) {
            // If it's running, get additional details
            try {
              // Get logs
              const logsResponse = await apiService.getSchedulerLogs(destId);
              destination.logs = logsResponse.log || [];
              
              // Get schedule stack
              try {
                const stackResponse = await apiService.getScheduleStack(destId);
                destination.scheduleStack = stackResponse.stack || [];
              } catch (stackError) {
                if (process.env.NODE_ENV === 'development') {
                  console.warn(`No schedule stack for running scheduler ${destId}`);
                }
                destination.scheduleStack = [];
              }
              
              // Get context
              try {
                const contextResponse = await apiService.getSchedulerContext(destId);
                console.log(`Context response for ${destId}:`, contextResponse);
                
                // Check if the response is a direct context object or contains a context_stack
                if (contextResponse.context_stack) {
                  // Use the context_stack if available
                  destination.contextStack = contextResponse.context_stack || [];
                } else if (contextResponse.vars || contextResponse.last_generated) {
                  // If it's a direct context object, create a single-item array
                  destination.contextStack = [contextResponse];
                  console.log(`Created context stack with direct context for ${destId}`, destination.contextStack);
                } else {
                  // Default to empty array
                  destination.contextStack = [];
                  console.warn(`Destination ${destId} has no usable context data`);
                }
                
                if (destination.contextStack && destination.contextStack.length > 0) {
                  console.log(`Destination ${destId} context[0]:`, destination.contextStack[0]);
                } else {
                  console.warn(`Destination ${destId} has empty contextStack`);
                }
              } catch (contextError) {
                if (process.env.NODE_ENV === 'development') {
                  console.warn(`No context for running scheduler ${destId}`);
                }
                destination.contextStack = [];
              }
              
              // Convert schedule stack items to individual schedules for the UI
              if (destination.scheduleStack && destination.scheduleStack.length > 0) {
                destination.schedules = destination.scheduleStack.map((schedule, index) => ({
                  id: `${destId}-schedule-${index}`,
                  name: schedule.name || `Schedule ${index + 1}`,
                  cron: schedule.cron || 'Unknown',
                  is_running: destination.isRunning && !destination.isPaused,
                  is_paused: destination.isPaused,
                  last_run: 'N/A',
                  next_run: 'N/A'
                }));
              }
            } catch (error) {
              console.error(`Error fetching details for running scheduler ${destId}:`, error);
            }
          } else {
            // If it's not running, we still want to show it in the UI
            destination.isRunning = false;
            destination.isPaused = false;
            
            // Even though it's not running, try to get the schedule stack
            try {
              const stackResponse = await apiService.getScheduleStack(destId);
              destination.scheduleStack = stackResponse.stack || [];
              
              // Try to get context as well
              try {
                const contextResponse = await apiService.getSchedulerContext(destId);
                if (contextResponse.context_stack) {
                  destination.contextStack = contextResponse.context_stack || [];
                } else if (contextResponse.vars || contextResponse.last_generated) {
                  destination.contextStack = [contextResponse];
                } else {
                  destination.contextStack = [];
                }
              } catch (contextError) {
                // Silently handle - context may not exist for stopped schedulers
                destination.contextStack = [];
              }
              
              // Convert schedule stack items to individual schedules for the UI
              if (destination.scheduleStack && destination.scheduleStack.length > 0) {
                destination.schedules = destination.scheduleStack.map((schedule, index) => ({
                  id: `${destId}-schedule-${index}`,
                  name: schedule.name || `Schedule ${index + 1}`,
                  cron: schedule.cron || 'Unknown',
                  is_running: false,
                  is_paused: false,
                  last_run: 'N/A',
                  next_run: 'N/A'
                }));
              }
            } catch (stackError) {
              // Silently handle - schedule may not exist for stopped schedulers
              destination.scheduleStack = [];
              destination.contextStack = [];
            }
          }
          
          enhancedDestinations.push(destination);
        } catch (error) {
          console.error(`Error processing destination ${destId}:`, error);
          // Add destination with basic info even if processing failed
          enhancedDestinations.push({
            id: destId,
            name: publishDestinations.find(d => d.id === destId)?.name || destId,
            schedules: [],
            isRunning: runningSchedulers.includes(destId)
          });
        }
      }
      
      setDestinations(enhancedDestinations);
      
      // Also update the scheduler status state from the batch response
      setSchedulerStatus(allStatuses);
      
      addLog({ type: 'info', message: 'Fetched scheduler data successfully' });
    } catch (error) {
      console.error('Error fetching schedulers:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch schedulers',
        variant: 'destructive',
        duration: 5000, // Auto-hide after 5 seconds
      });
      addLog({ type: 'error', message: 'Failed to fetch schedulers' });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    fetchDestinations();
  }, [fetchDestinations]);

  useEffect(() => {
    // Initialize local state for destinations
    const initialLogs = {};
    const initialShowLogs = {};
    const initialShowSchedule = {};
    const initialStatus = {};

    destinations.forEach(destination => {
      initialLogs[destination.id] = destination.logs || [];
      initialShowLogs[destination.id] = false;
      initialShowSchedule[destination.id] = false;
      initialStatus[destination.id] = {
        is_running: false,
        next_action: null
      };
    });

    setLocalLogs(initialLogs);
    setShowLogs(initialShowLogs);
    setShowSchedule(initialShowSchedule);
    setSchedulerStatus(initialStatus);
  }, [destinations]);

  useEffect(() => {
    // Set up polling for logs and status
    const pollingIntervalId = setInterval(() => {
      // Fetch logs for destinations with visible logs
      destinations.forEach(destination => {
        if (showLogs[destination.id]) {
          fetchLogs(destination.id);
        }
      });
      
      // Fetch all scheduler statuses in a single request
      fetchAllSchedulerStatuses();
    }, 15000); // Poll every 15 seconds

    return () => {
      clearInterval(pollingIntervalId);
    };
  }, [destinations, showLogs]);

  const fetchLogs = async (destinationId: string) => {
    try {
      const response = await apiService.getSchedulerLogs(destinationId);
      setLocalLogs(prevLogs => ({
        ...prevLogs,
        [destinationId]: response.log || []
      }));
    } catch (error) {
      console.error(`Error fetching logs for destination ${destinationId}:`, error);
    }
  };

  const fetchSchedulerStatus = async (destinationId: string) => {
    try {
      // First check if scheduler is running
      const runningResponse = await apiService.getDestinations();
      const destination = runningResponse.destinations.find(d => d.id === destinationId);
      const isRunning = destination?.scheduler_running || false;
      
      // Only log in debug mode
      if (process.env.NODE_ENV === 'development') {
        console.log(`Destination ${destinationId} running status:`, isRunning);
      }
      
      // Then get next action if scheduler is running
      let nextAction = null;
      if (isRunning) {
        try {
          if (process.env.NODE_ENV === 'development') {
            console.log(`Fetching next action for destination ${destinationId}`);
            const apiUrl = apiService.getApiUrl();
            console.log(`Using API URL: ${apiUrl}/schedulers/${destinationId}/next_action`);
          }
          
          const actionResponse = await apiService.getNextScheduledAction(destinationId);
          
          if (process.env.NODE_ENV === 'development') {
            console.log(`Next action response:`, actionResponse);
          }
          
          if (actionResponse && actionResponse.success !== false && actionResponse.next_action) {
            nextAction = actionResponse.next_action;
            if (process.env.NODE_ENV === 'development') {
              console.log(`Next action received:`, nextAction);
            }
          } else if (actionResponse && actionResponse.error) {
            console.warn(`Warning fetching next action: ${actionResponse.error}`);
          }
        } catch (actionError) {
          // Error already handled in the API service
          console.error(`Error fetching next action for destination ${destinationId}:`, actionError);
        }
      }
      
      setSchedulerStatus(prevStatus => ({
        ...prevStatus,
        [destinationId]: {
          is_running: isRunning,
          next_action: nextAction
        }
      }));
    } catch (error) {
      console.error(`Error fetching scheduler status for destination ${destinationId}:`, error);
      // Don't break the UI on error
      setSchedulerStatus(prevStatus => ({
        ...prevStatus,
        [destinationId]: {
          is_running: false,
          next_action: null
        }
      }));
    }
  };

  // New function to fetch all scheduler statuses at once
  const fetchAllSchedulerStatuses = async () => {
    try {
      const response = await apiService.getAllSchedulerStatuses();
      if (response && response.statuses) {
        setSchedulerStatus(response.statuses);
      }
    } catch (error) {
      console.error('Error fetching all scheduler statuses:', error);
    }
  };

  const handleToggleSchedule = async (destinationId: string, isRunning: boolean) => {
    try {
      // Save previous state
      const previousStatus = schedulerStatus[destinationId];
      
      // Update UI immediately for better UX
      setSchedulerStatus(prevStatus => ({
        ...prevStatus,
        [destinationId]: {
          ...prevStatus[destinationId],
          is_running: !isRunning // Toggle the running state
        }
      }));
      
      if (isRunning) {
        await apiService.pauseScheduler(destinationId);
      } else {
        await apiService.unpauseScheduler(destinationId);
      }
      
      // Refresh the data to ensure we have accurate state
      await fetchDestinations();
      
      // Immediately fetch updated status
      await fetchSchedulerStatus(destinationId);
      
      toast({
        title: 'Success',
        description: `Scheduler ${isRunning ? 'paused' : 'started'} successfully`,
        duration: 3000, // Auto-hide after 3 seconds
      });
    } catch (error) {
      console.error('Error toggling scheduler:', error);
      toast({
        title: 'Error',
        description: 'Failed to toggle scheduler',
        variant: 'destructive',
        duration: 5000, // Auto-hide after 5 seconds
      });
    }
  };

  const handleCreateSchedule = async (destinationId: string) => {
    try {
      // Get the schema
      console.log('SCHEDULER: Fetching schema from API');
      const schemaResponse = await apiService.getSchedulerSchema();
      console.log('SCHEDULER: Schema received (first 100 chars):', schemaResponse.substring(0, 100));
      console.log('SCHEDULER: Schema type:', typeof schemaResponse);
      console.log('SCHEDULER: Schema length:', schemaResponse.length);
      
      if (!schemaResponse) {
        throw new Error('Failed to get schema');
      }
      
      // Try parsing to see structure 
      try {
        const parsedSchema = JSON.parse(schemaResponse);
        console.log('SCHEDULER: First property in schema.properties:', Object.keys(parsedSchema.properties)[0]);
      } catch (e) {
        console.error('SCHEDULER: Error parsing schema:', e);
      }
      
      // Set up schema edit data
      setSchemaEditData({
        destination: destinationId,
        schema: schemaResponse,
        initialData: {},
        saveEndpoint: `${apiService.getApiUrl()}/schedulers/${destinationId}/schedule`,
        saveMethod: 'POST'
      });
      
      // Open the modal
      setSchemaEditModalOpen(true);
      
      toast({
        title: 'Info',
        description: 'Opening schedule editor...',
        duration: 3000, // Auto-hide after 3 seconds
      });
    } catch (error) {
      console.error('Error creating schedule:', error);
      toast({
        title: 'Error',
        description: 'Failed to create schedule',
        variant: 'destructive',
        duration: 5000, // Auto-hide after 5 seconds
      });
    }
  };

  const handleStartScheduler = async (destinationId: string) => {
    try {
      // Start the scheduler with an empty schedule
      await apiService.startScheduler(destinationId, {});
      
      // Refresh the data
      await fetchDestinations();
      
      toast({
        title: 'Success',
        description: `Started scheduler successfully`,
        duration: 3000, // Auto-hide after 3 seconds
      });
    } catch (error) {
      console.error('Error starting scheduler:', error);
      toast({
        title: 'Error',
        description: 'Failed to start scheduler',
        variant: 'destructive',
        duration: 5000, // Auto-hide after 5 seconds
      });
    }
  };

  const handleStopScheduler = async (destinationId: string) => {
    try {
      // Stop the scheduler
      await apiService.stopScheduler(destinationId);
      
      // Refresh the data
      await fetchDestinations();
      
      toast({
        title: 'Success',
        description: `Stopped scheduler successfully`,
        duration: 3000, // Auto-hide after 3 seconds
      });
    } catch (error) {
      console.error('Error stopping scheduler:', error);
      toast({
        title: 'Error',
        description: 'Failed to stop scheduler',
        variant: 'destructive',
        duration: 5000, // Auto-hide after 5 seconds
      });
    }
  };

  const handleUnloadSchedule = async (destinationId: string) => {
    try {
      // Unload the top schedule
      await apiService.unloadSchedule(destinationId);
      
      // Refresh the data
      await fetchDestinations();
      
      toast({
        title: 'Success',
        description: `Unloaded top schedule successfully`,
        duration: 3000, // Auto-hide after 3 seconds
      });
    } catch (error) {
      console.error('Error unloading schedule:', error);
      toast({
        title: 'Error',
        description: 'Failed to unload schedule',
        variant: 'destructive',
        duration: 5000, // Auto-hide after 5 seconds
      });
    }
  };

  const handleEditSchedule = async (destinationId: string, layer: number) => {
    try {
      // Get the schedule at the specified position
      const response = await apiService.getScheduleAtPosition(destinationId, layer);
      
      if (!response.schedule) {
        throw new Error('Failed to get schedule');
      }
      
      // Get the schema
      const schemaResponse = await apiService.getSchedulerSchema();
      
      if (!schemaResponse) {
        throw new Error('Failed to get schema');
      }
      
      // Set up schema edit data
      setSchemaEditData({
        destination: destinationId,
        schema: schemaResponse,
        initialData: response.schedule,
        saveEndpoint: `${apiService.getApiUrl()}/schedulers/${destinationId}/schedule/${layer}`,
        saveMethod: 'PUT'
      });
      
      // Open the modal
      setSchemaEditModalOpen(true);
      
      toast({
        title: 'Info',
        description: 'Opening schedule editor...',
        duration: 3000, // Auto-hide after 3 seconds
      });
    } catch (error) {
      console.error('Error editing schedule:', error);
      toast({
        title: 'Error',
        description: 'Failed to get schedule for editing',
        variant: 'destructive',
        duration: 5000, // Auto-hide after 5 seconds
      });
    }
  };
  
  const handleSchemaEditSave = () => {
    // Get the current destination from schemaEditData
    const destinationId = schemaEditData?.destination;
    
    // Refresh the data after successful save
    fetchDestinations().then(() => {
      // Auto-start the scheduler if it's a new schedule (POST method)
      if (destinationId && schemaEditData?.saveMethod === 'POST') {
        console.log(`Auto-starting scheduler for ${destinationId} after creating new schedule`);
        handleStartScheduler(destinationId);
        
        toast({
          title: 'Success',
          description: 'Schedule saved and started automatically',
          duration: 3000, // Auto-hide after 3 seconds
        });
      } else {
        toast({
          title: 'Success',
          description: 'Schedule saved successfully',
          duration: 3000, // Auto-hide after 3 seconds
        });
      }
    });
  };

  if (loading) {
    return (
      <MainLayout
        onToggleConsole={toggleConsole}
        consoleVisible={consoleVisible}
        onOpenAdvancedOptions={() => {}}
        consoleLogs={consoleLogs}
        onClearConsole={clearConsole}
      >
        <div className="flex items-center justify-center h-full">
          <p>Loading schedulers...</p>
        </div>
      </MainLayout>
    );
  }

  // Sort destinations: running schedulers first
  const sortedDestinations = [...destinations].sort((a, b) => {
    if (a.isRunning && !b.isRunning) return -1;
    if (!a.isRunning && b.isRunning) return 1;
    return 0;
  });

  return (
    <MainLayout
      onToggleConsole={toggleConsole}
      consoleVisible={consoleVisible}
      onOpenAdvancedOptions={() => {}}
      consoleLogs={consoleLogs}
      onClearConsole={clearConsole}
    >
      <div className="space-y-6">
        <div className="flex justify-between items-center">
          <h1 className="text-2xl font-bold">Scheduler Control Panel</h1>
          <Button onClick={fetchDestinations} variant="outline" size="sm">
            <RefreshCcw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
        
        <div className="grid grid-cols-1 gap-6">
          {/* Running Schedulers */}
          <Card>
            <CardHeader>
              <CardTitle>Running Schedulers</CardTitle>
            </CardHeader>
            <CardContent>
              {sortedDestinations.filter(d => d.isRunning).length === 0 ? (
                <p className="text-muted-foreground">No running schedulers</p>
              ) : (
                <div className="space-y-6">
                  {sortedDestinations.filter(d => d.isRunning).map((destination) => (
                    <SchedulerCard 
                      key={destination.name} 
                      destination={destination} 
                      onToggle={handleToggleSchedule}
                      onCreate={handleCreateSchedule}
                      onStart={handleStartScheduler}
                      onStop={handleStopScheduler}
                      onUnload={handleUnloadSchedule}
                      onEdit={handleEditSchedule}
                      schedulerStatus={schedulerStatus}
                    />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
          
          {/* Stopped Schedulers */}
          <Card>
            <CardHeader>
              <CardTitle>Stopped Schedulers</CardTitle>
            </CardHeader>
            <CardContent>
              {sortedDestinations.filter(d => !d.isRunning).length === 0 ? (
                <p className="text-muted-foreground">No stopped schedulers</p>
              ) : (
                <div className="space-y-6">
                  {sortedDestinations.filter(d => !d.isRunning).map((destination) => (
                    <SchedulerCard 
                      key={destination.name} 
                      destination={destination} 
                      onToggle={handleToggleSchedule}
                      onCreate={handleCreateSchedule}
                      onStart={handleStartScheduler}
                      onStop={handleStopScheduler}
                      onUnload={handleUnloadSchedule}
                      onEdit={handleEditSchedule}
                      schedulerStatus={schedulerStatus}
                    />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
      
      {/* Schema edit modal */}
      {schemaEditData && (
        <SchemaEditModal
          open={schemaEditModalOpen}
          onOpenChange={setSchemaEditModalOpen}
          destination={schemaEditData.destination}
          schema={schemaEditData.schema}
          initialData={schemaEditData.initialData}
          saveEndpoint={schemaEditData.saveEndpoint}
          saveMethod={schemaEditData.saveMethod}
          onSave={handleSchemaEditSave}
        />
      )}
    </MainLayout>
  );
};

interface SchedulerCardProps {
  destination: Destination;
  onToggle: (destinationId: string, isRunning: boolean) => Promise<void>;
  onCreate: (destinationId: string) => Promise<void>;
  onStart: (destinationId: string) => Promise<void>;
  onStop: (destinationId: string) => Promise<void>;
  onUnload: (destinationId: string) => Promise<void>;
  onEdit: (destinationId: string, layer: number) => Promise<void>;
  schedulerStatus?: Record<string, SchedulerStatus>;
}

const SchedulerCard: React.FC<SchedulerCardProps> = ({ 
  destination, 
  onToggle, 
  onCreate, 
  onStart, 
  onStop, 
  onUnload,
  onEdit,
  schedulerStatus
}) => {
  const [showLogs, setShowLogs] = useState(false);
  const [showSchedule, setShowSchedule] = useState(false);
  const [activeTab, setActiveTab] = useState<'context' | 'script'>('context');
  const [logs, setLogs] = useState<string[]>(destination.logs || []);
  const logsContainerRef = useRef<HTMLDivElement>(null);
  const { toast } = useToast();
  
  // Debug logs
  useEffect(() => {
    console.log(`SchedulerCard for ${destination.name} (${destination.id}):`);
    console.log(`- isRunning: ${destination.isRunning}`);
    console.log(`- isPaused: ${destination.isPaused}`);
    console.log(`- Has scheduleStack: ${destination.scheduleStack ? destination.scheduleStack.length : 0} items`);
    console.log(`- Has contextStack: ${destination.contextStack ? destination.contextStack.length : 0} items`);
    if (destination.contextStack && destination.contextStack.length > 0) {
      console.log(`- First context:`, destination.contextStack[0]);
    }
  }, [destination]);
  
  // Set up polling for logs when they're visible
  useEffect(() => {
    // Don't poll if logs aren't visible
    if (!showLogs) return;
    
    // Function to fetch the latest logs
    const fetchLogs = async () => {
      try {
        const response = await apiService.getSchedulerLogs(destination.id);
        if (response && response.log) {
          setLogs(response.log);
        }
      } catch (error) {
        console.error(`Error fetching logs for ${destination.id}:`, error);
      }
    };
    
    // Fetch logs immediately when becoming visible
    fetchLogs();
    
    // Set up polling interval (15 seconds)
    const intervalId = setInterval(fetchLogs, 15000);
    
    // Clean up interval when component unmounts or logs hidden
    return () => {
      clearInterval(intervalId);
    };
  }, [showLogs, destination.id]); // Re-run effect when showLogs changes
  
  // When destination.logs updates from parent (e.g., on manual refresh), update our local state
  useEffect(() => {
    setLogs(destination.logs || []);
  }, [destination.logs]);
  
  // Scroll logs to bottom whenever they change or become visible
  useEffect(() => {
    if (showLogs && logsContainerRef.current) {
      // Use requestAnimationFrame to ensure DOM has updated before scrolling
      requestAnimationFrame(() => {
        if (logsContainerRef.current) {
          logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight;
        }
      });
    }
  }, [logs, showLogs]);
  
  // Display status more prominently
  const statusBadge = () => {
    if (destination.isPaused) {
      return (
        <Badge variant="outline" className="px-3 py-1 bg-amber-100">
          Paused
        </Badge>
      );
    } else if (destination.isRunning) {
      return (
        <Badge variant="default" className="px-3 py-1">
          Running
        </Badge>
      );
    } else {
      return (
        <Badge variant="secondary" className="px-3 py-1">
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
            {nextAction.minutes_until_next < 60 
              ? `${nextAction.minutes_until_next} minutes from now`
              : `${Math.floor(nextAction.minutes_until_next / 60)}h ${nextAction.minutes_until_next % 60}m from now`}
          </p>
        )}
      </div>
    );
  };

  // Render context variables
  const renderContextVariables = (context: any) => {
    console.log("Rendering context:", context);
    
    if (!context) {
      console.warn("Context is undefined or null");
      return <p className="text-sm text-muted-foreground">No context available</p>;
    }
    
    if (!context.vars) {
      console.warn("Context has no vars property:", context);
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
                  <span className="font-semibold mr-2">{key}:</span>
                  <span className="text-muted-foreground whitespace-pre-wrap break-all">
                    {typeof value === 'object' 
                      ? JSON.stringify(value, null, 2)
                      : String(value)
                    }
                  </span>
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
  
  return (
    <Card className="shadow-md">
      <CardHeader className="bg-muted/30">
        <CardTitle className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div className="flex items-center space-x-2">
            <span>{destination.name}</span>
            {statusBadge()}
          </div>
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
                onClick={() => onToggle(destination.id, false)}
              >
                <Play className="h-4 w-4 mr-2" />
                Resume
              </Button>
            ) : destination.isRunning ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => onToggle(destination.id, true)}
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
        </CardTitle>
      </CardHeader>
      <CardContent className="mt-4">
        {/* Next Scheduled Action */}
        {destination.isRunning && (
          <div className="mb-4 p-3 bg-accent/30 rounded-md">
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
          <div className="mb-6">
            <div className="flex justify-between items-center mb-2">
              <Button 
                variant="outline" 
                size="sm" 
                onClick={() => setShowSchedule(!showSchedule)}
              >
                {showSchedule ? 'Hide details' : 'Show details'}
              </Button>
              {showSchedule && destination.scheduleStack && destination.scheduleStack.length > 0 && (
                <Button 
                  variant="outline" 
                  size="sm" 
                  onClick={() => onUnload(destination.id)}
                >
                  <ArrowDownToLine className="h-4 w-4 mr-2" />
                  Unload Top Schedule
                </Button>
              )}
            </div>
            
            {showSchedule && (
              <div className="bg-muted/30 p-4 rounded-lg">
                {destination.scheduleStack && destination.scheduleStack.length > 0 ? (
                  <div className="space-y-4">
                    {destination.scheduleStack.map((layer, index) => (
                      <div key={index} className="border rounded-lg p-4 bg-card">
                        <div className="flex justify-between items-center mb-2">
                          <h4 className="font-semibold">Layer {index + 1}</h4>
                          <div className="flex flex-wrap gap-2">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => onEdit(destination.id, index)}
                            >
                              Edit
                            </Button>
                            <SetVarsButton
                              destinationId={destination.id}
                              contextVars={destination.contextStack && destination.contextStack[index] ? destination.contextStack[index].vars || {} : {}}
                              onVarsSaved={() => {
                                toast({
                                  title: "Variable saved",
                                  description: "Context updated successfully. Refresh to see all changes.",
                                });
                              }}
                            />
                          </div>
                        </div>
                        
                        {/* Tabs for Context and Script */}
                        <div className="border-b mb-4">
                          <div className="flex space-x-2">
                            <button
                              className={`pb-2 px-1 text-sm transition-colors relative ${
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
                              className={`pb-2 px-1 text-sm transition-colors relative ${
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
                          <div className="mb-4">
                            {destination.contextStack && destination.contextStack[index] ? (
                              renderContextVariables(destination.contextStack[index])
                            ) : (
                              <p className="text-sm text-muted-foreground">No context available for this schedule layer</p>
                            )}
                          </div>
                        )}
                        
                        {activeTab === 'script' && (
                          <pre className="text-xs bg-muted p-2 rounded-md overflow-auto max-h-40">
                            {JSON.stringify(layer, null, 2)}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-muted-foreground">No schedule stack found</p>
                )}
              </div>
            )}
          </div>
        )}
        
        {/* Logs - Always show the option to view logs */}
        <div>
          <Button 
            variant="outline" 
            size="sm" 
            onClick={() => setShowLogs(!showLogs)}
            className="mb-2"
          >
            {showLogs ? 'Hide Logs' : 'Show Logs'}
          </Button>
          
          {showLogs && (
            <div 
              ref={logsContainerRef}
              className="bg-black text-green-400 p-4 rounded-lg font-mono text-xs overflow-auto max-h-60"
            >
              {logs && logs.length > 0 ? (
                logs.map((log, index) => (
                  <div key={index}>{log}</div>
                ))
              ) : (
                <p>No logs available</p>
              )}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
};

export default Scheduler; 
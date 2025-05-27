import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/components/ui/use-toast';
import { 
  Play, 
  Pause, 
  Plus, 
  AlertCircle, 
  RefreshCcw, 
  ArrowDownToLine, 
  X, 
  Settings,
  ChevronDown,
  ChevronRight,
  Zap,
  List
} from 'lucide-react';
import MainLayout from '@/components/layout/MainLayout';
import { useConsoleManagement } from '@/hooks/use-console-management';
import { useIsMobile } from '@/hooks/useIsMobile';
import apiService from '@/utils/api';
import { useNavigate } from 'react-router-dom';
import { SchemaEditModal } from '../components/scheduler/SchemaEditModal';
import { SetVarsButton } from '../components/scheduler/SetVarsButton';
import { VarsRegistryCard } from '../components/scheduler/VarsRegistryCard';
import { SchedulerEventsPanel } from '../components/scheduler/SchedulerEventsPanel';
import { extractEventTriggers } from '@/utils/scheduleUtils';
import { MobileActionSheet } from '@/components/ui/MobileActionSheet';
import { SchedulerCard } from '../components/scheduler/SchedulerCard';
import { CollapsibleCardHeader } from '../components/scheduler/CollapsibleCardHeader';
import { HierarchicalHeader } from '../components/scheduler/HierarchicalHeader';
import { HierarchicalContent } from '../components/scheduler/HierarchicalContent';
import { 
  Destination, 
  SchedulerStatus, 
  InstructionQueueResponse,
  NextAction
} from '@/types/scheduler';

// Extend Window interface to include fetchDestinations
declare global {
  interface Window {
    fetchDestinations?: () => Promise<void>;
  }
}

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

interface InstructionQueueItem {
  action: string;
  important: boolean;
  urgent: boolean;
  details: Record<string, any>;
}

const Scheduler = () => {
  const [destinations, setDestinations] = useState<Destination[]>([]);
  const [loading, setLoading] = useState(true);
  const { toast } = useToast();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
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

  // Add state for instruction queues
  const [instructionQueues, setInstructionQueues] = useState<{[key: string]: InstructionQueueResponse}>({});
  const [showInstructionQueue, setShowInstructionQueue] = useState<{[key: string]: boolean}>({});

  const [varsRegistryVisible, setVarsRegistryVisible] = useState(false);
  const [runningSectionVisible, setRunningSectionVisible] = useState(true);
  const [pausedSectionVisible, setPausedSectionVisible] = useState(false);
  const [stoppedSectionVisible, setStoppedSectionVisible] = useState(false);
  const [eventsSectionVisible, setEventsSectionVisible] = useState(false);
  const [eventCount, setEventCount] = useState(0);

  const [mobileActionSheetOpen, setMobileActionSheetOpen] = useState(false);

  const fetchDestinations = useCallback(async () => {
    setLoading(true);
    try {
      // Get list of running schedulers
      const response = await apiService.listSchedulers();
      const runningSchedulers = response.running || [];
      
      // Get publish destinations from the service
      const publishDestinations = await apiService.getPublishDestinations(true);
      
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
            console.log(`Processing scheduler status for ${destId}:`, statusInfo);
            
            // SIMPLE LOGIC: If status is paused OR is_paused is true -> it's paused
            if (statusInfo.status === 'paused' || statusInfo.is_paused === true) {
              destination.isPaused = true;
              // We intentionally don't set isRunning here
            } else if (statusInfo.status === 'running' || statusInfo.is_running === true) {
              destination.isRunning = true;
              destination.isPaused = false;
            } else {
              // Status is stopped or unknown
              destination.isRunning = false;
              destination.isPaused = false;
            }
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
                if (contextResponse.context_stack) {
                  destination.contextStack = contextResponse.context_stack || [];
                } else if (contextResponse.vars || contextResponse.last_generated) {
                  destination.contextStack = [contextResponse];
                } else {
                  destination.contextStack = [];
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
            // If it's not running AND it's not already marked as paused 
            // (don't override the paused state from the statusInfo)
            if (!destination.isPaused) {
              destination.isRunning = false;
              // Only set isPaused=false if it's not already true
              destination.isPaused = false;
            }
            
            // Even though it's not running or is paused, try to get the schedule stack
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
      });
      addLog({ type: 'error', message: 'Failed to fetch schedulers' });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  // Make fetchDestinations available in component scope
  (window as any).fetchDestinations = fetchDestinations;

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
    }, 5000); // Poll every 5 seconds (changed from 15 seconds)

    return () => {
      clearInterval(pollingIntervalId);
    };
  }, [destinations, showLogs]);

  const fetchLogs = async (destinationId: string) => {
    try {
      const logs = await apiService.getSchedulerLogs(destinationId);
      setLocalLogs(prevLogs => ({
        ...prevLogs,
        [destinationId]: logs.log || []
      }));
    } catch (error) {
      console.error('Error fetching logs:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch logs',
        variant: 'destructive',
      });
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
      toast({
        title: 'Error',
        description: 'Failed to fetch scheduler statuses',
        variant: 'destructive',
      });
    }
  };

  const handleToggleSchedule = async (destinationId: string, currentlyPaused: boolean) => {
    try {
      // Save previous state
      const previousStatus = schedulerStatus[destinationId];
      
      // Update UI immediately for better UX
      setSchedulerStatus(prevStatus => ({
        ...prevStatus,
        [destinationId]: {
          ...prevStatus[destinationId],
          is_running: !prevStatus[destinationId]?.is_running || false,
          is_paused: !currentlyPaused // Toggle paused state
        }
      }));
      
      // If currently paused, unpause it
      if (currentlyPaused) {
        await apiService.unpauseScheduler(destinationId);
      } else {
        // Otherwise pause it
        await apiService.pauseScheduler(destinationId);
      }
      
      // Refresh the data to ensure we have accurate state
      await fetchDestinations();
      
      // Immediately fetch updated status
      await fetchSchedulerStatus(destinationId);
      
      toast({
        title: 'Success',
        description: `Scheduler ${currentlyPaused ? 'resumed' : 'paused'} successfully`,
      });
    } catch (error) {
      console.error('Error toggling scheduler:', error);
      toast({
        title: 'Error',
        description: 'Failed to toggle scheduler',
        variant: 'destructive',
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
      });
    } catch (error) {
      console.error('Error creating schedule:', error);
      toast({
        title: 'Error',
        description: 'Failed to create schedule',
        variant: 'destructive',
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
      });
    } catch (error) {
      console.error('Error starting scheduler:', error);
      toast({
        title: 'Error',
        description: 'Failed to start scheduler',
        variant: 'destructive',
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
      });
    } catch (error) {
      console.error('Error stopping scheduler:', error);
      toast({
        title: 'Error',
        description: 'Failed to stop scheduler',
        variant: 'destructive',
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
      });
    } catch (error) {
      console.error('Error unloading schedule:', error);
      toast({
        title: 'Error',
        description: 'Failed to unload schedule',
        variant: 'destructive',
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
      });
    } catch (error) {
      console.error('Error editing schedule:', error);
      toast({
        title: 'Error',
        description: 'Failed to get schedule for editing',
        variant: 'destructive',
      });
    }
  };
  
  const handleSchemaEditSave = async () => {
    // Get the current destination from schemaEditData
    const destinationId = schemaEditData?.destination;
    
    if (!destinationId) {
      console.error('No destination ID found for schema edit');
      toast({
        title: 'Error',
        description: 'Failed to save schedule: Missing destination ID',
        variant: 'destructive',
      });
      return;
    }
    
    try {
      // First refresh the data to ensure we have the latest state
      await fetchDestinations();
      
      // Wait a short moment to ensure the schedule is saved
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      // Auto-start the scheduler if it's a new schedule (POST method)
      if (schemaEditData?.saveMethod === 'POST') {
        console.log(`Auto-starting scheduler for ${destinationId} after creating new schedule`);
        try {
          await handleStartScheduler(destinationId);
          toast({
            title: 'Success',
            description: 'Schedule saved and started automatically',
          });
        } catch (error) {
          console.error('Error starting scheduler:', error);
          toast({
            title: 'Warning',
            description: 'Schedule was saved but could not be started automatically. Try starting it manually.',
            variant: 'destructive',
          });
        }
      } else {
        // For existing schedules, ensure the state is properly updated
        const destination = destinations.find(d => d.id === destinationId);
        if (destination?.isRunning) {
          // If the scheduler is running, fetch its status to update UI
          await fetchSchedulerStatus(destinationId);
        }
        
        toast({
          title: 'Success',
          description: 'Schedule saved successfully',
        });
      }
    } catch (error) {
      console.error('Error updating state after schema edit:', error);
      toast({
        title: 'Warning',
        description: 'There was an error updating the UI. Please refresh the page.',
        variant: 'destructive',
      });
    }
  };

  // Add function to fetch instruction queue for a destination
  const fetchInstructionQueue = async (destinationId: string) => {
    try {
      const response = await apiService.getInstructionQueue(destinationId);
      setInstructionQueues(prevQueues => ({
        ...prevQueues,
        [destinationId]: response
      }));
    } catch (error) {
      console.error(`Error fetching instruction queue for destination ${destinationId}:`, error);
      // Don't break the UI on error
    }
  };

  // Modify the refreshInterval to also fetch instruction queues
  useEffect(() => {
    if (!loading) {
      const interval = setInterval(() => {
        destinations.forEach(destination => {
          if (destination.isRunning) {
            // Fetch status for running destinations
            fetchSchedulerStatus(destination.id);
            // Also fetch instruction queue
            fetchInstructionQueue(destination.id);
          }
        });
      }, 5000); // Refresh every 5 seconds
      
      return () => clearInterval(interval);
    }
  }, [loading, destinations]);

  // Initial fetch of instruction queues for running destinations
  useEffect(() => {
    destinations.forEach(destination => {
      if (destination.isRunning) {
        fetchInstructionQueue(destination.id);
      }
    });
  }, [destinations]);

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

  // Sort destinations: running schedulers first, then paused, then stopped
  const sortedDestinations = [...destinations].sort((a, b) => {
    // First running schedulers (not paused)
    if (a.isRunning === true && a.isPaused !== true && (b.isRunning !== true || b.isPaused === true)) {
      return -1;
    }
    if (b.isRunning === true && b.isPaused !== true && (a.isRunning !== true || a.isPaused === true)) {
      return 1;
    }
    
    // Then paused schedulers
    if (a.isPaused === true && b.isPaused !== true) {
      return -1;
    }
    if (b.isPaused === true && a.isPaused !== true) {
      return 1;
    }
    
    // Alphabetically within the same category
    return a.name.localeCompare(b.name);
  });

  // Group destinations by status
  const groupedDestinations = {
    running: sortedDestinations.filter((d) => d.isRunning && !d.isPaused),
    paused: sortedDestinations.filter((d) => d.isPaused),
    stopped: sortedDestinations.filter((d) => !d.isRunning && !d.isPaused)
  };

  return (
    <MainLayout
      onToggleConsole={toggleConsole}
      consoleVisible={consoleVisible}
      onOpenAdvancedOptions={() => {}}
      consoleLogs={consoleLogs}
      onClearConsole={clearConsole}
    >
      <div className={isMobile ? "-mx-4" : ""}>
        <div className={`mx-auto ${
          isMobile 
            ? 'px-1 pb-20 max-w-full space-y-2' 
            : 'px-4 pb-24 max-w-7xl space-y-4'
        }`}>
          {/* Page Header with Refresh Button */}
          <div className="flex justify-between items-center mb-4 px-3">
            <h1 className="text-2xl sm:text-3xl font-bold">Schedulers</h1>
            <Button 
              variant="outline" 
              onClick={fetchDestinations} 
              disabled={loading}
              size={isMobile ? "sm" : "default"}
            >
              <RefreshCcw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              {!isMobile && 'Refresh'}
            </Button>
          </div>

          {/* Vars Registry Section */}
          <Card>
            <HierarchicalHeader
              title="Variables Registry"
              level={1}
              isOpen={varsRegistryVisible}
              onToggle={() => setVarsRegistryVisible(!varsRegistryVisible)}
            />
            {varsRegistryVisible && (
              <HierarchicalContent level={1}>
                <VarsRegistryCard />
              </HierarchicalContent>
            )}
          </Card>

          {/* Events Section */}
          <Card>
            <HierarchicalHeader
              title="All Scheduler Events"
              level={1}
              isOpen={eventsSectionVisible}
              onToggle={() => setEventsSectionVisible(!eventsSectionVisible)}
              count={eventCount}
            />
            {eventsSectionVisible && (
              <HierarchicalContent level={1}>
                <SchedulerEventsPanel onEventCountChange={setEventCount} />
              </HierarchicalContent>
            )}
          </Card>

          {/* Running Schedulers Section */}
          <Card>
            <HierarchicalHeader
              title="Running Schedulers"
              level={1}
              isOpen={runningSectionVisible}
              onToggle={() => setRunningSectionVisible(!runningSectionVisible)}
              count={destinations.filter(d => d.isRunning && !d.isPaused).length}
            />
            {runningSectionVisible && (
              <HierarchicalContent level={1}>
                <div className={`grid ${isMobile ? 'gap-2' : 'gap-4'}`}>
                  {destinations
                    .filter(d => d.isRunning && !d.isPaused)
                    .map(destination => (
                      <SchedulerCard
                        key={destination.id}
                        destination={destination}
                        onToggle={handleToggleSchedule}
                        onCreate={handleCreateSchedule}
                        onStart={handleStartScheduler}
                        onStop={handleStopScheduler}
                        onUnload={handleUnloadSchedule}
                        onEdit={handleEditSchedule}
                        schedulerStatus={schedulerStatus}
                        instructionQueue={instructionQueues[destination.id]}
                        onToggleQueueView={() => {
                          setShowInstructionQueue(prev => ({
                            ...prev,
                            [destination.id]: !prev[destination.id]
                          }));
                        }}
                        showInstructionQueue={showInstructionQueue[destination.id] || false}
                        isMobile={isMobile}
                      />
                    ))}
                </div>
              </HierarchicalContent>
            )}
          </Card>

          {/* Paused Schedulers Section */}
          <Card>
            <HierarchicalHeader
              title="Paused Schedulers"
              level={1}
              isOpen={pausedSectionVisible}
              onToggle={() => setPausedSectionVisible(!pausedSectionVisible)}
              count={destinations.filter(d => d.isPaused).length}
            />
            {pausedSectionVisible && (
              <HierarchicalContent level={1}>
                <div className={`grid ${isMobile ? 'gap-2' : 'gap-4'}`}>
                  {destinations
                    .filter(d => d.isPaused)
                    .map(destination => (
                      <SchedulerCard
                        key={destination.id}
                        destination={destination}
                        onToggle={handleToggleSchedule}
                        onCreate={handleCreateSchedule}
                        onStart={handleStartScheduler}
                        onStop={handleStopScheduler}
                        onUnload={handleUnloadSchedule}
                        onEdit={handleEditSchedule}
                        schedulerStatus={schedulerStatus}
                        instructionQueue={instructionQueues[destination.id]}
                        onToggleQueueView={() => {
                          setShowInstructionQueue(prev => ({
                            ...prev,
                            [destination.id]: !prev[destination.id]
                          }));
                        }}
                        showInstructionQueue={showInstructionQueue[destination.id] || false}
                        isMobile={isMobile}
                      />
                    ))}
                </div>
              </HierarchicalContent>
            )}
          </Card>

          {/* Stopped Schedulers Section */}
          <Card>
            <HierarchicalHeader
              title="Stopped Schedulers"
              level={1}
              isOpen={stoppedSectionVisible}
              onToggle={() => setStoppedSectionVisible(!stoppedSectionVisible)}
              count={destinations.filter(d => !d.isRunning && !d.isPaused).length}
            />
            {stoppedSectionVisible && (
              <HierarchicalContent level={1}>
                <div className={`grid ${isMobile ? 'gap-2' : 'gap-4'}`}>
                  {destinations
                    .filter(d => !d.isRunning && !d.isPaused)
                    .map(destination => (
                      <SchedulerCard
                        key={destination.id}
                        destination={destination}
                        onToggle={handleToggleSchedule}
                        onCreate={handleCreateSchedule}
                        onStart={handleStartScheduler}
                        onStop={handleStopScheduler}
                        onUnload={handleUnloadSchedule}
                        onEdit={handleEditSchedule}
                        schedulerStatus={schedulerStatus}
                        instructionQueue={instructionQueues[destination.id]}
                        onToggleQueueView={() => {
                          setShowInstructionQueue(prev => ({
                            ...prev,
                            [destination.id]: !prev[destination.id]
                          }));
                        }}
                        showInstructionQueue={showInstructionQueue[destination.id] || false}
                        isMobile={isMobile}
                      />
                    ))}
                </div>
              </HierarchicalContent>
            )}
          </Card>

          {/* Schema Edit Modal */}
          {schemaEditModalOpen && schemaEditData && (
            <SchemaEditModal
              open={schemaEditModalOpen}
              onOpenChange={setSchemaEditModalOpen}
              onSave={handleSchemaEditSave}
              destination={schemaEditData.destination}
              schema={schemaEditData.schema}
              initialData={schemaEditData.initialData}
              saveEndpoint={schemaEditData.saveEndpoint}
              saveMethod={schemaEditData.saveMethod}
              scriptsDirectory="routes/scheduler/scripts"
            />
          )}
        </div>
      </div>
    </MainLayout>
  );
};

export default Scheduler; 
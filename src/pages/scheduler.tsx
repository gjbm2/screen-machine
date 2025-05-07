import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/components/ui/use-toast';
import { Play, Pause, Plus, AlertCircle, RefreshCcw, ArrowDownToLine, X, Settings, StopCircle, Edit } from 'lucide-react';
import MainLayout from '@/components/layout/MainLayout';
import { useConsoleManagement } from '@/hooks/use-console-management';
import apiService from '@/utils/api';
import { useNavigate } from 'react-router-dom';
import { SchemaEditModal } from '../components/scheduler/SchemaEditModal';
import { SetVarsButton } from '../components/scheduler/SetVarsButton';
import { VarsRegistryCard } from '../components/scheduler/VarsRegistryCard';

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
  time_until_display?: string;
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
      const publishDestinations = await apiService.getPublishDestinations();
      
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
            isPaused: false,  // Default to not paused
          };
          
          // Get status from the batch response
          const statusInfo = allStatuses[destId];
          if (statusInfo) {
            // Handle paused state first
            destination.isPaused = statusInfo.is_paused || statusInfo.status === 'paused';
            
            // A scheduler is considered "running" for UI purposes if it's either actually running or paused
            destination.isRunning = statusInfo.is_running || statusInfo.status === 'running' || destination.isPaused;
            
            if (process.env.NODE_ENV === 'development') {
              console.log(`Status for ${destId}:`, statusInfo);
              console.log(`- isPaused: ${destination.isPaused}, isRunning: ${destination.isRunning}`);
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
        // Add logging for debugging
        if (process.env.NODE_ENV === 'development') {
          console.log("Received updated scheduler statuses:", response.statuses);
          
          // Specifically look for paused schedulers
          const pausedSchedulers = Object.entries(response.statuses)
            .filter(([id, status]: [string, any]) => status.is_paused || status.status === 'paused');
          
          if (pausedSchedulers.length > 0) {
            console.log("Found PAUSED schedulers:", pausedSchedulers.map(([id]) => id).join(', '));
            
            // Log detailed info for each paused scheduler
            pausedSchedulers.forEach(([id, status]: [string, any]) => {
              console.log(`Paused scheduler ${id} details:`, status);
            });
          }
        }
        
        setSchedulerStatus(response.statuses);
        
        // Now update destination objects based on new statuses
        setDestinations(prevDestinations => {
          return prevDestinations.map(dest => {
            const status = response.statuses[dest.id];
            if (status) {
              const isPaused = status.is_paused || status.status === 'paused';
              const isRunning = status.is_running || status.status === 'running' || isPaused;
              
              return {
                ...dest,
                isPaused,
                isRunning
              };
            }
            return dest;
          });
        });
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

  const handleToggleSchedule = async (destinationId: string, isRunning: boolean) => {
    try {
      // Save previous state
      const previousStatus = schedulerStatus[destinationId];
      
      // Update UI immediately for better UX
      if (isRunning) {
        // Pausing a running scheduler
        setDestinations(prev => 
          prev.map(d => d.id === destinationId ? { ...d, isPaused: true, isRunning: true } : d)
        );
        setSchedulerStatus(prevStatus => ({
          ...prevStatus,
          [destinationId]: {
            ...prevStatus[destinationId],
            is_paused: true,
            status: 'paused'
          }
        }));
      } else {
        // Unpausing a paused scheduler
        setDestinations(prev => 
          prev.map(d => d.id === destinationId ? { ...d, isPaused: false, isRunning: true } : d)
        );
        setSchedulerStatus(prevStatus => ({
          ...prevStatus,
          [destinationId]: {
            ...prevStatus[destinationId],
            is_paused: false,
            status: 'running'
          }
        }));
      }
      
      // Call the API
      if (isRunning) {
        await apiService.pauseScheduler(destinationId);
      } else {
        await apiService.unpauseScheduler(destinationId);
      }
      
      // Refresh the data to ensure we have accurate state
      await fetchDestinations();
      
      toast({
        title: 'Success',
        description: `Scheduler ${isRunning ? 'paused' : 'resumed'} successfully`,
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
          {/* Variables Registry */}
          <VarsRegistryCard />
          
          {/* Running Schedulers */}
          <Card>
            <CardHeader>
              <CardTitle>Running Schedulers</CardTitle>
            </CardHeader>
            <CardContent>
              {sortedDestinations.filter(d => d.isRunning && !d.isPaused).length === 0 ? (
                <p className="text-muted-foreground">No running schedulers</p>
              ) : (
                <div className="space-y-6">
                  {sortedDestinations.filter(d => d.isRunning && !d.isPaused).map((destination) => (
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
          
          {/* Paused Schedulers */}
          <Card>
            <CardHeader>
              <CardTitle>Paused Schedulers</CardTitle>
            </CardHeader>
            <CardContent>
              {sortedDestinations.filter(d => d.isPaused).length === 0 ? (
                <p className="text-muted-foreground">No paused schedulers</p>
              ) : (
                <div className="space-y-6">
                  {sortedDestinations.filter(d => d.isPaused).map((destination) => (
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
              {sortedDestinations.filter(d => !d.isRunning && !d.isPaused).length === 0 ? (
                <p className="text-muted-foreground">No stopped schedulers</p>
              ) : (
                <div className="space-y-6">
                  {sortedDestinations.filter(d => !d.isRunning && !d.isPaused).map((destination) => (
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
          scriptsDirectory="routes/scheduler/scripts"
        />
      )}
    </MainLayout>
  );
};

interface SchedulerCardProps {
  destination: Destination;
  onToggle: (destinationId: string, isRunning: boolean) => void;
  onCreate: (destinationId: string) => void;
  onStart: (destinationId: string) => void;
  onStop: (destinationId: string) => void;
  onUnload: (destinationId: string) => void;
  onEdit: (destinationId: string, layer: number) => void;
  schedulerStatus: Record<string, any>;
}

const SchedulerCard: React.FC<SchedulerCardProps> = ({
  destination,
  onToggle,
  onCreate,
  onStart,
  onStop,
  onUnload,
  onEdit,
  schedulerStatus,
}) => {
  const [expanded, setExpanded] = useState(false);
  const toggleExpanded = () => setExpanded(!expanded);
  
  // Debug logs
  useEffect(() => {
    if (process.env.NODE_ENV === 'development') {
      console.log(`[SchedulerCard] Rendering card for ${destination.name} (${destination.id}):`);
      console.log(`[SchedulerCard] - isRunning: ${destination.isRunning}, isPaused: ${destination.isPaused}`);
      console.log(`[SchedulerCard] - Has scheduleStack: ${destination.scheduleStack ? destination.scheduleStack.length : 0} items`);
      console.log(`[SchedulerCard] - Has contextStack: ${destination.contextStack ? destination.contextStack.length : 0} items`);
      
      // Log the status from API for comparison
      const apiStatus = schedulerStatus[destination.id];
      if (apiStatus) {
        console.log(`[SchedulerCard] - API Status for ${destination.id}: `, apiStatus);
        console.log(`[SchedulerCard] - API is_running: ${apiStatus.is_running}, is_paused: ${apiStatus.is_paused}`);
        console.log(`[SchedulerCard] - API status string: ${apiStatus.status}`);
        
        // Check for mismatches between API and local state
        if (destination.isPaused !== (apiStatus.is_paused || apiStatus.status === 'paused')) {
          console.warn(`[SchedulerCard] WARNING: isPaused mismatch for ${destination.id}:`, {
            'component': destination.isPaused,
            'api': apiStatus.is_paused || apiStatus.status === 'paused'
          });
        }
        
        const apiIsRunning = apiStatus.is_running || apiStatus.status === 'running' || (apiStatus.is_paused || apiStatus.status === 'paused');
        if (destination.isRunning !== apiIsRunning) {
          console.warn(`[SchedulerCard] WARNING: isRunning mismatch for ${destination.id}:`, {
            'component': destination.isRunning,
            'api': apiIsRunning
          });
        }
      }
      
      if (destination.contextStack && destination.contextStack.length > 0) {
        console.log(`[SchedulerCard] - First context for ${destination.id}:`, destination.contextStack[0]);
      }
    }
  }, [destination, schedulerStatus]);
  
  // Display status more prominently
  const statusBadge = () => {
    if (destination.isPaused) {
      return (
        <Badge variant="outline" className="px-3 py-1 bg-amber-100 text-amber-800 border-amber-300 font-medium">
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
  
  // Action buttons
  const actionButtons = () => {
    // If the scheduler is running or paused (which means it's in the running_schedulers dict)
    if (destination.isRunning) {
      return (
        <div className="flex space-x-2">
          {destination.isPaused ? (
            <Button
              variant="outline" 
              size="sm"
              onClick={() => onToggle(destination.id, false)}
            >
              <Play className="h-4 w-4 mr-1" />
              Resume
            </Button>
          ) : (
            <Button
              variant="outline" 
              size="sm"
              onClick={() => onToggle(destination.id, true)}
            >
              <Pause className="h-4 w-4 mr-1" />
              Pause
            </Button>
          )}
          <Button
            variant="destructive" 
            size="sm"
            onClick={() => onStop(destination.id)}
          >
            <StopCircle className="h-4 w-4 mr-1" />
            Stop
          </Button>
          <Button
            variant="ghost" 
            size="sm"
            onClick={() => onEdit(destination.id, 0)}
          >
            <Edit className="h-4 w-4" />
          </Button>
        </div>
      );
    }
    
    // If the scheduler is not running
    return (
      <div className="flex space-x-2">
        {/* If there's a schedule available */}
        {destination.schedules && destination.schedules.length > 0 ? (
          <Button
            variant="outline" 
            size="sm"
            onClick={() => onStart(destination.id)}
          >
            <Play className="h-4 w-4 mr-1" />
            Start
          </Button>
        ) : (
          <Button
            variant="outline" 
            size="sm"
            onClick={() => onCreate(destination.id)}
          >
            <Plus className="h-4 w-4 mr-2" />
            Create
          </Button>
        )}
        <Button
          variant="ghost" 
          size="sm"
          onClick={() => onEdit(destination.id, 0)}
        >
          <Edit className="h-4 w-4" />
        </Button>
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
            {actionButtons()}
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="mt-4">
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
                onClick={toggleExpanded}
              >
                {expanded ? 'Hide details' : 'Show details'}
              </Button>
              {expanded && destination.scheduleStack && destination.scheduleStack.length > 0 && (
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
            
            {expanded && (
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
                          </div>
                        </div>
                        
                        <pre className="text-xs bg-muted p-2 rounded-md overflow-auto max-h-40">
                          {JSON.stringify(layer, null, 2)}
                        </pre>
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
      </CardContent>
    </Card>
  );
};

export default Scheduler; 
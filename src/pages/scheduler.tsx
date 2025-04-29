import React, { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/components/ui/use-toast';
import { Play, Pause, Plus, AlertCircle, RefreshCcw, ArrowDownToLine, X } from 'lucide-react';
import MainLayout from '@/components/layout/MainLayout';
import { useConsoleManagement } from '@/hooks/use-console-management';
import apiService from '@/utils/api';
import { getPublishDestinations } from '@/services/PublishService';
import { useNavigate } from 'react-router-dom';
import { SchemaEditModal } from '../components/scheduler/SchemaEditModal';

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
  contextStack?: Record<string, Context>;
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

  useEffect(() => {
    fetchSchedulers();
  }, []);

  const fetchSchedulers = async () => {
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
          
          // Check if this destination is in the running list
          if (runningSchedulers.includes(destId)) {
            // If it's running, get all the details
            try {
              // Get logs
              const logsResponse = await apiService.getSchedulerLogs(destId);
              destination.logs = logsResponse.log || [];
              
              // Get status
              const statusResponse = await apiService.getSchedulerStatus(destId);
              destination.isRunning = statusResponse.status === 'running' || statusResponse.status === 'paused';
              destination.isPaused = statusResponse.status === 'paused';
              
              // Get schedule stack
              const stackResponse = await apiService.getScheduleStack(destId);
              destination.scheduleStack = stackResponse.stack || [];
              
              // Get context
              const contextResponse = await apiService.getSchedulerContext(destId);
              destination.contextStack = contextResponse.context_stack || {};
              
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
            // Try to fetch schedule details anyway
            try {
              // Get schedule stack - might exist even if stopped
              const stackResponse = await apiService.getScheduleStack(destId);
              if (stackResponse && !stackResponse.error) {
                destination.scheduleStack = stackResponse.stack || [];
                
                // Get context if we have a schedule
                const contextResponse = await apiService.getSchedulerContext(destId);
                if (contextResponse && !contextResponse.error) {
                  destination.contextStack = contextResponse.context_stack || {};
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
              }
            } catch (error) {
              console.error(`Error fetching details for stopped scheduler ${destId}:`, error);
              // Continue with empty schedule details
            }
            
            destination.isRunning = false;
            destination.isPaused = false;
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
  };

  const handleToggleSchedule = async (destinationId: string, isRunning: boolean) => {
    try {
      if (isRunning) {
        await apiService.pauseScheduler(destinationId);
      } else {
        await apiService.unpauseScheduler(destinationId);
      }
      
      await fetchSchedulers();
      
      toast({
        title: 'Success',
        description: `Scheduler ${isRunning ? 'paused' : 'started'} successfully`,
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
      await fetchSchedulers();
      
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
      await fetchSchedulers();
      
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
      await fetchSchedulers();
      
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
  
  const handleSchemaEditSave = () => {
    // Refresh the data after successful save
    fetchSchedulers();
    
    toast({
      title: 'Success',
      description: 'Schedule saved successfully',
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
          <Button onClick={fetchSchedulers} variant="outline" size="sm">
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
}

const SchedulerCard: React.FC<SchedulerCardProps> = ({ 
  destination, 
  onToggle, 
  onCreate, 
  onStart, 
  onStop, 
  onUnload,
  onEdit
}) => {
  const [showLogs, setShowLogs] = useState(false);
  const [showSchedule, setShowSchedule] = useState(false);
  
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
  
  return (
    <Card className="shadow-md">
      <CardHeader className="bg-muted/30">
        <CardTitle className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <span>{destination.name}</span>
            {statusBadge()}
          </div>
          <div className="flex items-center space-x-2">
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
        {/* Empty State Message */}
        {(!destination.scheduleStack || destination.scheduleStack.length === 0) && (
          <div className="text-center py-4 text-muted-foreground">
            <p>No schedules found for this destination.</p>
            <p className="text-sm mt-2">Click "Create" to add a schedule or "Start" to create a default schedule.</p>
          </div>
        )}
      
        {/* Schedules */}
        {destination.schedules && destination.schedules.length > 0 && (
          <div className="space-y-4 mb-6">
            <h3 className="text-lg font-semibold">Schedules</h3>
            {destination.schedules.map((schedule) => (
              <div
                key={schedule.id}
                className="flex items-center justify-between p-4 border rounded-lg"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{schedule.name}</span>
                    <Badge variant={schedule.is_paused ? "outline" : schedule.is_running ? "default" : "secondary"}>
                      {schedule.is_paused ? "Paused" : schedule.is_running ? "Running" : "Stopped"}
                    </Badge>
                  </div>
                  <div className="text-sm text-muted-foreground">
                    <p>Cron: {schedule.cron}</p>
                    <p>Last run: {schedule.last_run}</p>
                    <p>Next run: {schedule.next_run}</p>
                  </div>
                  {schedule.error && (
                    <div className="flex items-center gap-2 text-destructive text-sm">
                      <AlertCircle className="h-4 w-4" />
                      <span>{schedule.error}</span>
                    </div>
                  )}
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onToggle(destination.id, destination.isRunning)}
                >
                  {destination.isRunning ? (
                    <>
                      <Pause className="h-4 w-4 mr-2" />
                      Pause
                    </>
                  ) : (
                    <>
                      <Play className="h-4 w-4 mr-2" />
                      Start
                    </>
                  )}
                </Button>
              </div>
            ))}
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
                {showSchedule ? 'Hide Schedule Stack' : 'Show Schedule Stack'}
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
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => onEdit(destination.id, index)}
                          >
                            Edit
                          </Button>
                        </div>
                        <pre className="text-xs bg-muted p-2 rounded-md overflow-auto max-h-40">
                          {JSON.stringify(layer, null, 2)}
                        </pre>
                        
                        {/* Show context variables if available */}
                        {destination.contextStack && destination.contextStack[index] && (
                          <div className="mt-4">
                            <h5 className="font-medium mb-2">Context Variables:</h5>
                            <div className="bg-muted p-2 rounded-md overflow-auto max-h-40">
                              <ul className="text-xs">
                                {Object.entries(destination.contextStack[index].vars || {}).map(([key, value]) => (
                                  <li key={key}>
                                    <strong>{key}:</strong> {JSON.stringify(value)}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          </div>
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
            <div className="bg-black text-green-400 p-4 rounded-lg font-mono text-xs overflow-auto max-h-60">
              {destination.logs && destination.logs.length > 0 ? (
                destination.logs.map((log, index) => (
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
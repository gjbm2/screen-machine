import React, { useState, useEffect } from 'react';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Clock, Calendar, Maximize, Package, ChevronDown, ChevronRight, Trash2, Send, History, List } from 'lucide-react';
import apiService from '@/utils/api';
import { toast } from 'sonner';
import { PublishDestination } from '@/utils/api';
import { HierarchicalHeader } from './HierarchicalHeader';
import { HierarchicalContent } from './HierarchicalContent';

interface EventItem {
  key: string;
  display_name: string | null;
  scope: string;
  active_from: string;
  expires: string;
  has_payload: boolean;
  payload?: any;
  single_consumer: boolean;
  created_at: string;
  destination_name?: string;
  destination_id?: string;
  unique_id?: string;
  consumed_by?: string;
  consumed_at?: string;
  status?: string;
}

interface EventsData {
  queue: EventItem[];
  history: EventItem[];
}

interface SchedulerEventsPanelProps {
  onEventCountChange?: (count: number) => void;
}

export const SchedulerEventsPanel: React.FC<SchedulerEventsPanelProps> = ({ onEventCountChange }) => {
  // State for form fields
  const [eventKey, setEventKey] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [scope, setScope] = useState('global'); // Default to global
  const [ttl, setTtl] = useState('60s');
  const [delay, setDelay] = useState('');
  const [futureTime, setFutureTime] = useState('');
  const [singleConsumer, setSingleConsumer] = useState(false);
  const [payload, setPayload] = useState('');
  const [payloadIsValid, setPayloadIsValid] = useState(true);
  const [payloadError, setPayloadError] = useState('');
  
  // State for event data
  const [eventsData, setEventsData] = useState<EventsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [destinations, setDestinations] = useState<PublishDestination[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const [allScopes, setAllScopes] = useState<string[]>(['global']);
  
  // State for payload modal
  const [payloadDialogOpen, setPayloadDialogOpen] = useState(false);
  const [selectedPayload, setSelectedPayload] = useState<any>(null);
  
  // Add expanded state (default: collapsed)
  const [isExpanded, setIsExpanded] = useState(false);
  
  // Add state for storing display names
  const [scopeDisplayMap, setScopeDisplayMap] = useState<Map<string, string>>(new Map());
  
  // Add pagination state for queue table
  const [queuePage, setQueuePage] = useState(0);
  const [queuePageSize, setQueuePageSize] = useState(10);
  
  // Add pagination state for history table
  const [historyPage, setHistoryPage] = useState(0);
  const [historyPageSize, setHistoryPageSize] = useState(10);
  
  // Add state for hierarchical sections
  const [triggerFormOpen, setTriggerFormOpen] = useState(false);
  const [queueSectionOpen, setQueueSectionOpen] = useState(false);
  const [historySectionOpen, setHistorySectionOpen] = useState(false);
  
  // Function to toggle expanded state
  const toggleExpanded = () => {
    console.log(`SchedulerEventsPanel: Toggling expanded state from ${isExpanded} to ${!isExpanded}`);
    setIsExpanded(!isExpanded);
  };
  
  // Fetch events and dependencies on component mount
  useEffect(() => {
    console.log('SchedulerEventsPanel: Component mounted, setting up polling');
    fetchDestinationsAndGroups();
    fetchEvents();
    
    // Set up polling for event updates (every 5 seconds)
    const intervalId = setInterval(() => {
      console.log('SchedulerEventsPanel: Polling interval triggered');
      fetchEvents();
    }, 5000);
    
    console.log(`SchedulerEventsPanel: Polling interval ${intervalId} set up`);
    
    // Clean up interval on unmount
    return () => {
      console.log(`SchedulerEventsPanel: Component unmounting, clearing interval ${intervalId}`);
      clearInterval(intervalId);
    };
  }, []);
  
  // Fetch publish destinations and groups
  const fetchDestinationsAndGroups = async () => {
    console.log('SchedulerEventsPanel: Fetching destinations and groups');
    try {
      // Get destinations
      const publishDestinations = await apiService.getPublishDestinations();
      console.log('SchedulerEventsPanel: Received destinations:', publishDestinations);
      setDestinations(publishDestinations);
      
      // Extract groups from the destinations using 'groups' property
      const uniqueGroups = new Set<string>();
      
      // Add default groups
      const defaultGroups = ['living-room', 'bedroom', 'kitchen'];
      defaultGroups.forEach(group => uniqueGroups.add(group));
      
      // Extract groups directly from destinations
      publishDestinations.forEach(dest => {
        if (dest.groups && Array.isArray(dest.groups)) {
          dest.groups.forEach(group => {
            if (group) uniqueGroups.add(group);
          });
        }
      });
      
      // Convert set to array
      const allGroups = Array.from(uniqueGroups);
      setGroups(allGroups);
      
      // Create the combined list of all unique scopes
      const scopeSet = new Set<string>(['global']); // Start with global
      
      // Add all groups
      allGroups.forEach(group => scopeSet.add(group));
      
      // Add all destinations with buckets
      publishDestinations
        .filter(dest => dest.has_bucket)
        .forEach(dest => scopeSet.add(dest.id));
      
      // Convert to array
      const scopes = Array.from(scopeSet);
      
      // Create a map for rendering friendly names with IDs
      const scopeDisplayMap = new Map<string, string>();
      scopeDisplayMap.set('global', 'Global (All Destinations)');
      
      // Add groups with their name only - no prefix needed
      for (const group of allGroups) {
        scopeDisplayMap.set(group, group);
      }
      
      // Add destinations with friendly names and IDs
      for (const dest of publishDestinations.filter(d => d.has_bucket)) {
        // If name and ID are the same, just show the name
        if (dest.name === dest.id) {
          scopeDisplayMap.set(dest.id, dest.id);
        } else {
          scopeDisplayMap.set(dest.id, `${dest.name} [${dest.id}]`);
        }
      }
      
      setAllScopes(scopes);
      setScopeDisplayMap(scopeDisplayMap);
      
    } catch (error) {
      console.error('Error fetching destinations and groups:', error);
      toast.error('Failed to fetch destinations');
      
      // FALLBACK: Set some basic scopes
      setAllScopes(['global', 'living-room', 'bedroom', 'kitchen', 'devtest']);
      setScope('global');
      
      // Set basic display names for fallback
      const basicMap = new Map<string, string>();
      basicMap.set('global', 'Global (All Destinations)');
      basicMap.set('living-room', 'living-room');
      basicMap.set('bedroom', 'bedroom');
      basicMap.set('kitchen', 'kitchen');
      basicMap.set('devtest', 'Development Test [devtest]');
      setScopeDisplayMap(basicMap);
    }
  };
  
  // Fetch events for display
  const fetchEvents = async () => {
    console.log(`SchedulerEventsPanel: Fetching ALL events`);
    setLoading(true);
    try {
      console.log(`SchedulerEventsPanel: Calling API to get all events`);
      const allEvents = await apiService.getAllEvents();
      console.log(`SchedulerEventsPanel: Received ${allEvents.queue.length} queued events and ${allEvents.history.length} history events`);
      setEventsData(allEvents);
    } catch (error) {
      console.error('Error fetching events:', error);
      if (loading) {
        toast.error('Failed to fetch events');
      }
    } finally {
      setLoading(false);
    }
  };
  
  // Handle payload validation
  const validatePayload = (value: string) => {
    if (!value.trim()) {
      setPayloadIsValid(true);
      setPayloadError('');
      return true;
    }
    
    try {
      JSON.parse(value);
      setPayloadIsValid(true);
      setPayloadError('');
      return true;
    } catch (error) {
      setPayloadIsValid(false);
      setPayloadError('Invalid JSON format');
      return false;
    }
  };
  
  // Handle form submission
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    console.log('SchedulerEventsPanel: Form submitted');
    
    // Validate required fields
    if (!eventKey) {
      toast.error('Event key is required');
      return;
    }
    
    // Validate payload if provided
    if (payload && !validatePayload(payload)) {
      toast.error('Invalid payload format');
      return;
    }
    
    // Prepare event data
    const eventData: any = {
      event: eventKey,
      scope: scope,  // Use the selected scope directly
      ttl,
      single_consumer: singleConsumer
    };
    
    // Add optional fields if provided
    if (displayName) eventData.display_name = displayName;
    if (delay) eventData.delay = delay;
    if (futureTime) eventData.future_time = futureTime;
    
    // Parse and add payload if provided
    if (payload) {
      try {
        eventData.payload = JSON.parse(payload);
      } catch {
        // Use as string if not valid JSON
        eventData.payload = payload;
      }
    }
    
    console.log('SchedulerEventsPanel: Event data prepared:', eventData);
    
    try {
      // Use the API service to throw the event
      console.log('SchedulerEventsPanel: Calling API to throw event');
      const result = await apiService.throwEvent(eventData);
      console.log('SchedulerEventsPanel: Event thrown successfully, result:', result);
      toast.success(`Event "${eventKey}" thrown successfully`);
      console.log('SchedulerEventsPanel: Now fetching updated events');
      await fetchEvents();
      console.log('SchedulerEventsPanel: Events fetched, resetting form');
      resetForm();
    } catch (error) {
      console.error('Error throwing event:', error);
      toast.error(`Failed to throw event: ${error}`);
    }
  };
  
  // Reset the form
  const resetForm = () => {
    setEventKey('');
    setDisplayName('');
    setScope('global');
    setTtl('60s');
    setDelay('');
    setFutureTime('');
    setSingleConsumer(false);
    setPayload('');
    setPayloadIsValid(true);
    setPayloadError('');
  };
  
  // Show payload details
  const showPayloadDetails = (payload: any) => {
    setSelectedPayload(payload);
    setPayloadDialogOpen(true);
  };
  
  // Format date for display
  const formatDate = (dateStr: string) => {
    try {
      const date = new Date(dateStr);
      return date.toLocaleString();
    } catch {
      return dateStr;
    }
  };
  
  // Check if event is in the future
  const isEventInFuture = (activeFrom: string) => {
    try {
      const eventDate = new Date(activeFrom);
      return eventDate > new Date();
    } catch {
      return false;
    }
  };
  
  // Function to handle page changes for queue
  const handleQueuePageChange = (newPage: number) => {
    setQueuePage(newPage);
  };
  
  // Function to handle page size changes for queue
  const handleQueuePageSizeChange = (newSize: number) => {
    setQueuePageSize(newSize);
    setQueuePage(0); // Reset to first page when changing page size
  };
  
  // Render queue table
  const renderQueueTable = () => {
    console.log('SchedulerEventsPanel: Rendering queue table');
    
    if (!eventsData) {
      return <p className="text-muted-foreground">No events data available</p>;
    }
    
    if (!eventsData.queue) {
      return <p className="text-muted-foreground">Queue data is missing</p>;
    }
    
    if (eventsData.queue.length === 0) {
      return <p className="text-muted-foreground">No active events in queue</p>;
    }
    
    // Function to delete an event
    const handleDeleteEvent = async (event: EventItem) => {
      try {
        if (!event.destination_id) {
          toast.error('Destination ID is missing');
          return;
        }
        
        if (event.unique_id) {
          // Delete by ID if available
          await apiService.clearEventById(event.destination_id, event.unique_id);
          toast.success(`Event ID ${event.unique_id.substring(0, 8)}... deleted`);
        } else {
          // Fall back to deleting by key
          await apiService.clearEventByKey(event.destination_id, event.key);
          toast.success(`Event '${event.key}' deleted`);
        }
        
        // Refresh events list
        fetchEvents();
      } catch (error) {
        console.error('Error deleting event:', error);
        toast.error('Failed to delete event');
      }
    };
    
    // Sort events by created_at (newest first)
    const sortedEvents = [...eventsData.queue].sort((a, b) => {
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
    
    // Calculate pagination
    const totalPages = Math.ceil(sortedEvents.length / queuePageSize);
    const startIndex = queuePage * queuePageSize;
    const endIndex = startIndex + queuePageSize;
    const paginatedEvents = sortedEvents.slice(startIndex, endIndex);
    
    return (
      <div className="space-y-2">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Key</TableHead>
              <TableHead>ID</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Destination</TableHead>
              <TableHead>Active From</TableHead>
              <TableHead>Expires</TableHead>
              <TableHead className="w-[120px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedEvents.map((event, index) => (
              <TableRow key={`queue-${index}-${event.key}-${event.unique_id || 'noid'}`}>
                <TableCell className="font-medium">
                  {event.key}
                  {event.display_name && (
                    <div className="text-xs text-muted-foreground">{event.display_name}</div>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs truncate max-w-[120px]">
                  {event.unique_id ? event.unique_id.substring(0, 8) + '...' : 'N/A'}
                </TableCell>
                <TableCell>
                  <Badge variant={event.status === 'ACTIVE' ? 'default' : 'secondary'}>
                    {event.status || 'ACTIVE'}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">{event.destination_name || event.destination_id || event.scope || '-'}</Badge>
                </TableCell>
                <TableCell>
                  <div className="flex items-center">
                    {isEventInFuture(event.active_from) && (
                      <Clock className="h-4 w-4 mr-1 text-blue-500" />
                    )}
                    {formatDate(event.active_from)}
                  </div>
                </TableCell>
                <TableCell>{formatDate(event.expires)}</TableCell>
                <TableCell>
                  <div className="flex space-x-2">
                    {event.has_payload && (
                      <Button
                        variant="outline"
                        size="icon"
                        title="View Payload"
                        onClick={() => showPayloadDetails(event.payload)}
                      >
                        <Package className="h-4 w-4" />
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="icon"
                      title="Delete Event"
                      onClick={() => handleDeleteEvent(event)}
                    >
                      <Trash2 className="h-4 w-4 text-red-500" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        
        {/* Pagination UI */}
        <div className="flex items-center justify-between">
          <div className="text-sm text-muted-foreground">
            Showing {startIndex + 1}-{Math.min(endIndex, sortedEvents.length)} of {sortedEvents.length} items
          </div>
          <div className="flex items-center space-x-2">
            <Select 
              value={queuePageSize.toString()} 
              onValueChange={(value) => handleQueuePageSizeChange(parseInt(value))}
            >
              <SelectTrigger className="h-8 w-[70px]">
                <SelectValue>{queuePageSize}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="5">5</SelectItem>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
                <SelectItem value="100">100</SelectItem>
              </SelectContent>
            </Select>
            <div className="flex items-center space-x-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleQueuePageChange(0)}
                disabled={queuePage === 0}
              >
                First
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleQueuePageChange(queuePage - 1)}
                disabled={queuePage === 0}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleQueuePageChange(queuePage + 1)}
                disabled={queuePage >= totalPages - 1}
              >
                Next
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleQueuePageChange(totalPages - 1)}
                disabled={queuePage >= totalPages - 1}
              >
                Last
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  };
  
  // Function to handle page changes for history
  const handleHistoryPageChange = (newPage: number) => {
    setHistoryPage(newPage);
  };
  
  // Function to handle page size changes for history
  const handleHistoryPageSizeChange = (newSize: number) => {
    setHistoryPageSize(newSize);
    setHistoryPage(0); // Reset to first page when changing page size
  };
  
  // Render history table
  const renderHistoryTable = () => {
    console.log('SchedulerEventsPanel: Rendering history table');
    
    if (!eventsData) {
      return <p className="text-muted-foreground">No events data available</p>;
    }
    
    if (!eventsData.history) {
      return <p className="text-muted-foreground">History data is missing</p>;
    }
    
    if (eventsData.history.length === 0) {
      return <p className="text-muted-foreground">No event history available</p>;
    }
    
    // Sort history events by created_at (newest first)
    const sortedHistory = [...eventsData.history].sort((a, b) => {
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
    
    // Calculate pagination
    const totalPages = Math.ceil(sortedHistory.length / historyPageSize);
    const startIndex = historyPage * historyPageSize;
    const endIndex = startIndex + historyPageSize;
    const paginatedHistory = sortedHistory.slice(startIndex, endIndex);
    
    return (
      <div className="space-y-2">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Key</TableHead>
              <TableHead>ID</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Destination</TableHead>
              <TableHead>Created At</TableHead>
              <TableHead>Consumed By</TableHead>
              <TableHead>Consumed At</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedHistory.map((event, index) => (
              <TableRow key={`history-${index}-${event.key}-${event.unique_id || 'noid'}`}>
                <TableCell className="font-medium">
                  {event.key}
                  {event.display_name && (
                    <div className="text-xs text-muted-foreground">{event.display_name}</div>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs truncate max-w-[120px]">
                  {event.unique_id ? event.unique_id.substring(0, 8) + '...' : 'N/A'}
                </TableCell>
                <TableCell>
                  <Badge 
                    variant={
                      event.status === 'EXPIRED' ? 'destructive' : 
                      event.status === 'CONSUMED' ? 'default' : 'secondary'
                    }
                  >
                    {event.status || 'UNKNOWN'}
                  </Badge>
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">{event.destination_name || event.destination_id || event.scope || '-'}</Badge>
                </TableCell>
                <TableCell>{formatDate(event.created_at)}</TableCell>
                <TableCell>
                  {event.consumed_by ? (
                    <span className="text-sm">{event.consumed_by}</span>
                  ) : (
                    <span className="text-muted-foreground text-sm">-</span>
                  )}
                </TableCell>
                <TableCell>
                  {event.consumed_at ? (
                    formatDate(event.consumed_at)
                  ) : (
                    <span className="text-muted-foreground">-</span>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex space-x-2">
                    {event.has_payload && (
                      <Button
                        variant="outline"
                        size="icon"
                        title="View Payload"
                        onClick={() => showPayloadDetails(event.payload)}
                      >
                        <Package className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        
        {/* Pagination UI */}
        <div className="flex items-center justify-between">
          <div className="text-sm text-muted-foreground">
            Showing {startIndex + 1}-{Math.min(endIndex, sortedHistory.length)} of {sortedHistory.length} items
          </div>
          <div className="flex items-center space-x-2">
            <Select 
              value={historyPageSize.toString()} 
              onValueChange={(value) => handleHistoryPageSizeChange(parseInt(value))}
            >
              <SelectTrigger className="h-8 w-[70px]">
                <SelectValue>{historyPageSize}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="5">5</SelectItem>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
                <SelectItem value="100">100</SelectItem>
              </SelectContent>
            </Select>
            <div className="flex items-center space-x-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleHistoryPageChange(0)}
                disabled={historyPage === 0}
              >
                First
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleHistoryPageChange(historyPage - 1)}
                disabled={historyPage === 0}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleHistoryPageChange(historyPage + 1)}
                disabled={historyPage >= totalPages - 1}
              >
                Next
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleHistoryPageChange(totalPages - 1)}
                disabled={historyPage >= totalPages - 1}
              >
                Last
              </Button>
            </div>
          </div>
        </div>
      </div>
    );
  };
  
  // Add useEffect for debug logging of count and expanded state
  useEffect(() => {
    if (eventsData) {
      const queueCount = eventsData.queue?.length || 0;
      console.log('SchedulerEventsPanel: Current event count:', 
        queueCount + (eventsData.history?.length || 0));
      
      // Call the callback with the queue count
      if (onEventCountChange) {
        onEventCountChange(queueCount);
      }
    }
  }, [eventsData, onEventCountChange]);

  useEffect(() => {
    console.log('SchedulerEventsPanel: Expanded state changed to:', isExpanded);
  }, [isExpanded]);

  // Function to clear all events for a destination
  const handleClearAllEvents = async () => {
    try {
      if (!scope || scope === 'global') {
        toast.error('Please select a specific destination to clear events');
        return;
      }
      
      // Confirm with the user
      if (!window.confirm(`Are you sure you want to clear all events for ${scope}?`)) {
        return;
      }
      
      await apiService.clearAllEvents(scope);
      toast.success(`All events for ${scope} cleared`);
      
      // Refresh events list
      fetchEvents();
    } catch (error) {
      console.error('Error clearing all events:', error);
      toast.error('Failed to clear events');
    }
  };

  return (
    <>
      {/* Level 2: Trigger Event Form */}
      <Card className="mb-4">
        <HierarchicalHeader
          title="Trigger Event"
          level={2}
          isOpen={triggerFormOpen}
          onToggle={() => setTriggerFormOpen(!triggerFormOpen)}
          icon={<Send className="h-4 w-4" />}
        />
        {triggerFormOpen && (
          <HierarchicalContent level={2}>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="eventKey">Event Key *</Label>
                  <Input
                    id="eventKey"
                    value={eventKey}
                    onChange={(e) => setEventKey(e.target.value)}
                    placeholder="user_login"
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="displayName">Display Name</Label>
                  <Input
                    id="displayName"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="User Login Event"
                  />
                </div>
              </div>
              
              <div className="space-y-2">
                <Label htmlFor="scope">Scope *</Label>
                <Select value={scope} onValueChange={setScope}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select scope">
                      {scope ? (scopeDisplayMap.get(scope) || scope) : "Select scope"}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {allScopes.map((scopeOption) => (
                      <SelectItem key={scopeOption} value={scopeOption}>
                        {scopeDisplayMap.get(scopeOption) || scopeOption}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  The event will be sent to the selected destination, group, or globally.
                </p>
              </div>
              
              <div className="grid grid-cols-3 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="ttl">TTL</Label>
                  <Input
                    id="ttl"
                    value={ttl}
                    onChange={(e) => setTtl(e.target.value)}
                    placeholder="60s"
                  />
                  <p className="text-xs text-muted-foreground">
                    Examples: 30s, 5m, 2h, 1d
                  </p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="delay">Delay</Label>
                  <Input
                    id="delay"
                    value={delay}
                    onChange={(e) => setDelay(e.target.value)}
                    placeholder="30s"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="futureTime">Future Time</Label>
                  <Input
                    id="futureTime"
                    type="datetime-local"
                    value={futureTime}
                    onChange={(e) => setFutureTime(e.target.value)}
                  />
                </div>
              </div>
              
              <div className="space-y-2">
                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="singleConsumer"
                    checked={singleConsumer}
                    onCheckedChange={(checked) => setSingleConsumer(checked as boolean)}
                  />
                  <Label htmlFor="singleConsumer">Single Consumer</Label>
                </div>
                <p className="text-xs text-muted-foreground">
                  If checked, event is removed after first consumption
                </p>
              </div>
              
              <div className="space-y-2">
                <Label htmlFor="payload">Payload (JSON)</Label>
                <Textarea
                  id="payload"
                  value={payload}
                  onChange={(e) => {
                    setPayload(e.target.value);
                    validatePayload(e.target.value);
                  }}
                  placeholder='{"user": "test_user", "action": "login"}'
                  className={payloadIsValid ? '' : 'border-red-500'}
                />
                {!payloadIsValid && (
                  <p className="text-xs text-red-500">{payloadError}</p>
                )}
              </div>
              
              <div className="flex justify-end space-x-2">
                <Button type="button" variant="outline" onClick={resetForm}>
                  Reset
                </Button>
                <Button type="submit">Throw Event</Button>
              </div>
            </form>
          </HierarchicalContent>
        )}
      </Card>

      {/* Level 2: Event Queue */}
      <Card className="mb-4">
        <HierarchicalHeader
          title="Event Queue"
          level={2}
          isOpen={queueSectionOpen}
          onToggle={() => setQueueSectionOpen(!queueSectionOpen)}
          count={eventsData?.queue?.length || 0}
          icon={<List className="h-4 w-4" />}
        />
        {queueSectionOpen && (
          <HierarchicalContent level={2}>
            {renderQueueTable()}
          </HierarchicalContent>
        )}
      </Card>

      {/* Level 2: Event History */}
      <Card>
        <HierarchicalHeader
          title="Event History"
          level={2}
          isOpen={historySectionOpen}
          onToggle={() => setHistorySectionOpen(!historySectionOpen)}
          count={eventsData?.history?.length || 0}
          icon={<History className="h-4 w-4" />}
        />
        {historySectionOpen && (
          <HierarchicalContent level={2}>
            {renderHistoryTable()}
          </HierarchicalContent>
        )}
      </Card>
      
      {/* Payload Dialog */}
      <Dialog open={payloadDialogOpen} onOpenChange={setPayloadDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Event Payload</DialogTitle>
            <DialogDescription>Detailed payload information</DialogDescription>
          </DialogHeader>
          
          <div className="mt-4 p-4 bg-gray-100 dark:bg-gray-800 rounded-md overflow-auto max-h-[400px]">
            <pre className="text-sm">
              {JSON.stringify(selectedPayload, null, 2)}
            </pre>
          </div>
          
          <DialogFooter>
            <Button onClick={() => setPayloadDialogOpen(false)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}; 
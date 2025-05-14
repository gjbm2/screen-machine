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
import { RefreshCcw, Clock, Calendar, Maximize, Package, ChevronDown, ChevronRight } from 'lucide-react';
import apiService from '@/utils/api';
import { toast } from 'sonner';
import { PublishDestination } from '@/utils/api';

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
}

interface EventsData {
  queue: EventItem[];
  history: EventItem[];
}

export const SchedulerEventsPanel: React.FC = () => {
  // State for form fields
  const [eventKey, setEventKey] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [scope, setScope] = useState('dest');
  const [destId, setDestId] = useState('');
  const [groupId, setGroupId] = useState('');
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
  
  // State for payload modal
  const [payloadDialogOpen, setPayloadDialogOpen] = useState(false);
  const [selectedPayload, setSelectedPayload] = useState<any>(null);
  
  // Add expanded state (default: collapsed)
  const [isExpanded, setIsExpanded] = useState(false);
  
  // Fetch events and dependencies on component mount
  useEffect(() => {
    fetchDestinationsAndGroups();
    fetchEvents();
    
    // Set up polling for event updates (every 5 seconds)
    const intervalId = setInterval(fetchEvents, 5000);
    
    // Clean up interval on unmount
    return () => clearInterval(intervalId);
  }, []);
  
  // Fetch publish destinations and groups
  const fetchDestinationsAndGroups = async () => {
    try {
      // Get destinations
      const publishDestinations = await apiService.getPublishDestinations();
      setDestinations(publishDestinations);
      
      // Set default destination if available
      if (publishDestinations.length > 0) {
        setDestId(publishDestinations[0].id);
      }
      
      // Extract groups from destinations (for group scope)
      // Normally this would come from the API, but we'll extract from destinations for now
      const uniqueGroups = publishDestinations.reduce((acc: string[], dest) => {
        // We're assuming groups are in the metadata but you may need to adjust this
        const destGroups = dest.description?.split(',').map(g => g.trim()) || [];
        return [...acc, ...destGroups.filter(g => g && !acc.includes(g))];
      }, []);
      
      setGroups(['living-room', 'bedroom', 'kitchen', ...uniqueGroups]);
      
      // Set default group if available
      if (uniqueGroups.length > 0) {
        setGroupId(uniqueGroups[0]);
      }
    } catch (error) {
      console.error('Error fetching destinations and groups:', error);
      toast.error('Failed to fetch destinations');
    }
  };
  
  // Fetch events for display
  const fetchEvents = async () => {
    setLoading(true);
    try {
      // For each destination, get its events
      let allEvents: EventsData = { queue: [], history: [] };
      
      // Using a sample destination for now - in production, we'd aggregate from all destinations
      if (destId) {
        // This endpoint is assumed to exist - adjust as needed
        const response = await fetch(`/api/schedulers/${destId}/events`);
        if (response.ok) {
          const data = await response.json();
          allEvents = data;
        }
      }
      
      setEventsData(allEvents);
    } catch (error) {
      console.error('Error fetching events:', error);
      // Don't show toast on periodic refresh to avoid spamming
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
    
    // Validate required fields
    if (!eventKey) {
      toast.error('Event key is required');
      return;
    }
    
    if (scope === 'dest' && !destId) {
      toast.error('Destination is required for destination scope');
      return;
    }
    
    if (scope === 'group' && !groupId) {
      toast.error('Group is required for group scope');
      return;
    }
    
    // Validate payload if provided
    if (payload && !validatePayload(payload)) {
      toast.error('Invalid payload format');
      return;
    }
    
    // Prepare event data
    const eventData: any = {
      scope,
      key: eventKey,
      ttl,
      single_consumer: singleConsumer
    };
    
    // Add optional fields if provided
    if (displayName) eventData.display_name = displayName;
    if (delay) eventData.delay = delay;
    if (futureTime) eventData.future_time = futureTime;
    if (scope === 'dest') eventData.dest_id = destId;
    if (scope === 'group') eventData.group_id = groupId;
    
    // Parse and add payload if provided
    if (payload) {
      try {
        eventData.payload = JSON.parse(payload);
      } catch {
        // Use as string if not valid JSON
        eventData.payload = payload;
      }
    }
    
    try {
      // Call the API to throw the event
      const response = await fetch('/api/schedulers/events/throw', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(eventData)
      });
      
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.message || 'Failed to throw event');
      }
      
      const result = await response.json();
      
      // Show success toast
      toast.success(`Event "${eventKey}" thrown successfully`);
      
      // Refresh events
      fetchEvents();
      
      // Optionally reset form
      // resetForm();
    } catch (error) {
      console.error('Error throwing event:', error);
      toast.error(`Failed to throw event: ${error}`);
    }
  };
  
  // Reset the form
  const resetForm = () => {
    setEventKey('');
    setDisplayName('');
    setScope('dest');
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
  
  // Render queue table
  const renderQueueTable = () => {
    if (!eventsData || !eventsData.queue || eventsData.queue.length === 0) {
      return <p className="text-muted-foreground">No active events in queue</p>;
    }
    
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Key</TableHead>
            <TableHead>Display Name</TableHead>
            <TableHead>Scope</TableHead>
            <TableHead>Active From</TableHead>
            <TableHead>Expires</TableHead>
            <TableHead className="w-[100px]">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {eventsData.queue.map((event, index) => (
            <TableRow key={`${event.key}-${index}`}>
              <TableCell className="font-medium">{event.key}</TableCell>
              <TableCell>{event.display_name || '-'}</TableCell>
              <TableCell>
                <Badge variant="outline">{event.scope}</Badge>
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
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  };
  
  // Render history table
  const renderHistoryTable = () => {
    if (!eventsData || !eventsData.history || eventsData.history.length === 0) {
      return <p className="text-muted-foreground">No event history available</p>;
    }
    
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Key</TableHead>
            <TableHead>Display Name</TableHead>
            <TableHead>Scope</TableHead>
            <TableHead>Created At</TableHead>
            <TableHead>Had Payload</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {eventsData.history.map((event, index) => (
            <TableRow key={`${event.key}-${index}`}>
              <TableCell className="font-medium">{event.key}</TableCell>
              <TableCell>{event.display_name || '-'}</TableCell>
              <TableCell>
                <Badge variant="outline">{event.scope}</Badge>
              </TableCell>
              <TableCell>{formatDate(event.created_at)}</TableCell>
              <TableCell>
                {event.has_payload ? (
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => showPayloadDetails(event.payload)}
                  >
                    <Package className="h-4 w-4" />
                  </Button>
                ) : (
                  'No'
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    );
  };
  
  return (
    <Card>
      <CardHeader 
        className="cursor-pointer hover:bg-muted/50 transition-colors" 
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center justify-between">
          <CardTitle className="text-xl font-bold flex items-center">
            {isExpanded ? (
              <ChevronDown className="h-5 w-5 mr-2 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-5 w-5 mr-2 text-muted-foreground" />
            )}
            Scheduler Events
            {eventsData && (
              <Badge variant="secondary" className="ml-2">
                {eventsData.queue.length + eventsData.history.length}
              </Badge>
            )}
          </CardTitle>
        </div>
      </CardHeader>
      {isExpanded && (
        <CardContent>
          <div className="space-y-4">
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
                    <SelectValue placeholder="Select scope" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="dest">Destination</SelectItem>
                    <SelectItem value="group">Group</SelectItem>
                    <SelectItem value="global">Global</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              
              {/* Conditional fields based on scope */}
              {scope === 'dest' && (
                <div className="space-y-2">
                  <Label htmlFor="destId">Destination *</Label>
                  <Select value={destId} onValueChange={setDestId}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select destination" />
                    </SelectTrigger>
                    <SelectContent>
                      {destinations.map((dest) => (
                        <SelectItem key={dest.id} value={dest.id}>
                          {dest.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              
              {scope === 'group' && (
                <div className="space-y-2">
                  <Label htmlFor="groupId">Group *</Label>
                  <Select value={groupId} onValueChange={setGroupId}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select group" />
                    </SelectTrigger>
                    <SelectContent>
                      {groups.map((group) => (
                        <SelectItem key={group} value={group}>
                          {group}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              
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

            <Tabs defaultValue="queue" className="w-full">
              <TabsList>
                <TabsTrigger value="queue">Queue</TabsTrigger>
                <TabsTrigger value="history">History</TabsTrigger>
              </TabsList>
              <TabsContent value="queue">
                {renderQueueTable()}
              </TabsContent>
              <TabsContent value="history">
                {renderHistoryTable()}
              </TabsContent>
            </Tabs>
          </div>
        </CardContent>
      )}
      
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
    </Card>
  );
}; 
// API service for all backend requests
import { toast } from 'sonner';

// Define default API URLs
const DEFAULT_API_URL = import.meta.env.VITE_API_URL || '/api';

// Types for buckets
export interface BucketItem {
  filename: string;
  url?: string;
  thumbnail_url?: string;
  thumbnail_embedded?: string;
  favorite: boolean;
  metadata?: Record<string, any>;
  created_at?: number;
}

export interface Bucket {
  name: string;
  items: BucketItem[];
  metadata?: Record<string, any>;
  published?: string;
  publishedAt?: string;
  favorites?: string[];
  sequence?: string[];
}

export interface PublishDestination {
  id: string;
  name: string;
  description?: string;
  icon?: string;
  has_bucket: boolean;
  file?: string;
  headless?: boolean;
}

// Type for image generation params
interface GenerateImageParams {
  prompt: string;
  workflow: string;
  params: Record<string, any>;
  global_params: Record<string, any>;
  imageFiles?: File[];
  referenceUrls?: string[];
  batch_id: string;
  placeholders: Array<{
    batch_index: number;
    placeholder_id: string;
  }>;
  refiner?: string;
  refiner_params?: Record<string, any>;
  is_async?: boolean;
}

interface BucketDetails {
  name: string;
  items: Array<{
    filename: string;
    url: string;
    thumbnail_url: string;
    thumbnail_embedded?: string;
    favorite: boolean;
    metadata: Record<string, any>;
    created_at: number;
  }>;
  published: string | null;
  publishedAt: string | null;
  favorites: string[];
  sequence: string[];
}

export class Api {
  private apiUrl: string;
  private mockMode: boolean;

  constructor(apiUrl: string = DEFAULT_API_URL) {
    this.apiUrl = apiUrl;
    // Check if we're running in mock mode (when backend is not available)
    this.mockMode = window.location.hostname.includes('lovable') || 
                    !apiUrl.startsWith('http');

    if (this.mockMode) {
      console.info('apiUrl:', apiUrl);
      console.info('hostname:', window.location.hostname);
      console.info('API Service running in mock mode - backend simulation active'); 
    } else { 
      console.info('API Service running in normal mode - backend live');
    }
  }

  // Get the API URL
  getApiUrl(): string {
    return this.apiUrl;
  }

  // Generate images through the API
	async generateImage(params: GenerateImageParams) {
	  try {
		const {
		  prompt,
		  workflow,
		  params: workflowParams,
		  global_params,
		  imageFiles,
		  batch_id,
		  placeholders,
		  referenceUrls,
          refiner,
          refiner_params,
		  is_async
		} = params;

		console.log(`[api] Received request with workflow: ${workflow}`);
		console.log(`[api] Received workflowParams:`, workflowParams);
		console.log(`[api] Received global_params:`, global_params);
		console.log(`[api] Received placeholders:`, placeholders);
		
		const formData = new FormData();

		const jsonData: any = {
		  prompt,
		  workflow,
		  params: workflowParams || {},
		  global_params: global_params || { batch_size: 1 },
		  batch_id,
		  has_reference_image: (imageFiles && imageFiles.length > 0) || false,
		};

		if (referenceUrls && referenceUrls.length > 0) {
		  jsonData.referenceUrls = referenceUrls;
		}

		if (placeholders && placeholders.length > 0) {
		  jsonData.placeholders = placeholders;
		}

		if (workflowParams?.publish_destination) {
		  console.log(`[api] Publishing to destination:`, workflowParams.publish_destination);
		}

		if (refiner && refiner !== 'none') {
		  console.log(`[api] Using refiner:`, refiner);
		  jsonData.refiner = refiner;
		  if (refiner_params) {
			console.log(`[api] With refiner params:`, refiner_params);
			jsonData.refiner_params = refiner_params;
		  }
		}

		console.log("[api] Full API payload:", jsonData);
		formData.append('data', JSON.stringify(jsonData));

		if (imageFiles && imageFiles.length > 0) {
		  imageFiles.forEach(file => {
			if (file instanceof File) {
			  formData.append('image', file);
			}
		  });
		}

		if (this.mockMode) {
		  return this.mockGenerateImage(params);
		}

		console.log('Calling:', `${this.apiUrl}/generate-image`);

		const response = await fetch(`${this.apiUrl}/generate-image`, {
		  method: 'POST',
		  body: formData,
		});

		if (!response.ok) {
		  const errorData = await response.json();
		  throw new Error(errorData.error || 'Failed to generate image');
		}

		return await response.json();
	  } catch (error) {
		console.error('Error generating image:', error);
		throw error;
	  }
	}

  
  // Send logs to the API
  async sendLog(message: string) {
    // In mock mode, just log to console
    if (this.mockMode) {
      console.info('[MOCK LOG]', message);
      return { success: true };
    }
    
    try {
      const response = await fetch(`${this.apiUrl}/log`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message }),
      });
      
      if (!response.ok) {
        console.error('Failed to send log');
        return { success: false };
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error sending log:', error);
      return { success: false };
    }
  }
  
  // Get logs from the API
  async getLogs(limit: number = 100) {
    if (this.mockMode) {
      // In mock mode, return mock logs
      return {
        success: true,
        logs: [
          '[MOCK BACKEND] Log system initialized',
          '[MOCK BACKEND] Ready to generate images'
        ]
      };
    }
    
    try {
      const response = await fetch(`${this.apiUrl}/logs?limit=${limit}`, {
        method: 'GET',
      });
      
      if (!response.ok) {
        console.error('Failed to get logs');
        return { success: false, logs: [] };
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error getting logs:', error);
      return { success: false, logs: [] };
    }
  }
  
  // Mock implementation for testing/preview
  private mockGenerateImage(params: GenerateImageParams) {
    // Get batch size from params
    const batchSize = params.global_params?.batch_size || 1;
    
    console.info('[MOCK LOG] [mock-backend]', `Generating ${batchSize} mock image(s) with prompt: "${params.prompt}"`);
    console.info('[MOCK LOG] [mock-backend]', `Using workflow: ${params.workflow}`);
    
    if (params.params?.publish_destination) {
      console.info('[MOCK LOG] [mock-backend]', `Publishing to: ${params.params.publish_destination}`);
    }
    
    if (params.refiner && params.refiner !== 'none') {
      console.info('[MOCK LOG] [mock-backend]', `Using refiner: ${params.refiner}`);
      if (params.refiner_params) {
        console.info('[MOCK LOG] [mock-backend]', `With refiner params:`, params.refiner_params);
      }
    }
    
    // Simulate network delay - changed from 2 seconds to 10 seconds
    return new Promise((resolve) => {
      setTimeout(() => {
        // Randomly select one of these placeholder images
        const placeholderImages = [
          'https://images.unsplash.com/photo-1543466835-00a7907e9de1?q=80&w=1974&auto=format&fit=crop',
          'https://images.unsplash.com/photo-1579783902614-a3fb3927b6a5?q=80&w=1945&auto=format&fit=crop',
          'https://images.unsplash.com/photo-1605979257913-1704eb7b6246?q=80&w=1770&auto=format&fit=crop',
          'https://images.unsplash.com/photo-1549289524-06cf8837ace5?q=80&w=1974&auto=format&fit=crop',
        ];
        
        const getRandomImage = () => {
          return placeholderImages[Math.floor(Math.random() * placeholderImages.length)];
        };
        
        // Create mock images based on the requested batch size and include placeholder IDs if provided
        const mockImages = Array(batchSize).fill(0).map((_, index) => {
          // Get placeholder ID if available
          const placeholderId = params.placeholders && params.placeholders[index] 
            ? params.placeholders[index].placeholder_id 
            : undefined;
          
          const batchIndex = params.placeholders && params.placeholders[index]
            ? params.placeholders[index].batch_index
            : index;
            
          return {
            id: `mock-${Date.now()}-${index}`,
            url: getRandomImage(),
            prompt: params.prompt,
            workflow: params.workflow,
            timestamp: Date.now(),
            batch_id: params.batch_id || `batch-${Date.now()}`,
            batch_index: batchIndex,
            placeholder_id: placeholderId, // Include the placeholder ID if available
            params: params.params,
            refiner: params.refiner,
            refiner_params: params.refiner_params,
            status: 'completed'
          };
        });
        
        console.info('[MOCK LOG] [mock-backend]', `Generated ${mockImages.length} mock image(s) successfully!`);
        
        resolve({
          success: true,
          images: mockImages,
          batch_id: params.batch_id || `batch-${Date.now()}`,
          prompt: params.prompt,
          workflow: params.workflow
        });
      }, 10000); // Changed from 2000 to 10000 (10 seconds)
    });
  }
  
  // Publish an image via backend (handled by Flask)
  async publishImage(data: {
    publish_destination_id: string;
    source: string;
    generation_info?: any;
    skip_bucket?: boolean;
  }): Promise<{ success: boolean; error?: string }> {
    console.error('DEPRECATED: Using old publishImage API format. Please update to the new unified format.');
    console.warn('This deprecated method will be removed in a future version.');
    
    // Map the old parameters to the new unified format
    return this.publishImageUnified({
      dest_bucket_id: data.publish_destination_id,
      ...(data.source.includes('/buckets/') && data.source.includes('/raw/') 
        ? this.extractBucketInfo(data.source) // Extract bucket info for bucket URLs
        : { source_url: data.source }), // For other URLs, pass as-is
      metadata: data.generation_info,
      skip_bucket: data.skip_bucket
    });
  }
  
  // Extract bucket and filename from a bucket URL
  private extractBucketInfo(url: string): { src_bucket_id: string; filename: string } {
    // Handle URLs like /api/buckets/<bucket_id>/raw/<filename>
    const parts = url.split('/');
    const bucketIndex = parts.indexOf('buckets') + 1;
    const filenameIndex = parts.indexOf('raw') + 1;
    
    if (bucketIndex > 0 && filenameIndex > 0 && bucketIndex < parts.length && filenameIndex < parts.length) {
      return {
        src_bucket_id: parts[bucketIndex],
        filename: parts[filenameIndex]
      };
    }
    
    // Fallback: Just try to get the filename
    return {
      src_bucket_id: 'unknown',
      filename: url.split('/').pop() || 'unknown'
    };
  }
  
  // Unified publish method that uses the new endpoint
  async publishImageUnified(data: {
    // Common parameters
    dest_bucket_id: string;
    
    // For bucket-to-bucket (Route A)
    src_bucket_id?: string;
    filename?: string;
    
    // For external URL (Route B)
    source_url?: string;
    metadata?: any;
    skip_bucket?: boolean;
  }): Promise<{ success: boolean; error?: string }> {
    // Validate we have the required params for one of the two routes
    const isRouteBucket = !!(data.src_bucket_id && data.filename);
    const isRouteExternal = !!data.source_url;
    
    if (!isRouteBucket && !isRouteExternal) {
      console.error('[publishImageUnified] Missing required parameters');
      return { success: false, error: 'Missing required parameters: either (src_bucket_id + filename) or source_url' };
    }
    
    console.log(`[publishImageUnified] Publishing with params:`, data);
    
    // Use the unified endpoint with timeout handling
    try {
      // Create an AbortController for timeout handling
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 30000); // 30-second timeout
      
      const response = await fetch(`${this.apiUrl}/publish`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          dest_bucket_id: data.dest_bucket_id,
          src_bucket_id: data.src_bucket_id,
          filename: data.filename,
          source_url: data.source_url,
          metadata: data.metadata,
          skip_bucket: data.skip_bucket
        }),
        signal: controller.signal
      });
      
      // Clear the timeout since the request completed
      clearTimeout(timeoutId);
      
      if (!response.ok) {
        const errorText = await response.text();
        let errorObj;
        
        try {
          errorObj = JSON.parse(errorText);
        } catch (e) {
          errorObj = { error: errorText || `Server error: ${response.status}` };
        }
        
        console.error(`[publishImageUnified] Error response (${response.status}):`, errorObj);
        return { 
          success: false, 
          error: errorObj.error || `Failed with status ${response.status}`
        };
      }
      
      console.log(`[publishImageUnified] Success!`);
      return { success: true };
    } catch (error) {
      // Check if this is an abort error (timeout)
      if (error instanceof DOMException && error.name === 'AbortError') {
        console.error(`[publishImageUnified] Request timed out after 30 seconds`);
        return { success: false, error: 'Request timed out' };
      }
      
      console.error(`[publishImageUnified] Request failed:`, error);
      return { 
        success: false, 
        error: error instanceof Error ? 
          `${error.name}: ${error.message}` : 
          String(error) 
      };
    }
  }
  
  // Save a scheduler schedule via backend
  async saveSchedule(destinationId: string, position: number | null, scheduleData: any) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Saving schedule for destination ID:', destinationId, 
                   position !== null ? `at position ${position}` : '(new)',
                   'with data:', scheduleData);
      return {
        success: true,
        message: 'Mock schedule save succeeded',
        position: position || 0
      };
    }

    try {
      // IMPORTANT: destinationId should be the ID of the destination, not the display name
      const endpoint = position !== null 
        ? `${this.apiUrl}/schedulers/${destinationId}/schedule/${position}` 
        : `${this.apiUrl}/schedulers/${destinationId}/schedule`;
      
      const method = position !== null ? 'PUT' : 'POST';
      
      console.log(`[api] Saving schedule to ${endpoint} with method ${method}`);
      
      const response = await fetch(endpoint, {
        method: method,
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(scheduleData),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Schedule save failed');
      }

      return await response.json();
    } catch (error) {
      console.error('Error saving schedule:', error);
      toast.error('Failed to save schedule');
      throw error;
    }
  }

  // Get the scheduler schema
  async getSchedulerSchema() {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Fetching scheduler schema');
      return JSON.stringify({
        type: 'object',
        properties: {
          name: { type: 'string' },
          cron: { type: 'string' },
          params: { type: 'object' }
        },
        required: ['name', 'cron']
      });
    }

    try {
      console.log('Fetching schema from API endpoint');
      const response = await fetch(`${this.apiUrl}/scheduler/schema`, {
        method: 'GET',
        headers: {
          'Accept': 'application/json'
        }
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to fetch scheduler schema');
      }

      // Get the raw text response instead of parsing as JSON
      const schemaText = await response.text();
      console.log('RAW schema text received from backend (first 100 chars):', schemaText.substring(0, 100));
      console.log('RAW schema text received from backend (full length):', schemaText.length);
      
      // Check if the first property is initial_actions
      const parsedForCheck = JSON.parse(schemaText);
      const firstKey = Object.keys(parsedForCheck.properties)[0];
      console.log('First property in schema.properties:', firstKey);
      
      return schemaText;
    } catch (error) {
      console.error('Error fetching scheduler schema:', error);
      toast.error('Failed to fetch scheduler schema');
      throw error;
    }
  }

  // List all schedulers
  async listSchedulers() {
    if (this.mockMode) {
      return { running: ['destination1', 'destination2'] };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers`);
      if (!response.ok) {
        throw new Error('Failed to list schedulers');
      }
      return await response.json();
    } catch (error) {
      console.error('Error listing schedulers:', error);
      throw error;
    }
  }

  // Get destinations with scheduler status
  async getDestinations() {
    if (this.mockMode) {
      return { 
        destinations: [
          { id: 'destination1', scheduler_running: true },
          { id: 'destination2', scheduler_running: false }
        ]
      };
    }

    try {
      // First get all publish destinations
      const publishDestinations = await this.getPublishDestinations();
      
      // Then get all scheduler statuses
      const statusesResponse = await this.getAllSchedulerStatuses();
      const statuses = statusesResponse.statuses || {};
      
      // Combine the information
      const destinations = publishDestinations.map(dest => ({
        id: dest.id,
        name: dest.name,
        scheduler_running: statuses[dest.id]?.is_running || false
      }));
      
      return { destinations };
    } catch (error) {
      console.error('Error getting destinations:', error);
      throw error;
    }
  }

  // Get scheduler logs
  async getSchedulerLogs(destinationId: string) {
    if (this.mockMode) {
      return { log: ['Mock log entry 1', 'Mock log entry 2'] };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}`);
      if (!response.ok) {
        throw new Error('Failed to fetch scheduler logs');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching scheduler logs:', error);
      throw error;
    }
  }

  // Start a scheduler
  async startScheduler(destinationId: string, schedule: any) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Starting scheduler ID:', destinationId);
      return {
        success: true,
        status: 'started',
        destination: destinationId
      };
    }

    try {
      // IMPORTANT: destinationId should be the ID of the destination, not the display name
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(schedule),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to start scheduler');
      }

      return await response.json();
    } catch (error) {
      console.error('Error starting scheduler:', error);
      toast.error('Failed to start scheduler');
      throw error;
    }
  }

  // Stop a scheduler
  async stopScheduler(destinationId: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Stopping scheduler ID:', destinationId);
      return {
        success: true,
        status: 'stopped',
        destination: destinationId
      };
    }

    try {
      // IMPORTANT: destinationId should be the ID of the destination, not the display name
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to stop scheduler');
      }

      return await response.json();
    } catch (error) {
      console.error('Error stopping scheduler:', error);
      toast.error('Failed to stop scheduler');
      throw error;
    }
  }

  // Get current schedule
  async getSchedule(destinationId: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Getting schedule for ID:', destinationId);
      return {
        success: true,
        schedule: {
          name: 'Mock Schedule',
          cron: '* * * * *'
        },
        destination: destinationId
      };
    }

    try {
      // IMPORTANT: destinationId should be the ID of the destination, not the display name
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule`, {
        method: 'GET',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to get schedule');
      }

      return await response.json();
    } catch (error) {
      console.error('Error getting schedule:', error);
      toast.error('Failed to get schedule');
      throw error;
    }
  }

  // Pause a scheduler
  async pauseScheduler(destinationId: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Pausing scheduler ID:', destinationId);
      return {
        success: true,
        status: 'paused',
        destination: destinationId
      };
    }

    try {
      // IMPORTANT: destinationId should be the ID of the destination, not the display name
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/pause`, {
        method: 'POST',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to pause scheduler');
      }

      return await response.json();
    } catch (error) {
      console.error('Error pausing scheduler:', error);
      toast.error('Failed to pause scheduler');
      throw error;
    }
  }

  // Unpause a scheduler
  async unpauseScheduler(destinationId: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Unpausing scheduler ID:', destinationId);
      return {
        success: true,
        status: 'running',
        destination: destinationId
      };
    }

    try {
      // IMPORTANT: destinationId should be the ID of the destination, not the display name
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/unpause`, {
        method: 'POST',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to unpause scheduler');
      }

      return await response.json();
    } catch (error) {
      console.error('Error unpausing scheduler:', error);
      toast.error('Failed to unpause scheduler');
      throw error;
    }
  }

  // Get scheduler status
  async getSchedulerStatus(destinationId: string) {
    if (this.mockMode) {
      return { is_running: false, is_paused: false };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/status`);
      if (!response.ok) {
        throw new Error('Failed to fetch scheduler status');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching scheduler status:', error);
      throw error;
    }
  }

  // Get scheduler context
  async getSchedulerContext(destinationId: string) {
    if (this.mockMode) {
      return { vars: {}, last_generated: null };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/context`);
      if (!response.ok) {
        throw new Error('Failed to fetch scheduler context');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching scheduler context:', error);
      throw error;
    }
  }

  // Set scheduler context
  async setSchedulerContext(destinationId: string, context: any) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Setting context for scheduler ID:', destinationId);
      return {
        success: true,
        context
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/context`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(context),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to set scheduler context');
      }

      return await response.json();
    } catch (error) {
      console.error('Error setting scheduler context:', error);
      toast.error('Failed to set scheduler context');
      throw error;
    }
  }

  // Load a schedule
  async loadSchedule(destinationId: string, schedule: any) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Loading schedule for ID:', destinationId);
      return {
        success: true,
        schedule,
        destination: destinationId
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(schedule),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to load schedule');
      }

      return await response.json();
    } catch (error) {
      console.error('Error loading schedule:', error);
      toast.error('Failed to load schedule');
      throw error;
    }
  }

  // Unload a schedule
  async unloadSchedule(destinationId: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Unloading schedule for ID:', destinationId);
      return {
        success: true,
        destination: destinationId
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to unload schedule');
      }

      return await response.json();
    } catch (error) {
      console.error('Error unloading schedule:', error);
      toast.error('Failed to unload schedule');
      throw error;
    }
  }

  // Get schedule stack
  async getScheduleStack(destinationId: string) {
    if (this.mockMode) {
      return { stack: [], stack_size: 0 };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule/stack`);
      if (!response.ok) {
        throw new Error('Failed to fetch schedule stack');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching schedule stack:', error);
      throw error;
    }
  }

  // Trigger an event
  async triggerEvent(destinationId: string, eventData: any) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Triggering event for ID:', destinationId);
      return {
        success: true,
        event: eventData.event
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/events`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(eventData),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to trigger event');
      }

      return await response.json();
    } catch (error) {
      console.error('Error triggering event:', error);
      toast.error('Failed to trigger event');
      throw error;
    }
  }

  // Get schedule at position
  async getScheduleAtPosition(destinationId: string, position: number) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Getting schedule at position:', position, 'for ID:', destinationId);
      return {
        success: true,
        schedule: {
          name: `Mock Schedule at position ${position}`,
          cron: '* * * * *'
        },
        position
      };
    }

    try {
      // Get the entire schedule stack
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule/stack`);

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to get schedule stack');
      }

      const data = await response.json();
      
      // Check if we have a stack and if the position is valid
      if (!data.stack || !Array.isArray(data.stack) || position >= data.stack.length) {
        throw new Error(`No schedule found at position ${position}`);
      }
      
      // Return the schedule at the specified position
      return {
        success: true,
        schedule: data.stack[position],
        position: position
      };
    } catch (error) {
      console.error('Error getting schedule at position:', error);
      toast.error('Failed to get schedule at position');
      throw error;
    }
  }

  // Set schedule at position
  async setScheduleAtPosition(destinationId: string, position: number, schedule: any) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Setting schedule at position:', position, 'for ID:', destinationId);
      return {
        success: true,
        schedule,
        position
      };
    }

    try {
      // First get the current stack
      const stackResponse = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule/stack`);
      if (!stackResponse.ok) {
        const err = await stackResponse.json();
        throw new Error(err.error || 'Failed to get schedule stack');
      }
      
      const stackData = await stackResponse.json();
      
      // Check if the position is valid for updating
      if (!stackData.stack || !Array.isArray(stackData.stack)) {
        throw new Error('Invalid schedule stack data');
      }
      
      // This is a workaround since the API doesn't support direct updates at positions
      // We need to unload schedules until the one we want to edit, then update it
      
      // First, stop the scheduler if it's running
      const statusResponse = await fetch(`${this.apiUrl}/schedulers/${destinationId}/status`);
      if (!statusResponse.ok) {
        throw new Error('Failed to get scheduler status');
      }
      
      const statusData = await statusResponse.json();
      const wasRunning = statusData.is_running;
      
      if (wasRunning) {
        // Stop the scheduler temporarily
        await fetch(`${this.apiUrl}/schedulers/${destinationId}`, {
          method: 'DELETE',
        });
      }
      
      // Now reload the current schedule with the updated version
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(schedule),
      });
      
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to update schedule');
      }
      
      // Restart the scheduler if it was running before
      if (wasRunning) {
        await fetch(`${this.apiUrl}/schedulers/${destinationId}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(schedule),
        });
      }
      
      return {
        success: true,
        schedule,
        position
      };
    } catch (error) {
      console.error('Error setting schedule at position:', error);
      toast.error('Failed to set schedule at position');
      throw error;
    }
  }

  // Remove schedule at position
  async removeScheduleAtPosition(destinationId: string, position: number) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Removing schedule at position:', position, 'for ID:', destinationId);
      return {
        success: true,
        position
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule/${position}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to remove schedule at position');
      }

      return await response.json();
    } catch (error) {
      console.error('Error removing schedule at position:', error);
      toast.error('Failed to remove schedule at position');
      throw error;
    }
  }

  // Clear scheduler context
  async clearSchedulerContext(destinationId: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Clearing context for scheduler ID:', destinationId);
      return {
        success: true
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/context/clear`, {
        method: 'POST',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to clear scheduler context');
      }

      return await response.json();
    } catch (error) {
      console.error('Error clearing scheduler context:', error);
      toast.error('Failed to clear scheduler context');
      throw error;
    }
  }

  // Get publish destinations
  async getPublishDestinations(): Promise<PublishDestination[]> {
    try {
      const response = await fetch(`${this.apiUrl}/publish-destinations`);
      if (!response.ok) {
        throw new Error('Failed to fetch publish destinations');
      }
      const destinations = await response.json();
      return destinations.map((dest: any) => ({
        id: dest.id,
        name: dest.name || dest.id,
        description: dest.description,
        icon: dest.icon,
        has_bucket: dest.has_bucket || false,
        headless: dest.headless || false
      }));
    } catch (error) {
      console.error('Error fetching publish destinations:', error);
      throw error;
    }
  }

  async getBucketDetails(bucketId: string): Promise<BucketDetails> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/complete`);
    if (!response.ok) {
      throw new Error(`Failed to get bucket details: ${response.statusText}`);
    }
    const data = await response.json();
    console.log('Raw bucket details response:', data);
    
    // Map files to items with the correct fields
    const items = Array.isArray(data.files) ? data.files.map(file => {
      const baseMeta = file.metadata || {};
      // Use created_at from the API response, fall back to metadata timestamp or file stats
      const createdTs = file.created_at || baseMeta.timestamp || file.modified;

      return {
        filename: file.filename,
        url: file.raw_url || file.url || '',
        thumbnail_url: file.thumbnail_url,
        thumbnail_embedded: file.thumbnail_embedded,
        favorite: file.favorite || false,
        metadata: {
          ...baseMeta,
          timestamp: createdTs,
        },
        created_at: createdTs, // Add created_at to the item directly
      };
    }) : [];
    
    // Get published info from the published object
    const published = data.published || null;
    
    // Log info about the published image but don't add it to items
    if (published && published.from_bucket === false) {
      console.log('Published image is not from this bucket:', published.filename);
      console.log('Published at:', published.published_at);
      console.log('Raw URL:', published.raw_url);
    }
    
    // Ensure we have a valid BucketDetails object
    const bucketDetails = {
      name: data.bucket_id || bucketId,
      items: items,
      published: published?.filename || null,
      publishedAt: published?.published_at || null,
      favorites: Array.isArray(data.favorites) ? data.favorites : [],
      sequence: Array.isArray(data.sequence) ? data.sequence : []
    };
    
    console.log('Processed bucket details:', bucketDetails);
    return bucketDetails;
  }

  // Get all buckets
  async fetchAllBuckets(): Promise<string[]> {
    if (this.mockMode) {
      return ['mock-bucket-1', 'mock-bucket-2'];
    }

    try {
      const destinations = await this.getPublishDestinations();
      return destinations.map(d => d.id);
    } catch (error) {
      console.error('Error fetching all buckets:', error);
      throw error;
    }
  }

  // Toggle favorite status for an image
  async toggleFavorite(bucketId: string, filename: string, isFavorite: boolean): Promise<boolean> {
    // If isFavorite is true, we want to favorite it (POST)
    // If isFavorite is false, we want to unfavorite it (DELETE)
    const method = isFavorite ? 'POST' : 'DELETE';
    
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/favorite/${filename}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
      }
    });
    if (!response.ok) {
      throw new Error(`Failed to toggle favorite: ${response.statusText}`);
    }
    const data = await response.json();
    return data.status === 'favorited' || data.status === 'unfavorited';
  }

  // Delete an image from a bucket
  async deleteImage(bucketId: string, filename: string): Promise<boolean> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/${filename}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      throw new Error(`Failed to delete image: ${response.statusText}`);
    }
    return true;
  }

  // Move an image up or down in a bucket
  async moveImage(bucketId: string, filename: string, direction: 'up' | 'down'): Promise<boolean> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/move/${filename}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ direction }),
    });
    if (!response.ok) {
      throw new Error(`Failed to move image: ${response.statusText}`);
    }
    return true;
  }

  // Copy an image to another bucket
  async copyImageToBucket(sourceBucketId: string, targetBucketId: string, filename: string, copy: boolean = false): Promise<{ status: string; filename: string }> {
    try {
      console.log(`[copyImageToBucket] source=${sourceBucketId}, target=${targetBucketId}, filename=${filename}, copy=${copy}`);
      
      const response = await fetch(`${this.apiUrl}/buckets/add_image_to_new_bucket`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          source_publish_destination: sourceBucketId,
          target_publish_destination: targetBucketId,
          filename,
          copy: copy,
        }),
      });
      
      if (!response.ok) {
        throw new Error(`Failed to copy image: ${response.statusText}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error copying image:', error);
      throw error;
    }
  }

  // Perform bucket maintenance
  async performBucketMaintenance(bucketId: string, action: 'purge' | 'reindex' | 'extract'): Promise<boolean> {
    // Map the action to the correct endpoint
    let endpoint;
    if (action === 'reindex') {
      endpoint = `${this.apiUrl}/buckets/${bucketId}/reindex`;
    } else if (action === 'purge') {
      endpoint = `${this.apiUrl}/buckets/${bucketId}/purge`;
    } else if (action === 'extract') {
      endpoint = `${this.apiUrl}/buckets/${bucketId}/extractjson`;
    } else {
      throw new Error(`Unknown maintenance action: ${action}`);
    }
    
    console.log(`[performBucketMaintenance] Using endpoint: ${endpoint} for action: ${action}`);
    
    const response = await fetch(endpoint, {
      method: 'POST',
    });
    
    if (!response.ok) {
      throw new Error(`Failed to perform maintenance: ${response.statusText}`);
    }
    
    return true;
  }

  // Get all scheduler statuses
  async getAllSchedulerStatuses() {
    if (this.mockMode) {
      return {
        statuses: {
          'destination1': { is_running: true, is_paused: false },
          'destination2': { is_running: false, is_paused: false }
        }
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/all/status`);
      if (!response.ok) {
        throw new Error('Failed to fetch scheduler statuses');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching scheduler statuses:', error);
      throw error;
    }
  }

  // Get next scheduled action
  async getNextScheduledAction(destinationId: string) {
    if (this.mockMode) {
      return { next_action: { type: 'generate', scheduled_time: new Date().toISOString() } };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/next_action`);
      if (!response.ok) {
        throw new Error('Failed to fetch next scheduled action');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching next scheduled action:', error);
      throw error;
    }
  }

  // Get variables registry
  async getVarsRegistry() {
    if (this.mockMode) {
      return {
        status: 'success',
        registry: {
          global: {
            'theme': {
              friendly_name: 'Daily Theme',
              owner: 'destination1',
              value: 'apples',
              timestamp: new Date().toISOString()
            }
          },
          groups: {
            'living-room': {
              'mood': {
                friendly_name: 'Room Mood',
                owner: 'destination2',
                value: 'calm',
                timestamp: new Date().toISOString()
              }
            }
          },
          imports: {
            'theme': {
              'destination2': {
                imported_as: '_theme',
                source: 'destination1',
                timestamp: new Date().toISOString()
              }
            }
          },
          last_updated: new Date().toISOString()
        }
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/vars-registry`);
      if (!response.ok) {
        throw new Error('Failed to fetch variables registry');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching variables registry:', error);
      throw error;
    }
  }

  // Get exported variables for a destination
  async getExportedVars(destinationId: string) {
    if (this.mockMode) {
      return {
        status: 'success',
        destination: destinationId,
        exported_vars: {
          'theme': {
            friendly_name: 'Daily Theme',
            owner: 'destination1',
            value: 'apples',
            timestamp: new Date().toISOString()
          }
        }
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/exported-vars`);
      if (!response.ok) {
        throw new Error('Failed to fetch exported variables');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching exported variables:', error);
      throw error;
    }
  }

  // Set an exported variable value
  async setExportedVar(varName: string, value: any) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Setting exported variable:', varName, 'to value:', value);
      return {
        status: 'success',
        var_name: varName,
        value: value,
        updated_importers: {}
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/exported-vars/${varName}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ value }),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to set exported variable');
      }

      return await response.json();
    } catch (error) {
      console.error('Error setting exported variable:', error);
      toast.error('Failed to set exported variable');
      throw error;
    }
  }

  // Delete an exported variable
  async deleteExportedVar(varName: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Deleting exported variable:', varName);
      return {
        status: 'success',
        message: `Exported variable '${varName}' was deleted`
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/exported-vars/${varName}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to delete exported variable');
      }

      return await response.json();
    } catch (error) {
      console.error('Error deleting exported variable:', error);
      toast.error('Failed to delete exported variable');
      throw error;
    }
  }

  async getBuckets(): Promise<string[]> {
    const response = await fetch(`${this.apiUrl}/buckets/`);
    if (!response.ok) {
      throw new Error(`Failed to get buckets: ${response.statusText}`);
    }
    return response.json();
  }

  async createBucket(bucketId: string): Promise<{ status: string; bucket_id: string }> {
    const response = await fetch(`${this.apiUrl}/buckets/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ bucket_id: bucketId }),
    });
    if (!response.ok) {
      throw new Error(`Failed to create bucket: ${response.statusText}`);
    }
    return response.json();
  }

  async uploadToBucket(bucketId: string, file: File): Promise<{ filename: string }> {
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/upload`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      throw new Error(`Failed to upload to bucket: ${response.statusText}`);
    }
    return response.json();
  }

  async publishFromBucket(bucketId: string, filename: string, skip_bucket?: boolean): Promise<{ success: boolean; error?: string }> {
    const response = await fetch(`${this.apiUrl}/publish/publish`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        publish_destination_id: bucketId,
        source_url: `${this.apiUrl}/buckets/${bucketId}/${filename}`,
        skip_bucket,
      }),
    });
    if (!response.ok) {
      throw new Error(`Failed to publish from bucket: ${response.statusText}`);
    }
    return response.json();
  }

  async favoriteInBucket(bucketId: string, filename: string): Promise<{ status: string }> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/favorite/${filename}`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error(`Failed to favorite in bucket: ${response.statusText}`);
    }
    return response.json();
  }

  async unfavoriteInBucket(bucketId: string, filename: string): Promise<{ status: string }> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/favorite/${filename}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      throw new Error(`Failed to unfavorite in bucket: ${response.statusText}`);
    }
    return response.json();
  }

  async deleteFromBucket(bucketId: string, filename: string): Promise<{ status: string }> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/${filename}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
      throw new Error(`Failed to delete from bucket: ${response.statusText}`);
    }
    return response.json();
  }

  async moveUpInBucket(bucketId: string, filename: string): Promise<{ status: string; index: number }> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/move-up/${filename}`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error(`Failed to move up in bucket: ${response.statusText}`);
    }
    return response.json();
  }

  async moveDownInBucket(bucketId: string, filename: string): Promise<{ status: string; index: number }> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/move-down/${filename}`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error(`Failed to move down in bucket: ${response.statusText}`);
    }
    return response.json();
  }

  async purgeBucket(bucketId: string): Promise<{ status: string; removed: string[] }> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/purge`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error(`Failed to purge bucket: ${response.statusText}`);
    }
    return response.json();
  }

  async reindexBuckets(): Promise<{ status: string; count: number }> {
    const response = await fetch(`${this.apiUrl}/buckets/reindex`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error(`Failed to reindex buckets: ${response.statusText}`);
    }
    return response.json();
  }

  async extractJsonFromBucket(bucketId: string): Promise<{ status: string; updated: string[] }> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/extractjson`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error(`Failed to extract JSON from bucket: ${response.statusText}`);
    }
    return response.json();
  }

  async publish(imagePath: string, destinationId: string): Promise<void> {
    try {
      const response = await fetch(`${this.apiUrl}/publish/publish`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          image_path: imagePath,
          destination_id: destinationId
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to publish image');
      }
    } catch (error) {
      console.error('Error publishing image:', error);
      throw error;
    }
  }

  // Move an image to a position after another image in a bucket
  async moveToPosition(bucketId: string, filename: string, targetFilename: string | null = null): Promise<{ status: string; index: number }> {
    const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/move-to/${filename}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ insert_after: targetFilename }),
    });
    if (!response.ok) {
      throw new Error(`Failed to move image to position: ${response.statusText}`);
    }
    return await response.json();
  }

  // File operations - for use with scheduler scripts and config files
  async listFiles(directory: string): Promise<string[]> {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Listing files in directory:', directory);
      return ['mock-file-1.json', 'mock-file-2.json'];
    }

    try {
      // Using ${this.apiUrl}/files instead of hardcoded '/api/files'
      const response = await fetch(`${this.apiUrl}/files?directory=${encodeURIComponent(directory)}`);
      
      if (!response.ok) {
        throw new Error(`Failed to list files: ${response.status}`);
      }
      
      const data = await response.json();
      return data.files || [];
    } catch (error) {
      console.error('Error listing files:', error);
      throw error;
    }
  }

  async readFile(filePath: string): Promise<any> {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Reading file:', filePath);
      return { content: '{"mock": "content"}' };
    }

    try {
      // Using ${this.apiUrl}/files instead of hardcoded '/api/files'
      const response = await fetch(`${this.apiUrl}/files/${encodeURIComponent(filePath)}`);
      
      if (!response.ok) {
        throw new Error(`Failed to read file: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error reading file:', error);
      throw error;
    }
  }

  async writeFile(path: string, content: string): Promise<{ success: boolean; path: string }> {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Writing file:', path);
      return { success: true, path };
    }

    try {
      // Using ${this.apiUrl}/files instead of hardcoded '/api/files'
      const response = await fetch(`${this.apiUrl}/files`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          path,
          content
        })
      });
      
      if (!response.ok) {
        throw new Error(`Failed to write file: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error writing file:', error);
      throw error;
    }
  }

  // Set a specific variable in a scheduler's context
  async setSchedulerContextVar(destinationId: string, varName: string, varValue: any) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Setting context variable for scheduler ID:', destinationId, 'varName:', varName, 'value:', varValue);
      return {
        status: 'success',
        var_name: varName,
        var_value: varValue,
        vars: { [varName]: varValue },
        deleted: varValue === null
      };
    }

    try {
      console.log('[API] setSchedulerContextVar - Starting request:', {
        destinationId,
        varName,
        varValue,
        endpoint: `${this.apiUrl}/schedulers/${destinationId}/context`,
        isNull: varValue === null
      });
      
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/context`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          var_name: varName,
          var_value: varValue
        }),
      });

      if (!response.ok) {
        const err = await response.json();
        console.error('[API] setSchedulerContextVar - Error response:', err);
        throw new Error(err.error || 'Failed to set scheduler context variable');
      }

      const data = await response.json();
      console.log('[API] setSchedulerContextVar - Success response:', data);
      return data;
    } catch (error) {
      console.error('[API] setSchedulerContextVar - Exception:', error);
      toast.error('Failed to set scheduler context variable');
      throw error;
    }
  }

  async cancelAllJobs(): Promise<{ success: boolean; cancelled?: number; error?: string }> {
    try {
      const response = await fetch(`${this.apiUrl}/generate/cancel_all_jobs`, {
        method: 'POST',
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        return { success: false, error: errorData.error || 'Failed to cancel jobs' };
      }
      
      const result = await response.json();
      return { 
        success: true, 
        cancelled: result.cancelled 
      };
    } catch (error) {
      console.error('Error cancelling jobs:', error);
      return { success: false, error: 'Network error' };
    }
  }
}

// Create a singleton instance of the API service
const apiService = new Api();

// Export the singleton instance
export default apiService;

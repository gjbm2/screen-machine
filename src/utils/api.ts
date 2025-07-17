// API service for all backend requests
import { toast } from 'sonner';

// Define default API URLs
const DEFAULT_API_URL = import.meta.env.VITE_API_URL || '/api';

// Types for buckets
export interface ReferenceImageInfo {
  index: number;
  original_filename: string;
  stored_path: string;
  thumbnail_path: string;
  content_type: string;
  size: number;
  source_type: string;
}

export interface BucketItem {
  filename: string;
  url?: string;
  thumbnail_url?: string;
  thumbnail_embedded?: string;
  favorite: boolean;
  metadata?: Record<string, any>;
  created_at?: number;
  reference_images?: ReferenceImageInfo[];
}

export interface Bucket {
  name: string;
  items: BucketItem[];
  metadata?: Record<string, any>;
  published?: string;
  publishedAt?: string;
  raw_url?: string;
  thumbnail_url?: string;
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
  hidden?: boolean;
  groups?: string[];
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
    reference_images?: ReferenceImageInfo[];
  }>;
  published: string | null;
  publishedAt: string | null;
  raw_url: string | null;
  thumbnail_url: string | null;
  favorites: string[];
  sequence: string[];
  error?: string;
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
		  // Convert "auto" to null so backend can apply default resolution logic
		  workflow: workflow === 'auto' ? null : workflow,
		  params: workflowParams || {},
		  global_params: global_params || { batch_size: 1 },
		  batch_id,
		  has_reference_image: (imageFiles && imageFiles.length > 0) || false,
		};

		// Process and normalize reference URLs
		if (referenceUrls && referenceUrls.length > 0) {
		  console.log(`[api] Processing ${referenceUrls.length} reference URLs:`, referenceUrls);
		  
		  // Ensure all URLs have proper formatting
		  const normalizedUrls = referenceUrls.map(url => {
			// Check if it's a data URI (from camera on mobile)
			if (url.startsWith('data:')) {
			  console.log(`[api] Found data URI (from camera): ${url.substring(0, 50)}...`);
			  return url; // Keep data URIs as-is
			}
			// If it's a relative path starting with /output, make it an absolute URL
			if (url.startsWith('/output/') || url.startsWith('/api/')) {
			  // Get the current origin (protocol + hostname + port)
			  const origin = window.location.origin;
			  const absoluteUrl = `${origin}${url}`;
			  console.log(`[api] Converting relative URL to absolute: ${url} → ${absoluteUrl}`);
			  return absoluteUrl;
			}
			return url;
		  });
		  
		  jsonData.referenceUrls = normalizedUrls;
		  console.log(`[api] Final referenceUrls being sent to backend:`, normalizedUrls.length, 'URLs');
		}

		if (placeholders && placeholders.length > 0) {
		  jsonData.placeholders = placeholders;
		}

		if (workflowParams?.publish_destination) {
		  console.log(`[api] Publishing to destination:`, workflowParams.publish_destination);
		}

		// Handle refiner - convert "auto" to null, send "none" explicitly
		if (refiner && refiner !== 'auto') {
		  console.log(`[api] Using refiner:`, refiner);
		  jsonData.refiner = refiner;
		  if (refiner_params) {
			console.log(`[api] With refiner params:`, refiner_params);
			jsonData.refiner_params = refiner_params;
		  }
		} else if (refiner === 'auto') {
		  console.log(`[api] Using auto refiner - letting backend decide`);
		  // Don't set refiner field, let backend apply default resolution
		} else {
		  console.log(`[api] No refiner specified`);
		}

		// Enhanced logging to debug mobile issues
		console.log("[api] Sending API payload:", jsonData);
		console.log("[api] Mobile debug - imageFiles:", imageFiles?.length, imageFiles?.map(f => f.name));
		console.log("[api] Mobile debug - referenceUrls:", referenceUrls?.length, referenceUrls?.map(url => url.substring(0, 50) + '...'));
		
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

		const result = await response.json();

		// Dispatch event for the frontend to handle new images and placeholder removal
		if (result.recent_files && result.recent_files.length > 0) {
		  // Get the metadata from the result.images array to include in the event
		  const imagesWithMetadata = result.images.map((img: any, index: number) => ({
			fileName: result.recent_files[index],
			// Preserve the entire image object from backend so the frontend has full metadata access
			metadata: {
			  ...img
			}
		  }));

		  // Dispatch an event to inform the Recent tab about new images with metadata
		  const eventDetail = {
			batchId: result.batch_id,
			files: result.recent_files,
			imagesWithMetadata: imagesWithMetadata
		  };
		  
		  console.log('[api] Dispatching recent:add event with metadata:', eventDetail);
		  window.dispatchEvent(new CustomEvent('recent:add', { detail: eventDetail }));
		}

		return result;
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
    
    // Simulate network delay
    return new Promise((resolve) => {
      // Show we're working
      console.info('[MOCK LOG] [mock-backend]', `Working on generating images...`);
      
      // For realistically simulating the behavior, after 5 seconds, dispatch a real event, then resolve
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
        
        // Generate mock image filenames (what would be saved to disk)
        const mockFilenames = Array(batchSize).fill(0).map((_, index) => {
          const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
          return `${timestamp}_batch-${params.batch_id}_${index}.jpg`;
        });
        
        // Create mock images based on the requested batch size
        const mockImages = Array(batchSize).fill(0).map((_, index) => {            
          return {
            id: `mock-${Date.now()}-${index}`,
            url: getRandomImage(),
            prompt: params.prompt,
            workflow: params.workflow,
            timestamp: Date.now(),
            batch_id: params.batch_id || `batch-${Date.now()}`,
            batch_index: index,
            params: params.params,
            refiner: params.refiner,
            refiner_params: params.refiner_params,
            status: 'completed'
          };
        });
        
        // Dispatch a real event for the Recent tab to handle
        if (mockFilenames.length > 0) {
          const imagesWithMetadata = mockImages.map((img: any, index: number) => ({
            fileName: mockFilenames[index],
            metadata: { ...img }
          }));

          const eventDetail = {
            batchId: params.batch_id,
            files: mockFilenames,
            imagesWithMetadata
          };
          
          window.dispatchEvent(new CustomEvent('recent:add', { detail: eventDetail }));
        }
        
        console.info('[MOCK LOG] [mock-backend]', `Generated ${mockImages.length} mock image(s) successfully!`);
        
        resolve({
          success: true,
          images: mockImages,
          batch_id: params.batch_id || `batch-${Date.now()}`,
          prompt: params.prompt,
          workflow: params.workflow,
          recent_files: mockFilenames
        });
      }, 5000); // 5 seconds
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
        body: JSON.stringify(data),
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
      
      const result = await response.json();
      console.log(`[publishImageUnified] Success!`, result);
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

  // Get instruction queue for a scheduler
  async getInstructionQueue(destinationId: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Getting instruction queue for scheduler ID:', destinationId);
      return {
        status: 'success',
        destination: destinationId,
        queue_size: 2,
        instructions: [
          {
            action: 'generate',
            important: false,
            urgent: false,
            details: {
              prompt: 'A mock generation prompt',
              workflow: 'default'
            }
          },
          {
            action: 'publish',
            important: true,
            urgent: false,
            details: {
              source: 'recent://latest',
              destination: 'mock-destination'
            }
          }
        ]
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/instructions`);
      if (!response.ok) {
        throw new Error('Failed to fetch instruction queue');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching instruction queue:', error);
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

  // Throw an event (unified endpoint)
  async throwEvent(eventData: any) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Throwing event:', eventData);
      return {
        status: 'queued',
        key: eventData.event,
        destinations: [eventData.scope || 'global']
      };
    }
    try {
      // Clone the event data to avoid modifying the original
      const payload = { ...eventData };
      
      // Ensure we have a scope field, using backwards compatibility
      if (!payload.scope) {
        // Convert old format destination/group params to new unified scope parameter
        if (payload.destination) {
          payload.scope = payload.destination;
          delete payload.destination;
        } else if (payload.group) {
          payload.scope = payload.group;
          delete payload.group;
        } else {
          // Default to global if no scope was provided
          payload.scope = 'global';
        }
      }
      
      const response = await fetch(`${this.apiUrl}/schedulers/events/throw`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || err.message || 'Failed to throw event');
      }
      return await response.json();
    } catch (error) {
      console.error('Error throwing event:', error);
      toast.error('Failed to throw event');
      throw error;
    }
  }

  // Get events for a destination
  async getEventsForDestination(destinationId: string) {
    console.log(`[API] getEventsForDestination called for: ${destinationId}`);
    
    if (this.mockMode) {
      console.log(`[API] Mock mode active, returning empty events`);
      // Return mock events
      return {
        queue: [],
        history: []
      };
    }
    try {
      const requestUrl = `${this.apiUrl}/schedulers/events?destination=${encodeURIComponent(destinationId)}`;
      console.log(`[API] Fetching events from URL: ${requestUrl}`);
      
      const response = await fetch(requestUrl);
      
      if (!response.ok) {
        console.error(`[API] Error fetching events: ${response.status} ${response.statusText}`);
        throw new Error('Failed to fetch events for destination');
      }
      
      const data = await response.json();
      console.log(`[API] Successfully received events:`, data);
      
      // Check if we got expected data structure
      if (!data.queue || !data.history) {
        console.warn(`[API] Unexpected response structure:`, data);
      } else {
        console.log(`[API] Received ${data.queue.length} queue items and ${data.history.length} history items`);
      }
      
      return data;
    } catch (error) {
      console.error('[API] Error fetching events for destination:', error);
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

  async purgeBucket(bucketId: string, days?: number): Promise<{ status: string; removed: string[]; error?: string }> {
    const url = new URL(`${this.apiUrl}/buckets/${bucketId}/purge`);
    if (days !== undefined) {
      url.searchParams.append('days', days.toString());
    }
    
    const response = await fetch(url.toString(), {
      method: 'DELETE',
    });
    
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Failed to purge bucket: ${errorText}`);
    }
    
    const result = await response.json();
    if (result.status !== 'purged') {
      throw new Error(result.error || 'Failed to purge bucket');
    }
    
    return result;
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
      const response = await fetch(`${this.apiUrl}/cancel_all_jobs`, {
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

  // Get currently published content for a destination (works for both bucket and non-bucket destinations)
  async getPublishedContent(destinationId: string): Promise<{
    published: string | null;
    publishedAt: string | null;
    raw_url: string | null;
    thumbnail_url: string | null;
  }> {
    try {
      const response = await fetch(`${this.apiUrl}/published/${destinationId}`);
      
      // If no published content, return empty result
      if (response.status === 404) {
        return {
          published: null,
          publishedAt: null,
          raw_url: null,
          thumbnail_url: null
        };
      }
      
      if (!response.ok) {
        throw new Error(`Failed to get published content: ${response.statusText}`);
      }
      
      const data = await response.json();
      console.log('Published content response:', data);
      
      return {
        published: data.filename || null,
        publishedAt: data.published_at || null,
        raw_url: data.raw_url || null,
        thumbnail_url: data.thumbnail_url || null
      };
    } catch (error) {
      console.error(`Error fetching published content for ${destinationId}:`, error);
      return {
        published: null,
        publishedAt: null,
        raw_url: null,
        thumbnail_url: null
      };
    }
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

  // Get publish destinations
  async getPublishDestinations(includeRecent: boolean = false): Promise<PublishDestination[]> {
    try {
      const response = await fetch(`${this.apiUrl}/publish-destinations`);
      if (!response.ok) {
        throw new Error('Failed to fetch publish destinations');
      }
      const destinations = await response.json();
      return destinations
        // Include all non-hidden destinations, plus _recent if includeRecent=true
        .filter((dest: any) => (!dest.hidden) || (includeRecent && dest.id === '_recent'))
        .map((dest: any) => ({
        id: dest.id,
        name: dest.name || dest.id,
        description: dest.description,
        icon: dest.icon,
        has_bucket: dest.has_bucket || false,
        headless: dest.headless || false,
        hidden: dest.hidden || false,
        groups: dest.groups || []
      }));
    } catch (error) {
      console.error('Error fetching publish destinations:', error);
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
      // The `/context/clear` endpoint doesn't exist in the backend
      // Instead, we need to get the current context and then reset each variable
      
      // First, get the current context to see what variables exist
      const contextResponse = await fetch(`${this.apiUrl}/schedulers/${destinationId}/context`);
      
      if (!contextResponse.ok) {
        throw new Error('Failed to fetch current context');
      }
      
      const contextData = await contextResponse.json();
      const variables = contextData.vars || {};
      
      console.log(`[API] clearSchedulerContext - Found ${Object.keys(variables).length} variables to clear`);
      
      // Create a collection of promises to clear each variable
      const clearPromises = Object.keys(variables).map(varName => {
        console.log(`[API] clearSchedulerContext - Clearing variable: ${varName}`);
        return this.setSchedulerContextVar(destinationId, varName, null);
      });
      
      // Wait for all variables to be cleared
      await Promise.all(clearPromises);
      
      return {
        success: true,
        cleared: Object.keys(variables).length
      };
    } catch (error) {
      console.error('Error clearing scheduler context:', error);
      toast.error('Failed to clear scheduler context');
      throw error;
    }
  }

  async getBucketDetails(bucketId: string): Promise<BucketDetails> {
    try {
      const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/complete`);
      
      // If the response is a 404 (not found), the bucket doesn't exist or isn't accessible
      // Return a valid empty bucket details instead of throwing an error
      if (response.status === 404) {
        console.log(`Bucket ${bucketId} not found, returning empty bucket details`);
        return {
          name: bucketId,
          items: [],
          published: null,
          publishedAt: null,
          raw_url: null,
          thumbnail_url: null,
          favorites: [],
          sequence: [],
          error: null
        };
      }
      
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
          // Include reference images array if provided by the API
          reference_images: Array.isArray(file.reference_images) ? file.reference_images : [],
        };
      }) : [];
      
      // Get published info from the published object
      const published = data.published || null;
      
      // Log info about the published image but don't add it to items
      if (published && published.from_bucket === false) {
        console.log('Published image is not from this bucket:', published.filename);
        console.log('Published at:', published.published_at);
        console.log('Raw URL:', published.raw_url);
        console.log('Thumbnail URL:', published.thumbnail_url);
      }
      
      // Ensure we have a valid BucketDetails object
      const bucketDetails = {
        name: data.bucket_id || bucketId,
        items: items,
        published: published?.filename || null,
        publishedAt: published?.published_at || null,
        raw_url: published?.raw_url || null,
        thumbnail_url: published?.thumbnail_url || null,
        favorites: Array.isArray(data.favorites) ? data.favorites : [],
        sequence: Array.isArray(data.sequence) ? data.sequence : [],
        error: null
      };
      
      console.log('Processed bucket details:', bucketDetails);
      return bucketDetails;
    } catch (error) {
      console.error(`Error fetching bucket details for ${bucketId}:`, error);
      // Return an empty bucket details instead of throwing
      return {
        name: bucketId,
        items: [],
        published: null,
        publishedAt: null,
        raw_url: null,
        thumbnail_url: null,
        favorites: [],
        sequence: [],
        error: null
      };
    }
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
    
    // URL encode the filename to handle special characters
    const encodedFilename = encodeURIComponent(filename);
    
    try {
      // Direct API call - no bucket checking
      console.log(`Toggling favorite for ${bucketId}/${filename} (${method})`);
      
      const response = await fetch(`${this.apiUrl}/buckets/${bucketId}/favorite/${encodedFilename}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
      }
    });
      
    if (!response.ok) {
        console.error(`Failed to toggle favorite for ${bucketId}/${filename} (${response.status} ${response.statusText})`);
      throw new Error(`Failed to toggle favorite: ${response.statusText}`);
    }
      
    const data = await response.json();
      console.log(`Success toggling favorite: ${data.status}`);
    return data.status === 'favorited' || data.status === 'unfavorited';
    } catch (error) {
      console.error(`Error toggling favorite for ${bucketId}/${filename}:`, error);
      throw error;
    }
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

  // Get events for ALL destinations
  async getAllEvents() {
    if (this.mockMode) {
      return {
        queue: [],
        history: []
      };
    }
    
    try {
      // First get all destinations
      const destinations = await this.getPublishDestinations();
      const allEvents = {
        queue: [],
        history: []
      };
      
      // Fetch events for each destination
      for (const dest of destinations) {
        try {
          const destEvents = await this.getEventsForDestination(dest.id);
          
          // Add destination ID to each event for reference
          const queueWithDest = destEvents.queue.map(event => ({
            ...event,
            destination_id: dest.id,
            destination_name: dest.name || dest.id
          }));
          
          const historyWithDest = destEvents.history.map(event => ({
            ...event,
            destination_id: dest.id,
            destination_name: dest.name || dest.id
          }));
          
          allEvents.queue.push(...queueWithDest);
          allEvents.history.push(...historyWithDest);
        } catch (err) {
          console.error(`Error fetching events for ${dest.id}:`, err);
        }
      }
      
      return allEvents;
    } catch (error) {
      console.error('Error fetching all events:', error);
      throw new Error('Failed to fetch all events');
    }
  }

  // Clear an event by key
  async clearEventByKey(destinationId: string, eventKey: string, clearHistory: boolean = false) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Clearing event by key:', eventKey, 'from destination:', destinationId);
      return {
        cleared_active: 1,
        cleared_history: clearHistory ? 1 : 0,
        total_cleared: clearHistory ? 2 : 1
      };
    }
    
    try {
      const params = new URLSearchParams({
        destination: destinationId,
        clear_history: clearHistory.toString()
      });
      
      const response = await fetch(`${this.apiUrl}/schedulers/events/${encodeURIComponent(eventKey)}?${params}`, {
        method: 'DELETE',
      });
      
      if (!response.ok) {
        throw new Error(`Failed to clear event: ${response.statusText}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error clearing event by key:', error);
      throw error;
    }
  }
  
  // Clear an event by unique ID
  async clearEventById(destinationId: string, eventId: string, clearHistory: boolean = false) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Clearing event by ID:', eventId, 'from destination:', destinationId);
      return {
        cleared_active: 1,
        cleared_history: clearHistory ? 1 : 0,
        total_cleared: clearHistory ? 2 : 1
      };
    }
    
    try {
      const params = new URLSearchParams({
        destination: destinationId,
        event_id: eventId,
        clear_history: clearHistory.toString()
      });
      
      // We use a dummy key in the URL since the event_id is passed as a parameter
      const response = await fetch(`${this.apiUrl}/schedulers/events/by-id?${params}`, {
        method: 'DELETE',
      });
      
      if (!response.ok) {
        throw new Error(`Failed to clear event: ${response.statusText}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error clearing event by ID:', error);
      throw error;
    }
  }
  
  // Clear all events for a destination
  async clearAllEvents(destinationId: string, clearHistory: boolean = false) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Clearing all events from destination:', destinationId);
      return {
        cleared_active: 5,
        cleared_history: clearHistory ? 10 : 0,
        total_cleared: clearHistory ? 15 : 5
      };
    }
    
    try {
      const params = new URLSearchParams({
        destination: destinationId,
        clear_history: clearHistory.toString()
      });
      
      const response = await fetch(`${this.apiUrl}/schedulers/events?${params}`, {
        method: 'DELETE',
      });
      
      if (!response.ok) {
        throw new Error(`Failed to clear events: ${response.statusText}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error clearing all events:', error);
      throw error;
    }
  }

  // Get published content for a specific destination
  async getPublishedContentForDestination(destinationId: string): Promise<{
    published: string | null;
    publishedAt: string | null;
    raw_url: string | null;
    thumbnail_url: string | null;
    meta?: any;
  }> {
    try {
      const response = await fetch(`${this.apiUrl}/published/${destinationId}`);
      
      if (!response.ok) {
        throw new Error(`Failed to get published content: ${response.statusText}`);
      }
      
      const data = await response.json();
      return {
        published: data.published || null,
        publishedAt: data.published_at || null,
        raw_url: data.raw_url || null,
        thumbnail_url: data.thumbnail_url || null,
        meta: data.meta || {}
      };
    } catch (error) {
      console.error(`Error fetching published content for ${destinationId}:`, error);
      return {
        published: null,
        publishedAt: null,
        raw_url: null,
        thumbnail_url: null,
        meta: {}
      };
    }
  }

  // Get the ambient mask data for a display
  async getMask(destinationId: string): Promise<{
    brightness: number;
    warm_hex: string;
    warm_alpha: number;
    timestamp: string;
  }> {
    const response = await fetch(`${this.apiUrl}/${destinationId}/mask`);
    if (!response.ok) {
      throw new Error('Failed to fetch mask');
    }
    return response.json();
  }

  // Enable masking for a display
  async enableMask(destinationId: string): Promise<{ status: string; destination: string }> {
    const response = await fetch(`${this.apiUrl}/${destinationId}/maskon`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error('Failed to enable mask');
    }
    return response.json();
  }

  // Disable masking for a display
  async disableMask(destinationId: string): Promise<{ status: string; destination: string }> {
    const response = await fetch(`${this.apiUrl}/${destinationId}/maskoff`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error('Failed to disable mask');
    }
    return response.json();
  }

  // Get the current mask state for a display
  async getMaskState(destinationId: string): Promise<{ enabled: boolean; destination: string }> {
    const response = await fetch(`${this.apiUrl}/${destinationId}/maskstate`);
    if (!response.ok) {
      throw new Error('Failed to get mask state');
    }
    return response.json();
  }

  async getLightsense() {
    const response = await fetch(`${this.apiUrl}/lightsensor/lightsense`);
    if (!response.ok) {
      throw new Error('Failed to fetch lightsense data');
    }
    return await response.json();
  }

  // Get intensity settings
  async getIntensitySettings(): Promise<any> {
    try {
      const response = await fetch(`${this.apiUrl}/lightsensor/intensity-settings`);
      if (!response.ok) {
        throw new Error('Failed to fetch intensity settings');
      }
      return await response.json();
    } catch (error) {
      console.error('Error fetching intensity settings:', error);
      throw error;
    }
  }

  // Update intensity settings for a sensor
  async updateIntensitySettings(sensorName: string, settings: { points: Array<{ lux: number; intensity: number }>; target_group: string }): Promise<any> {
    try {
      const response = await fetch(`${this.apiUrl}/lightsensor/intensity-settings/${sensorName}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(settings),
      });
      if (!response.ok) {
        throw new Error('Failed to update intensity settings');
      }
      return await response.json();
    } catch (error) {
      console.error('Error updating intensity settings:', error);
      throw error;
    }
  }
}

// Create a singleton instance of the API service
const apiService = new Api();

// Export the singleton instance
export default apiService;

// API service for all backend requests
import { toast } from 'sonner';

// Define default API URLs
const DEFAULT_API_URL = import.meta.env.VITE_API_URL || '/api';

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

class ApiService {
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
  async publishImage(data: any) {
	// console.info('api.ts', data);
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Publishing image with data:', data);
      return {
        success: true,
        message: 'Mock publish succeeded',
        path: '/mock/path/to/image.jpg'
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/publish-image`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(data),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Publish failed');
      }

      return await response.json();
    } catch (error) {
      console.error('Error publishing image:', error);
      toast.error('Failed to publish image');
      throw error;
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
      console.info('[MOCK BACKEND] Listing schedulers');
      return {
        success: true,
        running: ['mock-scheduler-1', 'mock-scheduler-2']
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers`, {
        method: 'GET',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to list schedulers');
      }

      return await response.json();
    } catch (error) {
      console.error('Error listing schedulers:', error);
      toast.error('Failed to list schedulers');
      throw error;
    }
  }

  // Get scheduler logs
  async getSchedulerLogs(destinationId: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Fetching logs for scheduler ID:', destinationId);
      return {
        success: true,
        log: [
          '[MOCK LOG] Scheduler started',
          '[MOCK LOG] Task executed successfully',
          '[MOCK LOG] Waiting for next execution'
        ]
      };
    }

    try {
      // IMPORTANT: destinationId should be the ID of the destination, not the display name
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}`, {
        method: 'GET',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to fetch scheduler logs');
      }

      return await response.json();
    } catch (error) {
      console.error('Error fetching scheduler logs:', error);
      toast.error('Failed to fetch scheduler logs');
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
      console.info('[MOCK BACKEND] Getting status for scheduler ID:', destinationId);
      return {
        success: true,
        status: 'running',
        destination: destinationId
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/status`, {
        method: 'GET',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to get scheduler status');
      }

      return await response.json();
    } catch (error) {
      console.error('Error getting scheduler status:', error);
      toast.error('Failed to get scheduler status');
      throw error;
    }
  }

  // Get scheduler context
  async getSchedulerContext(destinationId: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Getting context for scheduler ID:', destinationId);
      return {
        success: true,
        context: { vars: { key: 'value' } },
        context_stack: [{ vars: { key: 'value' } }]
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/context`, {
        method: 'GET',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to get scheduler context');
      }

      return await response.json();
    } catch (error) {
      console.error('Error getting scheduler context:', error);
      toast.error('Failed to get scheduler context');
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
      console.info('[MOCK BACKEND] Getting schedule stack for ID:', destinationId);
      return {
        success: true,
        stack: [
          { name: 'Mock Schedule 1', cron: '* * * * *' },
          { name: 'Mock Schedule 2', cron: '0 * * * *' }
        ]
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule/stack`, {
        method: 'GET',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to get schedule stack');
      }

      return await response.json();
    } catch (error) {
      console.error('Error getting schedule stack:', error);
      toast.error('Failed to get schedule stack');
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
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule/${position}`, {
        method: 'GET',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to get schedule at position');
      }

      return await response.json();
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
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/schedule/${position}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(schedule),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to set schedule at position');
      }

      return await response.json();
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
}

// Create a singleton instance of the API service
const apiService = new ApiService();

// Export the singleton instance
export default apiService;

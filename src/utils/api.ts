// API service for all backend requests
import { toast as sonnerToast } from 'sonner';
// Create a simple toast wrapper for API error messages (matches the shadcn/ui toast API)
const toast = {
  error: (message: string) => {
    sonnerToast.error(message, {
      duration: 5000 // Auto-hide after 5 seconds
    });
  },
  success: (message: string) => {
    sonnerToast.success(message, {
      duration: 3000 // Auto-hide after 3 seconds
    });
  }
};

// Define default API URLs
const DEFAULT_API_URL = import.meta.env.VITE_API_URL || '/api';

// Import the PublishService
import { getPublishDestinations, PublishDestination } from '@/services/PublishService';

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

// Bucket API Types
export interface BucketItem {
  filename: string;
  bucket: string;
  thumbnail: string;
  isFavorite: boolean;
  index: number;
  url?: string;
}

export interface Bucket {
  name: string;
  items: BucketItem[];
  published?: string;
  publishedAt?: string;
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

  // Get all available buckets
  async fetchAllBuckets(): Promise<string[]> {
    try {
      if (this.mockMode) {
        // Return mock bucket names
        return ['mock-bucket-1', 'mock-bucket-2', 'mock-bucket-3'];
      }

      const response = await fetch(`${this.apiUrl}/buckets/`);
      
      if (!response.ok) {
        throw new Error(`Failed to fetch buckets: ${response.statusText}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error fetching buckets:', error);
      toast.error('Failed to fetch buckets');
      return [];
    }
  }

  // Get detailed info for a specific bucket
  async fetchBucketDetails(bucketName: string): Promise<Bucket> {
    try {
      if (this.mockMode) {
        // Return mock bucket details
        return {
          name: bucketName,
          items: Array.from({ length: 5 }, (_, i) => ({
            filename: `mock-image-${i}.jpg`,
            bucket: bucketName,
            thumbnail: `https://placeholder.pics/svg/300x300/DEDEDE/555555/Mock%20${i}`,
            isFavorite: Math.random() > 0.5,
            index: i
          }))
        };
      }

      const response = await fetch(`${this.apiUrl}/buckets/${bucketName}/items`);
      
      if (!response.ok) {
        throw new Error(`Failed to fetch bucket details: ${response.statusText}`);
      }
      
      const data = await response.json();
      
      // Check if response includes embedded thumbnails
      if (data.items_with_thumbnails && data.items_with_thumbnails.length > 0) {
        console.log(`Using ${data.items_with_thumbnails.length} embedded thumbnails for ${bucketName}`);
        
        // Use embedded thumbnails
        const items: BucketItem[] = data.items_with_thumbnails.map((item, index) => ({
          filename: item.filename,
          bucket: bucketName,
          thumbnail: item.thumbnail_embedded || `${this.apiUrl}/buckets/${bucketName}/thumbnail/${item.filename}`,
          url: `${this.apiUrl}/buckets/${bucketName}/raw/${item.filename}`,
          isFavorite: (data.favorites || []).includes(item.filename),
          index
        }));
        
        return {
          name: bucketName,
          items,
          published: data.published_meta?.filename,
          publishedAt: data.published_meta?.published_at
        };
      }
      
      // Fallback to constructing thumbnail URLs if no embedded thumbnails
      console.log(`No embedded thumbnails available for ${bucketName}, using URL references`);
      
      // Transform API response to our expected format
      const items: BucketItem[] = (data.sequence || []).map((filename: string, index: number) => ({
        filename,
        bucket: bucketName,
        thumbnail: `${this.apiUrl}/buckets/${bucketName}/thumbnail/${filename}`,
        url: `${this.apiUrl}/buckets/${bucketName}/raw/${filename}`,
        isFavorite: (data.favorites || []).includes(filename),
        index
      }));
      
      return {
        name: bucketName,
        items,
        published: data.published_meta?.filename,
        publishedAt: data.published_meta?.published_at
      };
    } catch (error) {
      console.error(`Error fetching bucket details for ${bucketName}:`, error);
      toast.error(`Failed to fetch details for bucket: ${bucketName}`);
      return { name: bucketName, items: [] };
    }
  }

  // Toggle favorite status of an item
  async toggleFavorite(bucket: string, filename: string, currentState: boolean): Promise<boolean> {
    try {
      if (this.mockMode) {
        return true;
      }

      const method = currentState ? 'DELETE' : 'POST';
      const endpoint = `${this.apiUrl}/buckets/${bucket}/favorite/${filename}`;
      
      const response = await fetch(endpoint, { method });
      
      if (!response.ok) {
        throw new Error(`Failed to ${currentState ? 'unfavorite' : 'favorite'} image`);
      }
      
      const data = await response.json();
      toast.success(`Image ${currentState ? 'unfavorited' : 'favorited'}`);
      return true;
    } catch (error) {
      console.error('Error toggling favorite status:', error);
      toast.error('Failed to update favorite status');
      return false;
    }
  }

  // Delete an image from a bucket
  async deleteImage(bucket: string, filename: string): Promise<boolean> {
    try {
      if (this.mockMode) {
        return true;
      }

      const response = await fetch(`${this.apiUrl}/buckets/${bucket}/${filename}`, {
        method: 'DELETE'
      });
      
      if (!response.ok) {
        throw new Error('Failed to delete image');
      }
      
      toast.success('Image deleted');
      return true;
    } catch (error) {
      console.error('Error deleting image:', error);
      toast.error('Failed to delete image');
      return false;
    }
  }

  // Move an image up or down in sequence
  async moveImage(bucket: string, filename: string, direction: 'up' | 'down'): Promise<boolean> {
    try {
      if (this.mockMode) {
        return true;
      }

      const response = await fetch(`${this.apiUrl}/buckets/${bucket}/move-${direction}/${filename}`, {
        method: 'POST'
      });
      
      if (!response.ok) {
        throw new Error(`Failed to move image ${direction}`);
      }
      
      return true;
    } catch (error) {
      console.error(`Error moving image ${direction}:`, error);
      toast.error(`Failed to move image ${direction}`);
      return false;
    }
  }

  // Move an image to a specific position in sequence
  async moveImageToPosition(bucket: string, filename: string, position: number): Promise<boolean> {
    try {
      if (this.mockMode) {
        return true;
      }

      const response = await fetch(`${this.apiUrl}/buckets/${bucket}/move-to/${filename}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ position })
      });
      
      if (!response.ok) {
        throw new Error('Failed to move image to position');
      }
      
      toast.success(`Image moved to position ${position}`);
      return true;
    } catch (error) {
      console.error('Error moving image to position:', error);
      toast.error('Failed to move image to position');
      return false;
    }
  }

  // Copy an image to another bucket
  async copyImageToBucket(sourceBucket: string, targetBucket: string, filename: string): Promise<boolean> {
    try {
      if (this.mockMode) {
        return true;
      }

      const response = await fetch(`${this.apiUrl}/buckets/move`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          source_bucket: sourceBucket,
          dest_bucket: targetBucket,
          filename: filename,
          copy: true
        })
      });
      
      if (!response.ok) {
        throw new Error('Failed to copy image');
      }
      
      toast.success(`Image copied to ${targetBucket}`);
      return true;
    } catch (error) {
      console.error('Error copying image:', error);
      toast.error('Failed to copy image');
      return false;
    }
  }

  // Publish an image from a bucket
  async publishBucketImage(bucket: string, filename: string): Promise<boolean> {
    try {
      if (this.mockMode) {
        return true;
      }

      const response = await fetch(`${this.apiUrl}/buckets/${bucket}/publish/${filename}`, {
        method: 'POST'
      });
      
      if (!response.ok) {
        throw new Error('Failed to publish image');
      }
      
      toast.success('Image published');
      return true;
    } catch (error) {
      console.error('Error publishing image:', error);
      toast.error('Failed to publish image');
      return false;
    }
  }

  // Get the currently published image info for a bucket
  async getPublishedInfo(bucket: string): Promise<any> {
    try {
      if (this.mockMode) {
        return {
          filename: 'mock-published.jpg',
          published_at: new Date().toISOString(),
          raw_url: 'https://placeholder.pics/svg/800x600/DEDEDE/555555/Published%20Image',
          thumbnail_url: 'https://placeholder.pics/svg/300x300/DEDEDE/555555/Thumbnail'
        };
      }

      const response = await fetch(`${this.apiUrl}/buckets/${bucket}/published`);
      
      if (!response.ok) {
        throw new Error('Failed to get published image info');
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error getting published image info:', error);
      return null;
    }
  }

  // Upload an image to a bucket
  async uploadToBucket(bucket: string, file: File): Promise<any> {
    try {
      if (this.mockMode) {
        return {
          status: 'stored',
          filename: file.name,
          thumbnail: 'ok'
        };
      }

      const formData = new FormData();
      formData.append('file', file);
      
      const response = await fetch(`${this.apiUrl}/buckets/${bucket}/upload`, {
        method: 'POST',
        body: formData
      });
      
      if (!response.ok) {
        throw new Error('Failed to upload image');
      }
      
      const result = await response.json();
      toast.success('Image uploaded successfully');
      return result;
    } catch (error) {
      console.error('Error uploading image:', error);
      toast.error('Failed to upload image to bucket');
      return null;
    }
  }

  // Create a new bucket
  async createBucket(bucketName: string): Promise<boolean> {
    try {
      if (this.mockMode) {
        return true;
      }

      const response = await fetch(`${this.apiUrl}/buckets/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ bucket_name: bucketName })
      });
      
      if (!response.ok) {
        throw new Error('Failed to create bucket');
      }
      
      toast.success(`Bucket "${bucketName}" created`);
      return true;
    } catch (error) {
      console.error('Error creating bucket:', error);
      toast.error('Failed to create bucket');
      return false;
    }
  }

  // Get the API URL
  getApiUrl(): string {
    return this.apiUrl;
  }

  // Purge non-favorite images from a bucket
  async purgeNonFavorites(bucket: string): Promise<boolean> {
    try {
      if (this.mockMode) {
        return true;
      }

      const response = await fetch(`${this.apiUrl}/buckets/${bucket}/purge-non-favorites`, {
        method: 'POST'
      });
      
      if (!response.ok) {
        throw new Error('Failed to purge non-favorite images');
      }
      
      toast.success('Non-favorite images purged');
      return true;
    } catch (error) {
      console.error('Error purging non-favorite images:', error);
      toast.error('Failed to purge non-favorite images');
      return false;
    }
  }

  // Re-index a bucket
  async reindexBucket(bucket: string): Promise<boolean> {
    try {
      if (this.mockMode) {
        return true;
      }

      const response = await fetch(`${this.apiUrl}/buckets/${bucket}/reindex`, {
        method: 'POST'
      });
      
      if (!response.ok) {
        throw new Error('Failed to re-index bucket');
      }
      
      toast.success('Bucket re-indexed');
      return true;
    } catch (error) {
      console.error('Error re-indexing bucket:', error);
      toast.error('Failed to re-index bucket');
      return false;
    }
  }

  // Extract JSON from a bucket
  async extractJson(bucket: string): Promise<boolean> {
    try {
      if (this.mockMode) {
        return true;
      }

      const response = await fetch(`${this.apiUrl}/buckets/${bucket}/extract-json`, {
        method: 'POST'
      });
      
      if (!response.ok) {
        throw new Error('Failed to extract JSON');
      }
      
      toast.success('JSON extracted');
      return true;
    } catch (error) {
      console.error('Error extracting JSON:', error);
      toast.error('Failed to extract JSON');
      return false;
    }
  }

  // Get all available destinations with scheduler status
  async getDestinations() {
    // First get all publish destinations
    const publishDestinations = getPublishDestinations();
    
    // For each destination, check if scheduler is running
    const destinationsWithStatus = await Promise.all(
      publishDestinations.map(async (destination) => {
        try {
          // In mock mode, just return mock status
          if (this.mockMode) {
            return {
              ...destination,
              scheduler_running: Math.random() > 0.5, // Randomly set status for mock
              scheduler_status: Math.random() > 0.7 ? 'paused' : 'running'
            };
          }
          
          // Get scheduler status for this destination
          const response = await fetch(`${this.apiUrl}/schedulers/${destination.id}/status`, {
            method: 'GET',
          });
          
          if (!response.ok) {
            return {
              ...destination,
              scheduler_running: false,
              scheduler_status: 'stopped'
            };
          }
          
          const status = await response.json();
          return {
            ...destination,
            scheduler_running: status.is_running || status.is_paused,
            scheduler_status: status.status
          };
        } catch (error) {
          console.error(`Error checking scheduler status for ${destination.id}:`, error);
          return {
            ...destination,
            scheduler_running: false,
            scheduler_status: 'error'
          };
        }
      })
    );
    
    return {
      destinations: destinationsWithStatus
    };
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

  // Get the next scheduled action for a destination
  async getNextScheduledAction(destinationId: string) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Getting next scheduled action for ID:', destinationId);
      return {
        success: true,
        destination: destinationId,
        next_action: {
          has_next_action: true,
          next_time: "12:00",
          description: "Mock scheduled action",
          minutes_until_next: 60,
          timestamp: new Date().toISOString()
        }
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/${destinationId}/next_action`, {
        method: 'GET',
      });

      // If we get a 404, it just means the scheduler doesn't exist yet
      if (response.status === 404) {
        return {
          success: true,
          destination: destinationId,
          next_action: {
            has_next_action: false,
            next_time: null,
            description: null,
            minutes_until_next: null,
            timestamp: new Date().toISOString()
          }
        };
      }

      if (!response.ok) {
        // Try to parse the error response as JSON, but handle it gracefully if it's HTML
        try {
          const errorText = await response.text();
          // Check if the response looks like HTML
          if (errorText.trim().startsWith('<!DOCTYPE') || errorText.trim().startsWith('<html')) {
            console.error('Received HTML response instead of JSON for next_action endpoint');
            return {
              success: false,
              destination: destinationId,
              next_action: null,
              error: 'Received HTML response from API'
            };
          }
          
          // Try to parse as JSON
          const err = JSON.parse(errorText);
          throw new Error(err.error || 'Failed to get next scheduled action');
        } catch (parseError) {
          throw new Error('Failed to parse API response');
        }
      }

      // Try to handle the content safely
      try {
        const data = await response.json();
        return data;
      } catch (jsonError) {
        console.error('Error parsing next action JSON:', jsonError);
        return {
          success: false,
          destination: destinationId,
          next_action: null,
          error: 'Invalid JSON response'
        };
      }
    } catch (error) {
      console.error('Error getting next scheduled action:', error);
      // Don't toast this error as it might be expected
      return {
        success: false,
        destination: destinationId,
        next_action: null,
        error: error.message || 'Unknown error'
      };
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

      // If we get a 404, it just means the scheduler doesn't exist yet, return empty context
      if (response.status === 404) {
        return {
          success: true,
          context: { vars: {} },
          context_stack: []
        };
      }

      if (!response.ok) {
        const err = await response.json().catch(() => ({ error: 'Unknown error' }));
        throw new Error(err.error || 'Failed to get scheduler context');
      }

      const responseData = await response.json();
      
      // Log the response structure to help with debugging
      console.debug(`API: Scheduler context response structure for ${destinationId}:`, 
                   Object.keys(responseData));
      
      // Handle the case when the response is a direct context object
      if (responseData.vars !== undefined || responseData.last_generated !== undefined) {
        console.debug(`API: Detected direct context object for ${destinationId}`);
        // If the response is a direct context object, wrap it in our expected format
        return responseData;
      }
      
      // Otherwise return the response as is
      return responseData;
    } catch (error) {
      console.error('Error getting scheduler context:', error);
      // Don't toast this error as it's expected for new destinations
      // Return empty context instead of throwing
      return {
        success: true,
        context: { vars: {} },
        context_stack: []
      };
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

  // Set a specific context variable for a scheduler
  async setSchedulerContextVar(destinationId: string, varName: string, varValue: any) {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Setting context variable for scheduler ID:', destinationId, `${varName}=`, varValue);
      return {
        status: 'success',
        var_name: varName,
        var_value: varValue,
        vars: { [varName]: varValue }
      };
    }

    try {
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
        throw new Error(err.error || 'Failed to set variable');
      }

      return await response.json();
    } catch (error) {
      console.error(`Error setting scheduler variable ${varName}:`, error);
      toast.error(`Failed to set variable ${varName}`);
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

      // If we get a 404, it just means the scheduler doesn't exist yet, return an empty stack
      if (response.status === 404) {
        return {
          success: true,
          stack: []
        };
      }

      if (!response.ok) {
        const err = await response.json().catch(() => ({ error: 'Unknown error' }));
        throw new Error(err.error || 'Failed to get schedule stack');
      }

      return await response.json();
    } catch (error) {
      console.error('Error getting schedule stack:', error);
      // Don't toast this error as it's expected for new destinations
      // Return an empty stack instead of throwing
      return {
        success: true,
        stack: []
      };
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

  // Get all scheduler statuses at once
  async getAllSchedulerStatuses() {
    if (this.mockMode) {
      console.info('[MOCK BACKEND] Getting all scheduler statuses');
      return {
        success: true,
        statuses: {
          'mock-destination-1': {
            status: 'running',
            is_running: true,
            is_paused: false,
            next_action: {
              has_next_action: true,
              next_time: "12:00",
              description: "Mock scheduled action",
              minutes_until_next: 60,
              timestamp: new Date().toISOString()
            }
          },
          'mock-destination-2': {
            status: 'paused',
            is_running: false,
            is_paused: true,
            next_action: null
          }
        }
      };
    }

    try {
      const response = await fetch(`${this.apiUrl}/schedulers/all/status`, {
        method: 'GET',
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.error || 'Failed to get all scheduler statuses');
      }

      return await response.json();
    } catch (error) {
      console.error('Error getting all scheduler statuses:', error);
      // Don't toast this error as it might be called frequently
      return {
        success: false,
        statuses: {}
      };
    }
  }
}

// Create a singleton instance of the API service
const apiService = new ApiService();

// Export the singleton instance
export default apiService;

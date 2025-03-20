
import config from '../config';
import { toast } from 'sonner';

// Mock data for sandbox environment
const MOCK_IMAGES = [
  {
    id: "mock-1",
    url: "https://images.unsplash.com/photo-1543466835-00a7907e9de1?q=80&w=1974&auto=format&fit=crop",
    prompt: "Cute dog portrait",
    workflow: "text-to-image",
    timestamp: Date.now(),
    params: { style: "Photorealistic" },
    batch_id: "mock-batch-1",
    batch_index: 0
  },
  {
    id: "mock-2",
    url: "https://images.unsplash.com/photo-1579783902614-a3fb3927b6a5?q=80&w=1945&auto=format&fit=crop",
    prompt: "Abstract painting",
    workflow: "artistic-style-transfer",
    timestamp: Date.now() - 10000,
    params: { style: "Abstract" },
    batch_id: "mock-batch-2",
    batch_index: 0
  },
  {
    id: "mock-3",
    url: "https://images.unsplash.com/photo-1692891873526-61e7e87ea428?q=80&w=1780&auto=format&fit=crop",
    prompt: "Mountain landscape",
    workflow: "text-to-image",
    timestamp: Date.now() - 20000,
    params: { style: "Photorealistic" },
    batch_id: "mock-batch-3",
    batch_index: 0
  }
];

// API utility class for making requests to the backend
class ApiService {
  private baseUrl: string;
  private isMockMode: boolean;
  private mockLogs: string[] = [];

  constructor() {
    this.baseUrl = config.apiUrl;
    
    // Check if we should use mock mode (when backend is unavailable)
    this.isMockMode = window.location.hostname.includes('lovableproject.com') || 
                     window.location.hostname.includes('gptengineer.app');
    
    if (this.isMockMode) {
      console.log('API Service running in mock mode - backend simulation active');
    }
  }

  // Helper method to determine if we should use mock data
  private async testBackendConnection(): Promise<boolean> {
    if (this.isMockMode) {
      try {
        // Try to connect to backend
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1000);
        
        const response = await fetch(`${this.baseUrl}/logs?limit=1`, {
          signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        if (response.ok) {
          this.isMockMode = false;
          console.log('Switched to real backend mode');
          return true;
        }
      } catch (error) {
        // Connection failed, stay in mock mode
      }
    }
    
    return !this.isMockMode;
  }

  // Send a log message to the backend
  async sendLog(message: string, source: string = 'frontend') {
    try {
      await this.testBackendConnection();
      
      if (this.isMockMode) {
        // Store locally in mock mode
        const timestamp = new Date().toISOString();
        this.mockLogs.push(`[${timestamp}] [${source}] ${message}`);
        console.log(`[MOCK LOG] [${source}] ${message}`);
        return { success: true };
      }
      
      const response = await fetch(`${this.baseUrl}/log`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message, source }),
      });
      
      if (!response.ok) {
        console.error(`API error: ${response.status}`);
        return null;
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error sending log:', error);
      return null;
    }
  }

  // Get logs from the backend
  async getLogs(limit: number = 100) {
    try {
      await this.testBackendConnection();
      
      if (this.isMockMode) {
        // Return mock logs in mock mode
        return { logs: this.mockLogs.slice(-limit) };
      }
      
      const response = await fetch(`${this.baseUrl}/logs?limit=${limit}`);
      
      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error fetching logs:', error);
      throw error;
    }
  }

  // Generate an image based on prompt and parameters
  async generateImage(data: {
    prompt: string;
    workflow: string;
    params: Record<string, any>;
    global_params: Record<string, any>;
    refiner?: string;
    refiner_params?: Record<string, any>;
    imageFiles?: File[];
    batch_id?: string;
    batch_size?: number;
  }) {
    try {
      await this.testBackendConnection();
      
      if (this.isMockMode) {
        // Simulate network delay
        await this.sendLog(`Generating ${data.batch_size || 1} mock image(s) with prompt: "${data.prompt.substring(0, 50)}${data.prompt.length > 50 ? '...' : ''}"`, 'mock-backend');
        
        await new Promise(resolve => setTimeout(resolve, 2000));
        
        // Create mock response with batch_size images
        const batchSize = data.batch_size || 1;
        const batch_id = data.batch_id || `mock-batch-${Date.now()}`;
        
        const images = [];
        for (let i = 0; i < batchSize; i++) {
          // Cycle through the mock images to get variety
          const mockImage = MOCK_IMAGES[i % MOCK_IMAGES.length];
          
          images.push({
            id: `mock-${Date.now()}-${i}`,
            url: mockImage.url,
            prompt: data.prompt,
            workflow: data.workflow,
            timestamp: Date.now(),
            params: data.params,
            global_params: data.global_params,
            refiner: data.refiner || 'none',
            refiner_params: data.refiner_params || {},
            batch_id: batch_id,
            batch_index: i
          });
        }
        
        await this.sendLog(`Generated ${images.length} mock image(s) successfully!`, 'mock-backend');
        
        return {
          success: true,
          images: images,
          batch_id: batch_id,
          prompt: data.prompt,
          workflow: data.workflow
        };
      }
      
      // Real backend request
      const formData = new FormData();
      formData.append('data', JSON.stringify({
        prompt: data.prompt,
        workflow: data.workflow,
        params: data.params,
        global_params: data.global_params,
        refiner: data.refiner || 'none',
        refiner_params: data.refiner_params || {},
        has_reference_image: data.imageFiles && data.imageFiles.length > 0,
        batch_id: data.batch_id,
        batch_size: data.batch_size || 1
      }));

      // Append image files if present
      if (data.imageFiles && data.imageFiles.length > 0) {
        for (let i = 0; i < data.imageFiles.length; i++) {
          formData.append('image', data.imageFiles[i]);
        }
      }

      // Log the request
      await this.sendLog(`Generating image with prompt: "${data.prompt.substring(0, 50)}${data.prompt.length > 50 ? '...' : ''}"`, 'api-client');

      const response = await fetch(`${this.baseUrl}/generate-image`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Error generating image:', error);
      this.sendLog(`Error generating image: ${error}`, 'api-client');
      throw error;
    }
  }

  // Get all images
  async getImages() {
    try {
      await this.testBackendConnection();
      
      if (this.isMockMode) {
        // Return mock images in mock mode
        return { images: MOCK_IMAGES };
      }
      
      const response = await fetch(`${this.baseUrl}/images`);
      
      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error fetching images:', error);
      throw error;
    }
  }

  // Get a specific image by ID
  async getImage(imageId: string) {
    try {
      await this.testBackendConnection();
      
      if (this.isMockMode) {
        // Find mock image by ID
        const mockImage = MOCK_IMAGES.find(img => img.id === imageId);
        if (mockImage) {
          return mockImage;
        }
        throw new Error('Image not found');
      }
      
      const response = await fetch(`${this.baseUrl}/images/${imageId}`);
      
      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error(`Error fetching image ${imageId}:`, error);
      throw error;
    }
  }
  
  // Get all workflows
  async getWorkflows() {
    try {
      await this.testBackendConnection();
      
      if (this.isMockMode) {
        // Return workflows from local data
        const workflows = await import('../data/workflows.json');
        return { workflows: workflows.default };
      }
      
      const response = await fetch(`${this.baseUrl}/workflows`);
      
      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error fetching workflows:', error);
      throw error;
    }
  }
  
  // Get all refiners
  async getRefiners() {
    try {
      await this.testBackendConnection();
      
      if (this.isMockMode) {
        // Return refiners from local data
        const refiners = await import('../data/refiners.json');
        return { refiners: refiners.default };
      }
      
      const response = await fetch(`${this.baseUrl}/refiners`);
      
      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error fetching refiners:', error);
      throw error;
    }
  }
  
  // Get refiner parameters
  async getRefinerParams(refinerId: string) {
    try {
      await this.testBackendConnection();
      
      if (this.isMockMode) {
        // Return refiner params from local data
        const refinerParams = await import('../data/refiner-params.json');
        const refinerData = refinerParams.default.find((r: any) => r.id === refinerId);
        
        if (refinerData) {
          return refinerData;
        }
        throw new Error('Refiner not found');
      }
      
      const response = await fetch(`${this.baseUrl}/refiner-params/${refinerId}`);
      
      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error(`Error fetching refiner params for ${refinerId}:`, error);
      throw error;
    }
  }
  
  // Get global options
  async getGlobalOptions() {
    try {
      await this.testBackendConnection();
      
      if (this.isMockMode) {
        // Return global options from local data
        const globalOptions = await import('../data/global-options.json');
        return { global_options: globalOptions.default };
      }
      
      const response = await fetch(`${this.baseUrl}/global-options`);
      
      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }
      
      return await response.json();
    } catch (error) {
      console.error('Error fetching global options:', error);
      throw error;
    }
  }
}

// Export a singleton instance
export const apiService = new ApiService();
export default apiService;

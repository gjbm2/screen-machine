// API service for all backend requests
import { toast } from 'sonner';

// Define default API URLs
const DEFAULT_API_URL = import.meta.env.VITE_API_URL || '/api';

// Type for image generation params
interface GenerateImageParams {
  prompt: string;
  workflow: string;
  params?: Record<string, any>;
  global_params?: Record<string, any>;
  refiner?: string;
  refiner_params?: Record<string, any>;
  imageFiles?: File[];
  batch_id?: string;
}

class ApiService {
  private apiUrl: string;
  private mockMode: boolean;

  constructor(apiUrl: string = DEFAULT_API_URL) {
    this.apiUrl = apiUrl;
    // Check if we're running in mock mode (when backend is not available)
    this.mockMode = window.location.hostname.includes('lovableproject.com');
    
    if (this.mockMode) {
      console.info('API Service running in mock mode - backend simulation active');
    }
  }

  // Generate images through the API
  async generateImage(params: GenerateImageParams) {
    try {
      const { prompt, workflow, params: workflowParams, global_params, refiner, refiner_params, imageFiles, batch_id } = params;
      
      // Create form data for multipart request
      const formData = new FormData();
      
      // Create a data object to serialize as JSON
      const jsonData: any = {
        prompt,
        workflow,
        params: workflowParams || {},
        global_params: global_params || {},
        batch_id,
        has_reference_image: (imageFiles && imageFiles.length > 0) || false
      };
      
      // IMPORTANT: Make sure batch_size is included in global_params
      if (!jsonData.global_params.batch_size) {
        jsonData.global_params.batch_size = 1; // Default to 1 if not specified
      }
      
      // Log the params to debug batch size issues
      console.log("[api] Generating image with params:", {
        prompt,
        workflow,
        params: workflowParams,
        global_params,
        batchSize: jsonData.global_params.batch_size
      });
      
      // Add refiner if specified
      if (refiner) {
        jsonData.refiner = refiner;
        if (refiner_params) {
          jsonData.refiner_params = refiner_params;
        }
      }
      
      // Append the JSON data
      formData.append('data', JSON.stringify(jsonData));
      
      // Append image files if any
      if (imageFiles && imageFiles.length > 0) {
        imageFiles.forEach(file => {
          formData.append('image', file);
        });
      }
      
      if (this.mockMode) {
        // In mock mode, return placeholder images
        return this.mockGenerateImage(params);
      }
      
      // Send the actual request to the backend
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
    // Make sure we correctly extract batch size from global_params
    const batchSize = params.global_params?.batch_size || 1;
    console.info('[MOCK LOG] [mock-backend]', `Generating ${batchSize} mock image(s) with prompt: "${params.prompt}"`);
    
    // Simulate network delay
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
        
        // Create mock images based on the requested batch size
        const mockImages = Array(batchSize).fill(0).map((_, index) => ({
          id: `mock-${Date.now()}-${index}`,
          url: getRandomImage(),
          prompt: params.prompt,
          workflow: params.workflow,
          timestamp: Date.now(),
          batch_id: params.batch_id || `batch-${Date.now()}`,
          batch_index: index,
          params: params.params,
          refiner: params.refiner,
          refiner_params: params.refiner_params
        }));
        
        console.info('[MOCK LOG] [mock-backend]', `Generated ${mockImages.length} mock image(s) successfully!`);
        
        resolve({
          success: true,
          images: mockImages,
          batch_id: params.batch_id || `batch-${Date.now()}`,
          prompt: params.prompt,
          workflow: params.workflow
        });
      }, 3000); // 3 second delay to simulate processing
    });
  }
}

// Create a singleton instance of the API service
const apiService = new ApiService();

// Export the singleton instance
export default apiService;

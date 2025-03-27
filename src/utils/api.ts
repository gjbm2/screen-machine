
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
  imageFiles?: (File | string)[];
  batch_id?: string;
  placeholders?: Array<{batch_index: number, placeholder_id: string}>;
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

  // Generate images through the API
  async generateImage(params: GenerateImageParams) {
    try {
      const { prompt, workflow, params: workflowParams, global_params, refiner, refiner_params, imageFiles, batch_id, placeholders } = params;
      
      console.log(`[api] Received request with workflow: ${workflow}`);
      console.log(`[api] Received workflowParams:`, workflowParams);
      console.log(`[api] Received global_params:`, global_params);
      console.log(`[api] Received refiner:`, refiner);
      console.log(`[api] Received refiner_params:`, refiner_params);
      console.log(`[api] Received placeholders:`, placeholders);
      
      // Create form data for multipart request
      const formData = new FormData();
      
      // Create a data object to serialize as JSON
      const jsonData: any = {
        prompt,
        workflow,
        params: workflowParams || {},
        global_params: global_params || { batch_size: 1 },
        batch_id,
        has_reference_image: (imageFiles && imageFiles.length > 0) || false
      };
      
      // Add placeholders if available
      if (placeholders && placeholders.length > 0) {
        jsonData.placeholders = placeholders;
      }
      
      // Log publish destination if present
      if (workflowParams?.publish_destination) {
        console.log(`[api] Publishing to destination:`, workflowParams.publish_destination);
      }
      
      // Add refiner if specified
      if (refiner && refiner !== 'none') {
        console.log(`[api] Using refiner:`, refiner);
        jsonData.refiner = refiner;
        if (refiner_params) {
          console.log(`[api] With refiner params:`, refiner_params);
          jsonData.refiner_params = refiner_params;
        }
      }
      
      // Log the complete API payload for debugging
      console.log("[api] Full API payload:", jsonData);
      
      // Append the JSON data
      formData.append('data', JSON.stringify(jsonData));
      
      // Append image files if any
      if (imageFiles && imageFiles.length > 0) {
        imageFiles.forEach(file => {
          // Only append File objects, not string URLs
          if (file instanceof File) {
            formData.append('image', file);
          }
        });
      }
      
      if (this.mockMode) {
        // In mock mode, return placeholder images
        return this.mockGenerateImage(params);
      }
      
	  console.log('Calling:', `${this.apiUrl}/generate-image`);

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
}

// Create a singleton instance of the API service
const apiService = new ApiService();

// Export the singleton instance
export default apiService;

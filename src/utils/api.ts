
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
  isVerboseDebug?: boolean;
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
      console.info('API Service running in mock mode - backend simulation active');
    }
  }

  // Generate images through the API
  async generateImage(params: GenerateImageParams) {
    try {
      const { prompt, workflow, params: workflowParams, global_params, refiner, refiner_params, imageFiles, batch_id, isVerboseDebug } = params;
      
      if (isVerboseDebug) {
        console.info("[VERBOSE] ⬇️ START API REQUEST ⬇️");
        console.info("[VERBOSE] Generate image API call with params:");
        console.info("[VERBOSE] - Prompt:", prompt);
        console.info("[VERBOSE] - Workflow:", workflow);
        console.info("[VERBOSE] - Batch ID:", batch_id || "new batch");
        console.info("[VERBOSE] - Global params:", global_params);
        console.info("[VERBOSE] - Batch size:", global_params?.batch_size);
      }
      
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
      
      // CRITICAL: Log the original batch_size we received to debug issues
      if (isVerboseDebug) {
        console.info(`[VERBOSE] Original global_params received:`, global_params);
        console.info(`[VERBOSE] Batch size in global_params: ${jsonData.global_params.batch_size}`);
      } else {
        console.log(`[api] Original global_params received:`, global_params);
      }
      
      // CRITICAL: Log the batch_size in the payload before sending
      if (isVerboseDebug) {
        console.info(`[VERBOSE] Generating with batch_size: ${jsonData.global_params.batch_size}`);
        console.info("[VERBOSE] Full API payload:", {
          prompt,
          workflow,
          params: workflowParams,
          global_params,
          batch_size: jsonData.global_params.batch_size,
          refiner: refiner || 'none',
          refiner_params: refiner_params || {}
        });
      } else {
        console.log(`[api] Generating with batch_size:`, jsonData.global_params.batch_size);
        console.log("[api] Full API payload:", {
          prompt,
          workflow,
          params: workflowParams,
          global_params,
          batch_size: jsonData.global_params.batch_size,
          refiner: refiner || 'none',
          refiner_params: refiner_params || {}
        });
      }
      
      // Add refiner if specified
      if (refiner && refiner !== 'none') {
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
      
      if (isVerboseDebug) {
        console.info("[VERBOSE] Sending API request to:", `${this.apiUrl}/generate-image`);
      }
      
      // Send the actual request to the backend
      const response = await fetch(`${this.apiUrl}/generate-image`, {
        method: 'POST',
        body: formData,
      });
      
      if (!response.ok) {
        const errorData = await response.json();
        if (isVerboseDebug) {
          console.info("[VERBOSE] ❌ API error response:", errorData);
        }
        throw new Error(errorData.error || 'Failed to generate image');
      }
      
      const responseData = await response.json();
      
      if (isVerboseDebug) {
        console.info("[VERBOSE] API response successful:", responseData);
        console.info("[VERBOSE] Images received:", responseData.images?.length || 0);
        console.info("[VERBOSE] ⬆️ END API REQUEST ⬆️");
      }
      
      return responseData;
    } catch (error) {
      console.error('Error generating image:', error);
      if (params.isVerboseDebug) {
        console.info("[VERBOSE] ❌ Exception during API call:", error);
        console.info("[VERBOSE] ⬆️ END API REQUEST WITH ERROR ⬆️");
      }
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
    // CRITICAL: Always directly use the provided batch size without any manipulation
    const batchSize = params.global_params?.batch_size || 1;
    
    if (params.isVerboseDebug) {
      console.info('[VERBOSE] [MOCK BACKEND] 🎨 Generating mock images');
      console.info(`[VERBOSE] [MOCK BACKEND] Using batch size: ${batchSize}`);
      console.info(`[VERBOSE] [MOCK BACKEND] Generating ${batchSize} mock image(s) with prompt: "${params.prompt}"`);
      console.info(`[VERBOSE] [MOCK BACKEND] Using workflow: ${params.workflow}`);
    } else {
      console.info('[MOCK LOG] [mock-backend]', `Generating ${batchSize} mock image(s) with prompt: "${params.prompt}"`);
      console.info('[MOCK LOG] [mock-backend]', `Using workflow: ${params.workflow}`);
    }
    
    if (params.refiner && params.refiner !== 'none') {
      if (params.isVerboseDebug) {
        console.info(`[VERBOSE] [MOCK BACKEND] Using refiner: ${params.refiner}`);
      } else {
        console.info('[MOCK LOG] [mock-backend]', `Using refiner: ${params.refiner}`);
      }
    }
    
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
        
        // Create mock images based on the requested batch size - CRITICAL: Use the provided batch size
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
          refiner_params: params.refiner_params,
          status: 'completed'
        }));
        
        if (params.isVerboseDebug) {
          console.info(`[VERBOSE] [MOCK BACKEND] Generated ${mockImages.length} mock image(s) successfully!`);
          console.info(`[VERBOSE] [MOCK BACKEND] Response:`, {
            success: true,
            images: mockImages.length,
            batch_id: params.batch_id || `batch-${Date.now()}`,
          });
        } else {
          console.info('[MOCK LOG] [mock-backend]', `Generated ${mockImages.length} mock image(s) successfully!`);
        }
        
        resolve({
          success: true,
          images: mockImages,
          batch_id: params.batch_id || `batch-${Date.now()}`,
          prompt: params.prompt,
          workflow: params.workflow
        });
      }, 2000); // 2 second delay to simulate processing
    });
  }
}

// Create a singleton instance of the API service
const apiService = new ApiService();

// Export the singleton instance
export default apiService;

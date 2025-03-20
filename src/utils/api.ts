
import config from '../config';

// API utility class for making requests to the backend
class ApiService {
  private baseUrl: string;

  constructor() {
    this.baseUrl = config.apiUrl;
  }

  // Generate an image based on prompt and parameters
  async generateImage(data: {
    prompt: string;
    workflow: string;
    params: Record<string, any>;
    global_params: Record<string, any>;
    refiner?: string;
    refiner_params?: Record<string, any>;
    has_reference_images?: boolean;
    reference_image_count?: number;
    batch_id?: string;
    batch_size?: number;
  }) {
    try {
      const response = await fetch(`${this.baseUrl}/generate-image`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(data),
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Error generating image:', error);
      throw error;
    }
  }

  // Get all images
  async getImages() {
    try {
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

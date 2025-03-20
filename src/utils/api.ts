import config from '../config';

// API utility class for making requests to the backend
class ApiService {
  private baseUrl: string;

  constructor() {
    this.baseUrl = config.apiUrl;
  }

  // Send a log message to the backend
  async sendLog(message: string, source: string = 'frontend') {
    try {
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

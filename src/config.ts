
// Configuration for the application
const config = {
  // API URL for the backend - use environment variable
  apiUrl: import.meta.env.VITE_API_URL || '/api',
  
  // Version information
  version: '1.0.0',
  
  // Default settings
  defaults: {
    workflow: 'text-to-image',
    refiner: 'none',
    batchSize: 1
  }
};

export default config;

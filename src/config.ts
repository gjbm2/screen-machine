
// Configuration for the application
const config = {
  // API URL for the backend
  apiUrl: process.env.NODE_ENV === 'production' 
    ? '/api' 
    : 'http://localhost:5000/api',
  
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

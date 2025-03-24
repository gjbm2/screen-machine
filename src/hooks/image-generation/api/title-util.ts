
/**
 * Utility functions for working with image titles
 */

// Get or initialize counter for image titles
export const getNextImageNumber = (): number => {
  // Initialize counter if it doesn't exist yet
  if (typeof window.imageCounter === 'undefined') {
    window.imageCounter = 0;
  }
  
  // Increment counter and return new value
  window.imageCounter += 1;
  return window.imageCounter;
};

// Generate a formatted title with the current counter, prompt, and workflow
export const generateImageTitle = (prompt: string, workflow: string): string => {
  const imageNumber = getNextImageNumber();
  return `${imageNumber}. ${prompt} (${workflow})`;
};

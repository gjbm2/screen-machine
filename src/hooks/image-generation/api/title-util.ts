
/**
 * Utility functions for working with image titles
 */

// Get or initialize counter for image titles
export const getNextImageNumber = (): number => {
  // Initialize counter if it doesn't exist yet
  if (typeof window.imageCounter === 'undefined') {
    window.imageCounter = 0;
  }
  
  // Return current value without incrementing
  return window.imageCounter;
};

// Increment the counter (only call this when initiating a new generation)
export const incrementImageCounter = (): void => {
  // Initialize counter if it doesn't exist yet
  if (typeof window.imageCounter === 'undefined') {
    window.imageCounter = 0;
  }
  
  // Increment counter
  window.imageCounter += 1;
  console.log('[incrementImageCounter] New counter value:', window.imageCounter);
};

// Generate a formatted title with the current counter, prompt, and workflow
export const generateImageTitle = (prompt: string, workflow: string): string => {
  const imageNumber = getNextImageNumber();
  return `${imageNumber}. ${prompt} (${workflow})`;
};

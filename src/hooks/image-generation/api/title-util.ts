
/**
 * Generates a title for an image based on the prompt and workflow
 */
export const generateImageTitle = (prompt: string, workflow: string): string => {
  // Take the first few words of the prompt
  const promptPart = prompt
    .split(' ')
    .slice(0, 4)
    .join(' ')
    .replace(/[^\w\s]/gi, '')
    .trim();
  
  // Clean up the workflow name
  const workflowPart = workflow
    .replace(/[-_]/g, ' ')
    .trim();
    
  // Combine them with a timestamp for uniqueness
  const timestamp = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14);
  
  return `${promptPart} - ${workflowPart} - ${timestamp}`;
};

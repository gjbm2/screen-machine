
/**
 * Generates a title for an image based on the prompt and workflow
 */
export const generateImageTitle = (prompt?: string, workflow?: string): string => {
  // Generate a title from the prompt (first 30 chars max) and workflow
  const promptText = prompt ? prompt.substring(0, 30) + (prompt.length > 30 ? '...' : '') : 'Untitled';
  const workflowName = workflow || 'default';
  
  return `${promptText} (${workflowName})`;
};

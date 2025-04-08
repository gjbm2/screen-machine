
import typedWorkflows from '@/data/typedWorkflows';

/**
 * Finds an appropriate workflow that can handle images
 * @param currentWorkflowId The current workflow ID
 * @param hasImages Whether there are images that need to be processed
 * @returns The ID of a workflow that can handle images, or the current workflow if images aren't needed
 */
export const findImageCapableWorkflow = (currentWorkflowId: string, hasImages: boolean) => {
  if (!hasImages) {
    return currentWorkflowId;
  }
  
  const workflows = typedWorkflows;
  const currentIndex = workflows.findIndex(w => w.id === currentWorkflowId);
  
  if (currentIndex === -1) {
    const firstImageWorkflow = workflows.find(w => w.input && w.input.includes('image'));
    return firstImageWorkflow ? firstImageWorkflow.id : currentWorkflowId;
  }
  
  const currentWorkflow = workflows[currentIndex];
  if (currentWorkflow.input && currentWorkflow.input.includes('image')) {
    return currentWorkflowId;
  }
  
  // Find next image-capable workflow
  for (let i = currentIndex + 1; i < workflows.length; i++) {
    if (workflows[i].input && workflows[i].input.includes('image')) {
      return workflows[i].id;
    }
  }
  
  // If not found after current, look from beginning
  for (let i = 0; i < currentIndex; i++) {
    if (workflows[i].input && workflows[i].input.includes('image')) {
      return workflows[i].id;
    }
  }
  
  return currentWorkflowId;
};

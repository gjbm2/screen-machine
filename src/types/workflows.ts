
export interface WorkflowParam {
  id: string;
  name: string;
  type: 'select' | 'checkbox' | 'range' | 'text';
  options?: string[];
  default?: string | boolean | number;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  params: WorkflowParam[];
}

// Add a new interface for reference image data
export interface ReferenceImageData {
  url: string;
  name?: string;
  size?: number;
  type?: string;
}

// Add status type for image generation
export type ImageGenerationStatus = 'generating' | 'completed' | 'error' | 'failed';

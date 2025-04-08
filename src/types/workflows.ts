
export interface WorkflowParam {
  id: string;
  name: string;
  type: 'select' | 'checkbox' | 'range' | 'text' | 'number';
  options?: string[];
  default?: string | boolean | number;
}

export interface Workflow {
  id: string;
  name: string;
  icon: string;
  description: string;
  default?: boolean;
  input?: string[];
  params: WorkflowParam[];
}

// Add a new interface for reference image data
export interface ReferenceImageData {
  url: string;
  name?: string;
  size?: number;
  type?: string;
}

// Add status type for image generation - adding 'to_update' to fix TypeScript errors
export type ImageGenerationStatus = 'generating' | 'completed' | 'error' | 'failed' | 'to_update';

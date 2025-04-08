
export interface WorkflowParam {
  id: string;
  name: string;
  type: "select" | "checkbox" | "range" | "text" | "number";
  options?: string[];
  default?: any;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  icon?: string;
  default?: boolean;
  input?: string | string[];
  params: WorkflowParam[];
}

// Add the missing ImageGenerationStatus type that's being imported in multiple files
export type ImageGenerationStatus = 'generating' | 'completed' | 'failed' | 'error' | 'to_update';

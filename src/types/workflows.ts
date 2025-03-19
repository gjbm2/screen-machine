
export interface WorkflowParam {
  id: string;
  name: string;
  type: 'select' | 'checkbox' | 'range';
  options?: string[];
  default?: string | boolean | number;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  params: WorkflowParam[];
}

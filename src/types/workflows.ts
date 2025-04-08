
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

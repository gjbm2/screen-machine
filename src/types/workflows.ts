export interface WorkflowParam {
  id: string;
  name: string;
  type: "select" | "checkbox" | "range" | "text" | "number";
  options?: { value: string; label: string }[];
  default?: string | number | boolean;
}

export interface Workflow {
  id: string;
  name: string;
  icon?: string;
  description: string;
  default?: boolean;
  input?: string[];
  async?: boolean; // Add async property
  params: WorkflowParam[];
}

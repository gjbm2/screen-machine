
import { Workflow, WorkflowParam } from '@/types/workflows';
import rawWorkflowsData from './workflows.json';

// Type assertion function to convert the raw JSON data to properly typed workflows
function convertToTypedWorkflows(data: any[]): Workflow[] {
  return data.map(workflow => ({
    id: workflow.id,
    name: workflow.name,
    icon: workflow.icon,
    description: workflow.description || '', // Ensure description exists with a fallback
    default: workflow.default || false, // Include the default property
    input: workflow.input, // Pass through the input property directly
    params: workflow.params.map((param: any): WorkflowParam => ({
      id: param.id,
      name: param.name,
      type: param.type as "select" | "checkbox" | "range" | "text" | "number",
      options: param.options,
      default: param.default
    }))
  }));
}

// Export the typed version of the workflows data
const typedWorkflows: Workflow[] = convertToTypedWorkflows(rawWorkflowsData);

export default typedWorkflows;

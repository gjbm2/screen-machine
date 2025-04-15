
import { Workflow, WorkflowParam } from '@/types/workflows';
import rawWorkflowsData from './workflows.json';

// Type assertion function to convert the raw JSON data to properly typed workflows
function convertToTypedWorkflows(data: any[]): Workflow[] {
	return data.map(workflow => ({
	  id: workflow.id,
	  name: workflow.name,
	  icon: workflow.icon,
	  description: workflow.description || '',
	  default: workflow.default || false,
	  input: workflow.input || [], 
	  async: workflow.async || false, // Ensure async property is properly passed through
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

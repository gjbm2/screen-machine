import { Workflow, WorkflowParam } from '@/types/workflows';
import rawWorkflowsData from './workflows.json';

function convertToTypedWorkflows(data: any[]): Workflow[] {
  return data.map(workflow => ({
    id: workflow.id,
    name: workflow.name,
    icon: workflow.icon,
    description: workflow.description || '',
    default: workflow.default || false,
    input: workflow.input || [],
    async: typeof workflow.async === 'boolean' ? workflow.async : false, // ✅ Fix here
    runpod_id: workflow.runpod_id,
    alexavisible: workflow.alexavisible,
    style_guidance: workflow.style_guidance,
    style_descriptor: workflow.style_descriptor,
    uses_images: workflow.uses_images,
    processing_stages: workflow.processing_stages?.map((stage: any) => ({
      name: stage.name,
      weight: stage.weight
    })),
    params: (workflow.params || []).map((param: any): WorkflowParam => ({
      id: param.id,
      name: param.name,
      type: param.type as "select" | "checkbox" | "range" | "text" | "number",
      options: param.options,
      default: param.default
    }))
  }));
}

const typedWorkflows: Workflow[] = convertToTypedWorkflows(rawWorkflowsData);

export default typedWorkflows;
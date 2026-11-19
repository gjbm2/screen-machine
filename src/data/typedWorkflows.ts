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

// The deployment's private entries (not in git; routes/utils.merge_catalogue does the same for Flask): an entry with
// a public id changes that entry's fields, a new id goes after the entry its "after" names, else at the end.
function mergeCatalogue(publicData: any[], privateData: any[]): any[] {
  const out = publicData.map(w => ({ ...w }));
  for (const { after, ...p } of privateData || []) {
    const mine = out.find(w => w.id === p.id);
    if (mine) {
      Object.assign(mine, p);
      continue;
    }
    const at = out.findIndex(w => w.id === after);
    out.splice(at < 0 ? out.length : at + 1, 0, p);
  }
  return out;
}

// "hidden": true keeps a workflow's graph and config but leaves it out of what the app offers
const typedWorkflows: Workflow[] = convertToTypedWorkflows(
  mergeCatalogue(rawWorkflowsData as any[], __PRIVATE_WORKFLOWS__).filter(workflow => !workflow.hidden)
);

export default typedWorkflows;
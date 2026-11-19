import { useSyncExternalStore } from 'react';
import { Workflow, WorkflowParam } from '@/types/workflows';
import { requestJson } from '@/shared/http/client';
import rawWorkflowsData from './workflows.json';

function convertToTypedWorkflows(data: any[]): Workflow[] {
  return data.map(workflow => ({
    id: workflow.id,
    name: workflow.name,
    icon: workflow.icon,
    summary: workflow.summary,
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
const offered = (data: any[]) => convertToTypedWorkflows(data.filter(workflow => !workflow.hidden));

// The shared catalogue at first. The deployment's private entries are never in the bundle: Flask hands them only to
// people allowed them (GET /api/workflows/private; docs/ACCOUNTS.md), and loadPrivateWorkflows() merges them in place,
// so code holding this array (a .find at submit time) sees them too.
const typedWorkflows: Workflow[] = offered(rawWorkflowsData as any[]);

let snapshot: Workflow[] = [...typedWorkflows];
const listeners = new Set<() => void>();
let privateLoad: Promise<void> | null = null;

/** Asks Flask for the private entries once per page (it answers [] to anyone not allowed them). */
export function loadPrivateWorkflows(): Promise<void> {
  privateLoad ??= requestJson<any[]>('/workflows/private', undefined, 'Could not read the workflows')
    .then(privateData => {
      if (!Array.isArray(privateData) || privateData.length === 0) return;
      typedWorkflows.splice(0, typedWorkflows.length, ...offered(mergeCatalogue(rawWorkflowsData as any[], privateData)));
      snapshot = [...typedWorkflows];
      listeners.forEach(l => l());
    })
    .catch(() => { privateLoad = null; });
  return privateLoad;
}

/** The workflows this person may use, re-rendering when the private entries arrive. */
export function useWorkflows(): Workflow[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      void loadPrivateWorkflows();
      return () => listeners.delete(listener);
    },
    () => snapshot,
  );
}

export default typedWorkflows;
// The generator's Advanced panel (owner, 19 Nov: "meant to adapt to the workflow in question and surface relevant
// controls"). Everything in it is read from the catalogue, so a new workflow brings its own controls:
//   Workflow       Auto or one workflow: its one-line summary, and its description (model, steps…) on request
//   Its settings   that workflow's params (size, steps, seed, shape…); none under Auto, which picks per prompt
//   Prompt helper  the refiner, with its description
// The old Global settings (endpoint, debug, quality, console), refiner params and resource links were placeholders
// nothing read, so they're gone.
import React, { useState } from 'react';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetFooter,
  SheetClose,
} from '@/components/ui/sheet';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { Workflow } from '@/types/workflows';
import ParamInput from './advanced/ParamInput';
import refinersData from '@/data/refiners.json';
import { useIsMobile } from '@/hooks/use-mobile';

const AUTO_WORKFLOW = { id: 'auto', name: 'Auto', summary: 'Picks the best one for your prompt' };
const AUTO_REFINER = { id: 'auto', name: 'Auto', description: 'Picks the best helper for the workflow and prompt' };

interface AdvancedOptionsProps {
  workflows: Workflow[];
  selectedWorkflow: string;
  onWorkflowChange: (workflowId: string) => void;
  params: Record<string, any>;
  onParamChange: (paramId: string, value: any) => void;
  onResetParams: () => void;
  selectedRefiner?: string;
  onRefinerChange?: (refinerId: string) => void;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
}

const AdvancedOptions: React.FC<AdvancedOptionsProps> = ({
  workflows,
  selectedWorkflow,
  onWorkflowChange,
  params,
  onParamChange,
  onResetParams,
  selectedRefiner = 'auto',
  onRefinerChange = () => {},
  isOpen = false,
  onOpenChange = () => {},
}) => {
  const isMobile = useIsMobile();
  const [aboutOpen, setAboutOpen] = useState(false);

  const workflow = selectedWorkflow === 'auto' ? null : workflows.find(w => w.id === selectedWorkflow) ?? null;
  const settings = workflow?.params ?? [];
  const changed = settings.some(p => params[p.id] !== undefined && params[p.id] !== p.default);
  const refiner = selectedRefiner === 'auto' ? AUTO_REFINER : refinersData.find(r => r.id === selectedRefiner);

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent
        className={`${isMobile ? 'w-[85%]' : 'sm:max-w-md'} overflow-y-auto`}
        onPointerDownOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.stopPropagation()}
      >
        <SheetHeader className="text-left p-0">
          <SheetTitle>Advanced</SheetTitle>
          <SheetDescription>The workflow, its settings and the prompt helper</SheetDescription>
        </SheetHeader>

        <div className="py-6 space-y-6">
          <section className="space-y-2">
            <label className="text-sm font-medium" htmlFor="advanced-workflow">Workflow</label>
            <Select value={workflow ? workflow.id : 'auto'} onValueChange={(id) => { setAboutOpen(false); onWorkflowChange(id); }}>
              <SelectTrigger id="advanced-workflow" className="w-full text-left">
                <SelectValue placeholder="Auto" />
              </SelectTrigger>
              <SelectContent>
                {[AUTO_WORKFLOW, ...workflows].map((w) => (
                  <SelectItem key={w.id} value={w.id}>{w.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{(workflow ?? AUTO_WORKFLOW).summary}</p>
            {workflow?.description && (
              <button type="button" className="text-xs underline underline-offset-2" onClick={() => setAboutOpen(o => !o)} aria-expanded={aboutOpen}>
                {aboutOpen ? 'Less' : 'More about it'}
              </button>
            )}
            {aboutOpen && workflow?.description && <p className="text-xs text-muted-foreground">{workflow.description}</p>}
          </section>

          <section className="space-y-3 rounded-md border p-3">
            <div className="flex items-center justify-between gap-2">
              <h3 className="text-sm font-medium">{workflow ? `${workflow.name} settings` : 'Settings'}</h3>
              {changed && (
                <button type="button" className="text-xs underline underline-offset-2" onClick={onResetParams}>
                  Back to defaults
                </button>
              )}
            </div>
            {!workflow && (
              <p className="text-sm text-muted-foreground">
                Auto picks a workflow for each prompt. Choose one above to set its size, steps or seed.
              </p>
            )}
            {workflow && settings.length === 0 && (
              <p className="text-sm text-muted-foreground">This workflow has nothing to set.</p>
            )}
            {settings.map((param) => (
              <ParamInput key={param.id} param={param} value={params[param.id]} onChange={onParamChange} />
            ))}
          </section>

          <section className="space-y-2">
            <label className="text-sm font-medium" htmlFor="advanced-refiner">Prompt helper</label>
            <Select value={selectedRefiner} onValueChange={onRefinerChange}>
              <SelectTrigger id="advanced-refiner" className="w-full text-left">
                <SelectValue placeholder="Auto" />
              </SelectTrigger>
              <SelectContent>
                {[AUTO_REFINER, ...refinersData].map((r) => (
                  <SelectItem key={r.id} value={r.id}>{r.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{refiner?.description || ''}</p>
          </section>
        </div>

        <SheetFooter className="flex justify-end py-4 sm:py-0">
          <SheetClose asChild>
            <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
          </SheetClose>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
};

export default AdvancedOptions;

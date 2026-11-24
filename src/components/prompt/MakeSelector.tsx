// How a picture is made, in one toolbar button (owner, 24 Nov: the toolbar was "very expensive in real estate terms";
// "move workflows and refiners into a single icon"). The two are one decision, made together and rarely changed: the
// workflow (what kind of picture) and the refiner (how the prompt is rewritten first). The button is the workflow's icon
// with the refiner's on its corner (none: no corner), and both names beside it where there is room; it opens one panel
// with the two lists under a Workflow / Refiner switch. Picking either closes it, as the two buttons did.
//
// Film has no workflow to choose (its own fixed one): the button is the refiner's and opens just its list.
import React, { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Segmented } from '@/components/ui/segmented';
import { useIsMobile, useWindowSize } from '@/hooks/use-mobile';
import { WorkflowProps } from '@/components/prompt-form/types';
import { AUTO_WORKFLOW, WorkflowMenu, workflowIcon } from './WorkflowMenu';
import { RefinerMenu, findRefiner, refinerIcon } from './RefinerMenu';

type Tab = 'workflow' | 'refiner';

const TABS = [
  { value: 'workflow', label: 'Workflow' },
  { value: 'refiner', label: 'Refiner' },
] as const;

const MakeSelector: React.FC<{
  workflows: WorkflowProps[];
  selectedWorkflow: string;
  onWorkflowChange: (workflowId: string) => void;
  selectedRefiner: string;
  onRefinerChange: (refinerId: string) => void;
  /** Film: the refiner only, from these (after Auto). */
  film?: boolean;
  refinerOnly?: string[];
}> = ({ workflows, selectedWorkflow, onWorkflowChange, selectedRefiner, onRefinerChange, film = false, refinerOnly }) => {
  const isMobile = useIsMobile();
  const { width, height: viewportHeight } = useWindowSize();
  const showNames = width >= 600;
  const [isOpen, setIsOpen] = useState(false);
  const [tab, setTab] = useState<Tab>(film ? 'refiner' : 'workflow');
  const [maxContentHeight, setMaxContentHeight] = useState<number | undefined>(undefined);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const workflow = selectedWorkflow === 'auto' ? AUTO_WORKFLOW : workflows.find((w) => w.id === selectedWorkflow);
  const refiner = findRefiner(selectedRefiner);
  const refinerOn = selectedRefiner !== 'none';
  // the refiner named only when one is chosen (Auto · Auto read as a stammer)
  const names = film ? refiner?.name || 'No refiner'
    : `${workflow?.name || 'Workflow'}${refinerOn && selectedRefiner !== 'auto' && refiner ? ` · ${refiner.name}` : ''}`;
  const label = film ? `Refiner: ${refiner?.name || 'none'}`
    : `Workflow: ${workflow?.name || 'Auto'}; refiner: ${refinerOn ? refiner?.name || selectedRefiner : 'none'}`;

  // open on the workflow (the one changed more), after the last pick closed it
  useEffect(() => { if (isOpen) setTab(film ? 'refiner' : 'workflow'); }, [isOpen, film]);

  useEffect(() => {
    if (isOpen && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      setMaxContentHeight(Math.max(viewportHeight - rect.bottom - 16, 150));
    }
  }, [isOpen, isMobile, viewportHeight]);

  const pickWorkflow = (id: string) => { onWorkflowChange(id); setIsOpen(false); };
  const pickRefiner = (id: string) => { onRefinerChange(id); setIsOpen(false); };

  const trigger = (
    <Button
      ref={buttonRef}
      variant="outline"
      className="h-[36px] border border-input hover:bg-purple-500/10 text-purple-700 flex items-center px-3 text-sm"
      type="button"
      aria-label={label}
      title={label}
      data-make-selector
    >
      {film ? refinerIcon(refiner?.icon) : (
        <span className="relative inline-flex">
          {workflowIcon(workflow?.icon)}
          {refinerOn && (
            <span className="absolute -bottom-1.5 -right-2 rounded-full bg-white p-px ring-1 ring-purple-200">
              {refinerIcon(refiner?.icon, 'h-3 w-3')}
            </span>
          )}
        </span>
      )}
      {showNames && <span className={`${film ? 'ml-2' : 'ml-3'} max-w-[200px] truncate text-sm`}>{names}</span>}
    </Button>
  );

  const content = (
    <div className="space-y-2">
      {!film && (
        <Segmented<Tab> value={tab} onChange={setTab} options={TABS} ariaLabel="Workflow or refiner" className="w-full [&>button]:flex-1 [&>button]:justify-center" />
      )}
      {tab === 'workflow' && !film
        ? <WorkflowMenu workflows={workflows} selectedWorkflow={selectedWorkflow} onPick={pickWorkflow} />
        : <RefinerMenu selectedRefiner={selectedRefiner} onPick={pickRefiner} only={refinerOnly} />}
    </div>
  );

  return (
    <div className="flex items-center h-[48px]">
      {/* opens on a click, on a desktop as on a phone (owner, 24 Nov: a hover card opened under the arriving pointer and
          the click then closed and reopened it) */}
      <Popover open={isOpen} onOpenChange={setIsOpen}>
        <PopoverTrigger asChild>{trigger}</PopoverTrigger>
        <PopoverContent align="start" side="bottom" sideOffset={isMobile ? 8 : 4} collisionPadding={8} avoidCollisions
          className="w-72 p-2 overflow-y-auto" style={{ maxHeight: maxContentHeight }}>
          {content}
        </PopoverContent>
      </Popover>
    </div>
  );
};

export default MakeSelector;

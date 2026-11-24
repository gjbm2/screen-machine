// The workflow list (what kind of picture), shown in the toolbar's one "Workflow and refiner" button
// (MakeSelector.tsx; owner, 24 Nov: the toolbar was "very expensive in real estate terms").
import React, { useState } from 'react';
import * as LucideIcons from 'lucide-react';
import { Image, Info, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { WorkflowProps } from '@/components/prompt-form/types';

export const AUTO_WORKFLOW = {
  id: 'auto',
  name: 'Auto',
  summary: 'Picks the best one for your prompt',
  icon: 'Zap'
};

export const workflowIcon = (iconName?: string, className = 'h-5 w-5') => {
  if (!iconName) return <Image className={className} />;
  if (iconName === 'Zap') return <Zap className={className} />;
  const IconComponent = (LucideIcons as any)[iconName];
  return IconComponent ? <IconComponent className={className} /> : <Image className={className} />;
};

export const WorkflowMenu: React.FC<{
  workflows: WorkflowProps[];
  selectedWorkflow: string;
  onPick: (workflowId: string) => void;
}> = ({ workflows, selectedWorkflow, onPick }) => {
  const [aboutOpen, setAboutOpen] = useState<string | null>(null);
  const autoOption = AUTO_WORKFLOW;
  const getWorkflowIcon = (iconName?: string) => workflowIcon(iconName);
  const handleSelectWorkflow = onPick;

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 gap-1">
        {/* Auto option first */}
        <Button
          key="auto"
          variant={selectedWorkflow === 'auto' ? "secondary" : "ghost"}
          size="sm"
          className="justify-start text-sm h-auto py-2"
          onClick={() => handleSelectWorkflow('auto')}
          type="button"
        >
          <div className="mr-2 flex-shrink-0">
            <Zap className="h-5 w-5" />
          </div>
          <div className="flex flex-col items-start overflow-hidden">
            <span className="truncate w-full text-left text-sm font-medium">Auto</span>
            <span className="text-xs text-muted-foreground w-full text-left whitespace-normal">{autoOption.summary}</span>
          </div>
        </Button>
        
        {/* Separator */}
        <div className="border-t border-border my-1" />
        
        {/* Regular workflows: the plain name and one line on what it's for (the catalogue's summary). The rest (the
            model, steps and the like: its description) on hover, or under the row after a tap on its info button. */}
        {workflows.map((workflow) => (
          <div key={workflow.id}>
            <div className="flex items-stretch gap-0.5">
              <Button
                variant={workflow.id === selectedWorkflow ? "secondary" : "ghost"}
                size="sm"
                className="min-w-0 flex-1 justify-start text-sm h-auto py-2"
                onClick={() => handleSelectWorkflow(workflow.id)}
                title={workflow.description || undefined}
                type="button"
              >
                <div className="mr-2 flex-shrink-0">
                  {getWorkflowIcon(workflow.icon)}
                </div>
                <div className="flex flex-col items-start overflow-hidden">
                  <span className="truncate w-full text-left text-sm">{workflow.name}</span>
                  {workflow.summary && (
                    <span className="text-xs text-muted-foreground w-full text-left truncate">{workflow.summary}</span>
                  )}
                </div>
              </Button>
              {workflow.description && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-auto shrink-0 px-2 text-muted-foreground"
                  aria-label={`About ${workflow.name}`}
                  aria-expanded={aboutOpen === workflow.id}
                  onClick={() => setAboutOpen(aboutOpen === workflow.id ? null : workflow.id)}
                  type="button"
                >
                  <Info className="h-4 w-4" />
                </Button>
              )}
            </div>
            {aboutOpen === workflow.id && (
              <p className="px-3 pb-2 text-xs text-muted-foreground whitespace-normal">{workflow.description}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

export default WorkflowMenu;


import React, { useState } from 'react';
import { 
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { Settings, ChevronDown } from 'lucide-react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Workflow, WorkflowParam } from '@/types/workflows';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';

interface AdvancedOptionsProps {
  workflows: Workflow[];
  selectedWorkflow: string;
  onWorkflowChange: (workflowId: string) => void;
  params: Record<string, any>;
  onParamChange: (paramId: string, value: any) => void;
}

const AdvancedOptions: React.FC<AdvancedOptionsProps> = ({
  workflows,
  selectedWorkflow,
  onWorkflowChange,
  params,
  onParamChange,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [isParamsOpen, setIsParamsOpen] = useState(true);
  
  const currentWorkflow = workflows.find(w => w.id === selectedWorkflow) || workflows[0];

  return (
    <Sheet open={isOpen} onOpenChange={setIsOpen}>
      <SheetTrigger asChild>
        <Button variant="outline" className="w-full">
          <Settings className="h-4 w-4 mr-2" />
          Advanced Options
        </Button>
      </SheetTrigger>
      <SheetContent className="sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Advanced Options</SheetTitle>
          <SheetDescription>
            Configure generation settings for your images
          </SheetDescription>
        </SheetHeader>
        
        <div className="py-6 space-y-6">
          <div className="space-y-2">
            <label className="text-sm font-medium">Generation Workflow</label>
            <Select 
              value={selectedWorkflow} 
              onValueChange={onWorkflowChange}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select workflow" />
              </SelectTrigger>
              <SelectContent>
                {workflows.map((workflow) => (
                  <SelectItem key={workflow.id} value={workflow.id}>
                    {workflow.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground mt-1">
              {currentWorkflow?.description}
            </p>
          </div>

          {currentWorkflow?.params && currentWorkflow.params.length > 0 && (
            <Collapsible
              open={isParamsOpen}
              onOpenChange={setIsParamsOpen}
              className="border rounded-md p-2"
            >
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">Parameters</h3>
                <CollapsibleTrigger asChild>
                  <Button variant="ghost" size="sm">
                    <ChevronDown className={`h-4 w-4 transition-transform ${isParamsOpen ? "transform rotate-180" : ""}`} />
                    <span className="sr-only">Toggle parameters</span>
                  </Button>
                </CollapsibleTrigger>
              </div>
              
              <CollapsibleContent className="space-y-4 mt-2">
                {currentWorkflow.params.map((param: WorkflowParam) => (
                  <div key={param.id} className="space-y-1">
                    <Label htmlFor={param.id} className="text-sm font-medium">{param.name}</Label>
                    
                    {param.type === 'select' && (
                      <Select 
                        value={String(params[param.id] !== undefined ? params[param.id] : param.default)}
                        onValueChange={(value) => onParamChange(param.id, value)}
                      >
                        <SelectTrigger className="w-full">
                          <SelectValue placeholder={`Select ${param.name.toLowerCase()}`} />
                        </SelectTrigger>
                        <SelectContent>
                          {param.options?.map((option) => (
                            <SelectItem key={option} value={option}>
                              {option}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                    
                    {param.type === 'checkbox' && (
                      <div className="flex items-center space-x-2">
                        <Checkbox
                          id={param.id}
                          checked={params[param.id] !== undefined ? params[param.id] : Boolean(param.default)}
                          onCheckedChange={(checked) => onParamChange(param.id, Boolean(checked))}
                        />
                        <Label htmlFor={param.id} className="text-sm cursor-pointer">Enable</Label>
                      </div>
                    )}

                    {param.type === 'range' && (
                      <div className="space-y-2">
                        <Slider
                          id={param.id}
                          min={0}
                          max={100}
                          step={1}
                          value={[params[param.id] !== undefined ? Number(params[param.id]) : Number(param.default) || 50]}
                          onValueChange={(value) => onParamChange(param.id, value[0])}
                          className="mt-2"
                        />
                        <div className="flex justify-between text-xs text-muted-foreground">
                          <span>0%</span>
                          <span>Current: {params[param.id] !== undefined ? params[param.id] : param.default || 50}%</span>
                          <span>100%</span>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </CollapsibleContent>
            </Collapsible>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
};

export default AdvancedOptions;

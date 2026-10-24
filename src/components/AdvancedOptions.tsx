
import React, { useState, useEffect } from 'react';
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
import { Separator } from '@/components/ui/separator';
import { Workflow, WorkflowParam } from '@/types/workflows';
import ParamSection from './advanced/ParamSection';
import ResourceLinks from './advanced/ResourceLinks';
import refinersData from '@/data/refiners.json';
import refinerParamsData from '@/data/refiner-params.json';
import { useIsMobile } from '@/hooks/use-mobile';

interface AdvancedOptionsProps {
  workflows: Workflow[];
  selectedWorkflow: string;
  onWorkflowChange: (workflowId: string) => void;
  params: Record<string, any>;
  onParamChange: (paramId: string, value: any) => void;
  globalParams?: Record<string, any>;
  onGlobalParamChange?: (paramId: string, value: any) => void;
  selectedRefiner?: string;
  onRefinerChange?: (refinerId: string) => void;
  refinerParams?: Record<string, any>;
  onRefinerParamChange?: (paramId: string, value: any) => void;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
}

const AdvancedOptions: React.FC<AdvancedOptionsProps> = ({
  workflows,
  selectedWorkflow,
  onWorkflowChange,
  params,
  onParamChange,
  selectedRefiner = 'none',
  onRefinerChange = () => {},
  refinerParams = {},
  onRefinerParamChange = () => {},
  isOpen = false,
  onOpenChange = () => {},
}) => {
  const [isParamsOpen, setIsParamsOpen] = useState(true);
  const [isRefinerParamsOpen, setIsRefinerParamsOpen] = useState(true);
  const [currentRefinerParams, setCurrentRefinerParams] = useState<WorkflowParam[]>([]);
  const isMobile = useIsMobile();
  
  // Handle open/close events
  const handleOpenChange = (open: boolean) => {
    console.log("Advanced options panel - handleOpenChange called with:", open);
    onOpenChange(open);
  };
  
  // "auto": the generator picks for the prompt, so no one workflow's description or parameters apply.
  const chosenWorkflow = workflows.find(w => w.id === selectedWorkflow);
  const currentWorkflow = selectedWorkflow === 'auto' ? undefined : chosenWorkflow || workflows[0];
  const chosenRefiner = refinersData.find(r => r.id === selectedRefiner);

  useEffect(() => {
    const refinerData = refinerParamsData.find(r => r.id === selectedRefiner);
    if (refinerData && refinerData.params) {
      setCurrentRefinerParams(refinerData.params as WorkflowParam[]);
      
      if (refinerData.params) {
        refinerData.params.forEach(param => {
          if (param.default !== undefined && refinerParams[param.id] === undefined) {
            onRefinerParamChange(param.id, param.default);
          }
        });
      }
    } else {
      setCurrentRefinerParams([]);
    }
  }, [selectedRefiner, onRefinerParamChange, refinerParams]);

  const sheetWidth = isMobile ? "w-[85%]" : "sm:max-w-md";

  return (
    <Sheet open={isOpen} onOpenChange={handleOpenChange}>
      <SheetContent 
        className={`${sheetWidth} overflow-y-auto`}
        onPointerDownOutside={(e) => {
          // This prevents the click outside from propagating but allows the dialog to close
          e.preventDefault();
        }}
        onEscapeKeyDown={(e) => {
          // Allow escape key to close the sheet but prevent propagation
          e.stopPropagation();
        }}
      >
        <SheetHeader className="text-left p-0">
          <div className="flex justify-between items-center mb-4">
            <div>
              <SheetTitle>Advanced</SheetTitle>
              <SheetDescription>
                How pictures get made: the style engine, the finish and their settings
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>
        
        <div className="py-6 space-y-6">
          <div className="space-y-2">
            <label className="text-sm font-medium">Style engine</label>
            <Select 
              value={selectedWorkflow} 
              onValueChange={onWorkflowChange}
            >
              <SelectTrigger className="w-full text-left">
                <SelectValue placeholder="Select workflow" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto (picks for your prompt)</SelectItem>
                {workflows.map((workflow) => (
                  <SelectItem key={workflow.id} value={workflow.id}>
                    {workflow.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {currentWorkflow?.description && (
              <p className="text-xs text-muted-foreground mt-1">{currentWorkflow.description}</p>
            )}
          </div>

          {currentWorkflow?.params && currentWorkflow.params.length > 0 && (
            <ParamSection
              title="Style engine settings"
              params={currentWorkflow.params}
              values={params}
              onChange={onParamChange}
              isOpen={isParamsOpen}
              onOpenChange={setIsParamsOpen}
            />
          )}

          <div className="space-y-2">
            <label className="text-sm font-medium">Finish</label>
            <Select 
              value={selectedRefiner} 
              onValueChange={onRefinerChange}
            >
              <SelectTrigger className="w-full text-left">
                <SelectValue placeholder="Select refiner" />
              </SelectTrigger>
              <SelectContent>
                {!refinersData.some(r => r.id === 'auto') && (
                  <SelectItem value="auto">Auto (picks for your prompt)</SelectItem>
                )}
                {refinersData.map((refiner) => (
                  <SelectItem key={refiner.id} value={refiner.id}>
                    {refiner.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedRefiner !== 'auto' && chosenRefiner?.description && (
              <p className="text-xs text-muted-foreground mt-1">{chosenRefiner.description}</p>
            )}
          </div>

          {currentRefinerParams.length > 0 && (
            <ParamSection
              title="Finish settings"
              params={currentRefinerParams}
              values={refinerParams}
              onChange={onRefinerParamChange}
              isOpen={isRefinerParamsOpen}
              onOpenChange={setIsRefinerParamsOpen}
            />
          )}

        </div>
        
        <Separator className="my-4" />
        
        <ResourceLinks />
        
        <SheetFooter className="flex justify-end py-4 sm:py-0 mt-4">
          <SheetClose asChild>
            <Button variant="outline" onClick={() => handleOpenChange(false)}>Close</Button>
          </SheetClose>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
};

export default AdvancedOptions;

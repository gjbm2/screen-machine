
import React, { useState, useEffect } from 'react';
import { 
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
  SheetFooter,
  SheetClose,
} from '@/components/ui/sheet';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { Settings, ChevronDown, ExternalLink } from 'lucide-react';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Workflow, WorkflowParam } from '@/types/workflows';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Separator } from '@/components/ui/separator';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import globalOptionsData from '@/data/global-options.json';
import refinersData from '@/data/refiners.json';
import refinerParamsData from '@/data/refiner-params.json';
import maintenanceLinks from '@/data/maintenance-links.json';

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
  globalParams = {},
  onGlobalParamChange = () => {},
  selectedRefiner = 'none',
  onRefinerChange = () => {},
  refinerParams = {},
  onRefinerParamChange = () => {},
  isOpen = false,
  onOpenChange = () => {},
}) => {
  const [isParamsOpen, setIsParamsOpen] = useState(true);
  const [isRefinerParamsOpen, setIsRefinerParamsOpen] = useState(true);
  const [isGlobalParamsOpen, setIsGlobalParamsOpen] = useState(true);
  const [currentRefinerParams, setCurrentRefinerParams] = useState<any[]>([]);
  
  const currentWorkflow = workflows.find(w => w.id === selectedWorkflow) || workflows[0];

  // Update refiner parameters when selected refiner changes
  useEffect(() => {
    const refinerData = refinerParamsData.find(r => r.id === selectedRefiner);
    setCurrentRefinerParams(refinerData?.params || []);
    
    // Initialize default values for refiner params
    if (refinerData && refinerData.params) {
      const defaultParams: Record<string, any> = {};
      refinerData.params.forEach(param => {
        if (param.default !== undefined && refinerParams[param.id] === undefined) {
          onRefinerParamChange(param.id, param.default);
        }
      });
    }
  }, [selectedRefiner]);

  // Render parameter inputs based on type
  const renderParamInput = (param: WorkflowParam, value: any, onChange: (paramId: string, value: any) => void) => {
    switch (param.type) {
      case 'select':
        return (
          <Select 
            value={String(value !== undefined ? value : param.default)}
            onValueChange={(value) => onChange(param.id, value)}
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
        );
      
      case 'checkbox':
        return (
          <div className="flex items-center space-x-2">
            <Checkbox
              id={param.id}
              checked={value !== undefined ? value : Boolean(param.default)}
              onCheckedChange={(checked) => onChange(param.id, Boolean(checked))}
            />
            <Label htmlFor={param.id} className="text-sm cursor-pointer">Enable</Label>
          </div>
        );
      
      case 'range':
        return (
          <div className="space-y-2">
            <Slider
              id={param.id}
              min={0}
              max={100}
              step={1}
              value={[value !== undefined ? Number(value) : Number(param.default) || 50]}
              onValueChange={(val) => onChange(param.id, val[0])}
              className="mt-2"
            />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>0%</span>
              <span>Current: {value !== undefined ? value : param.default || 50}%</span>
              <span>100%</span>
            </div>
          </div>
        );
      
      case 'text':
        return (
          <Textarea
            id={param.id}
            value={value !== undefined ? value : (param.default || '')}
            onChange={(e) => onChange(param.id, e.target.value)}
            placeholder={`Enter ${param.name.toLowerCase()}`}
            className="resize-none min-h-[100px]"
          />
        );
      
      default:
        return null;
    }
  };

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent className="sm:max-w-md overflow-y-auto">
        <SheetHeader className="text-left p-0">
          <div className="flex justify-between items-center mb-4">
            <div>
              <SheetTitle>Advanced Options</SheetTitle>
              <SheetDescription>
                Configure generation settings for your images
              </SheetDescription>
            </div>
          </div>
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

          {/* Workflow Parameters Section */}
          {currentWorkflow?.params && currentWorkflow.params.length > 0 && (
            <Collapsible
              open={isParamsOpen}
              onOpenChange={setIsParamsOpen}
              className="border rounded-md p-2"
            >
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">Workflow Parameters</h3>
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
                    {renderParamInput(param, params[param.id], onParamChange)}
                  </div>
                ))}
              </CollapsibleContent>
            </Collapsible>
          )}

          {/* Generation Refiner Section */}
          <div className="space-y-2">
            <label className="text-sm font-medium">Generation Refiner</label>
            <Select 
              value={selectedRefiner} 
              onValueChange={onRefinerChange}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select refiner" />
              </SelectTrigger>
              <SelectContent>
                {refinersData.map((refiner) => (
                  <SelectItem key={refiner.id} value={refiner.id}>
                    {refiner.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground mt-1">
              {refinersData.find(r => r.id === selectedRefiner)?.description || ''}
            </p>
          </div>

          {/* Refiner Parameters Section */}
          {currentRefinerParams.length > 0 && (
            <Collapsible
              open={isRefinerParamsOpen}
              onOpenChange={setIsRefinerParamsOpen}
              className="border rounded-md p-2"
            >
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">Refiner Parameters</h3>
                <CollapsibleTrigger asChild>
                  <Button variant="ghost" size="sm">
                    <ChevronDown className={`h-4 w-4 transition-transform ${isRefinerParamsOpen ? "transform rotate-180" : ""}`} />
                    <span className="sr-only">Toggle refiner parameters</span>
                  </Button>
                </CollapsibleTrigger>
              </div>
              
              <CollapsibleContent className="space-y-4 mt-2">
                {currentRefinerParams.map((param: any) => (
                  <div key={param.id} className="space-y-1">
                    <Label htmlFor={param.id} className="text-sm font-medium">{param.name}</Label>
                    {param.description && <p className="text-xs text-muted-foreground mb-2">{param.description}</p>}
                    {renderParamInput(param, refinerParams[param.id], onRefinerParamChange)}
                  </div>
                ))}
              </CollapsibleContent>
            </Collapsible>
          )}

          {/* Global Parameters Section */}
          <Collapsible
            open={isGlobalParamsOpen}
            onOpenChange={setIsGlobalParamsOpen}
            className="border rounded-md p-2"
          >
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium">Global Settings</h3>
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm">
                  <ChevronDown className={`h-4 w-4 transition-transform ${isGlobalParamsOpen ? "transform rotate-180" : ""}`} />
                  <span className="sr-only">Toggle global settings</span>
                </Button>
              </CollapsibleTrigger>
            </div>
            
            <CollapsibleContent className="space-y-4 mt-2">
              {globalOptionsData.map((param: any) => (
                <div key={param.id} className="space-y-1">
                  <Label htmlFor={param.id} className="text-sm font-medium">{param.name}</Label>
                  <p className="text-xs text-muted-foreground mb-2">{param.description}</p>
                  {renderParamInput(param, globalParams[param.id], onGlobalParamChange)}
                </div>
              ))}
            </CollapsibleContent>
          </Collapsible>
        </div>
        
        <Separator className="my-4" />
        
        {/* Maintenance Links */}
        <div className="pb-8">
          <h3 className="text-sm font-medium mb-4">Resources</h3>
          <div className="space-y-3">
            {maintenanceLinks.map((link, index) => (
              <a 
                key={index}
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-start gap-2 text-sm hover:text-primary transition-colors"
              >
                <ExternalLink className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <div>
                  <div className="font-medium">{link.title}</div>
                  <div className="text-xs text-muted-foreground">{link.description}</div>
                </div>
              </a>
            ))}
          </div>
        </div>
        
        <SheetFooter className="absolute bottom-4 right-4">
          <SheetClose asChild>
            <Button variant="outline">Close</Button>
          </SheetClose>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
};

export default AdvancedOptions;

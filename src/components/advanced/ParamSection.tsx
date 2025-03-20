
import React from 'react';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { ChevronDown } from 'lucide-react';
import ParamInput from './ParamInput';
import { WorkflowParam } from '@/types/workflows';

interface ParamSectionProps {
  title: string;
  params: WorkflowParam[];
  values: Record<string, any>;
  onChange: (paramId: string, value: any) => void;
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
}

const ParamSection: React.FC<ParamSectionProps> = ({
  title,
  params,
  values,
  onChange,
  isOpen,
  onOpenChange
}) => {
  if (params.length === 0) {
    return null;
  }

  return (
    <Collapsible
      open={isOpen}
      onOpenChange={onOpenChange}
      className="border rounded-md p-2"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">{title}</h3>
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm">
            <ChevronDown className={`h-4 w-4 transition-transform ${isOpen ? "transform rotate-180" : ""}`} />
            <span className="sr-only">Toggle {title.toLowerCase()}</span>
          </Button>
        </CollapsibleTrigger>
      </div>
      
      <CollapsibleContent className="space-y-4 mt-2">
        {params.map((param) => (
          <ParamInput
            key={param.id}
            param={param}
            value={values[param.id]}
            onChange={onChange}
          />
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
};

export default ParamSection;

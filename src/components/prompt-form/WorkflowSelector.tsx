
import React from 'react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useVerboseDebug } from '@/hooks/use-verbose-debug';

interface WorkflowSelectorProps {
  selectedWorkflow: string;
  workflows: any[];
  onWorkflowChange: (workflowId: string) => void;
  disabled?: boolean;
}

const WorkflowSelector: React.FC<WorkflowSelectorProps> = ({
  selectedWorkflow,
  workflows,
  onWorkflowChange,
  disabled = false
}) => {
  const { logVerbose } = useVerboseDebug();
  
  const handleChange = (value: string) => {
    logVerbose(`Workflow changed to: ${value}`);
    onWorkflowChange(value);
  };
  
  return (
    <Select
      value={selectedWorkflow}
      onValueChange={handleChange}
      disabled={disabled}
    >
      <SelectTrigger className="h-8 text-xs">
        <SelectValue placeholder="Select workflow" />
      </SelectTrigger>
      <SelectContent>
        {workflows.map(workflow => (
          <SelectItem key={workflow.id} value={workflow.id}>
            {workflow.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
};

export default WorkflowSelector;

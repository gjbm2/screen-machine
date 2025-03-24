
import React from 'react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useVerboseDebug } from '@/hooks/use-verbose-debug';

interface RefinerSelectorProps {
  selectedRefiner: string;
  refiners: any[];
  onRefinerChange: (refinerId: string) => void;
  disabled?: boolean;
}

const RefinerSelector: React.FC<RefinerSelectorProps> = ({
  selectedRefiner,
  refiners,
  onRefinerChange,
  disabled = false
}) => {
  const { logVerbose } = useVerboseDebug();
  
  const handleChange = (value: string) => {
    logVerbose(`Refiner changed to: ${value}`);
    onRefinerChange(value);
  };
  
  return (
    <Select
      value={selectedRefiner}
      onValueChange={handleChange}
      disabled={disabled}
    >
      <SelectTrigger className="h-8 text-xs">
        <SelectValue placeholder="Select refiner" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="none">No Refiner</SelectItem>
        {refiners.map(refiner => (
          <SelectItem key={refiner.id} value={refiner.id}>
            {refiner.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
};

export default RefinerSelector;

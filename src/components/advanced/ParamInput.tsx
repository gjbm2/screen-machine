import React from 'react';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { Textarea } from '@/components/ui/textarea';
import { WorkflowParam } from '@/types/workflows';

interface ParamInputProps {
  param: WorkflowParam;
  value: any;
  onChange: (paramId: string, value: any) => void;
}

const ParamInput: React.FC<ParamInputProps> = ({ param, value, onChange }) => {
  // Use default value if value is undefined
  const paramValue = value !== undefined ? value : param.default;

  switch (param.type) {
    case 'select':
      return (
        <div className="space-y-1">
          <Label htmlFor={param.id} className="text-sm font-medium">{param.name}</Label>
          <Select 
            value={String(paramValue)}
            onValueChange={(value) => onChange(param.id, value)}
          >
            <SelectTrigger className="w-full" id={param.id}>
              <SelectValue placeholder={`Select ${param.name.toLowerCase()}`} />
            </SelectTrigger>
            <SelectContent>
              {param.options?.map((option) => {
                // Check if option is an object with value and label properties
                if (typeof option === 'object' && option !== null && 'value' in option && 'label' in option) {
                  return (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  );
                }
                // Handle string options
                return (
                  <SelectItem key={String(option)} value={String(option)}>
                    {String(option)}
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>
        </div>
      );
    
    case 'checkbox':
      return (
        <div className="space-y-1">
          <Label htmlFor={param.id} className="text-sm font-medium">{param.name}</Label>
          <div className="flex items-center space-x-2 mt-2">
            <Checkbox
              id={param.id}
              checked={Boolean(paramValue)}
              onCheckedChange={(checked) => onChange(param.id, Boolean(checked))}
            />
            <Label htmlFor={param.id} className="text-sm cursor-pointer">Enable</Label>
          </div>
        </div>
      );
    
    case 'range':
      return (
        <div className="space-y-2">
          <Label htmlFor={param.id} className="text-sm font-medium">{param.name}</Label>
          <Slider
            id={param.id}
            min={0}
            max={100}
            step={1}
            value={[Number(paramValue) || 50]}
            onValueChange={(val) => onChange(param.id, val[0])}
            className="mt-2"
          />
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>0%</span>
            <span>Current: {paramValue || 50}%</span>
            <span>100%</span>
          </div>
        </div>
      );
    
    case 'text':
      return (
        <div className="space-y-1">
          <Label htmlFor={param.id} className="text-sm font-medium">{param.name}</Label>
          <Textarea
            id={param.id}
            value={String(paramValue || '')}
            onChange={(e) => onChange(param.id, e.target.value)}
            placeholder={`Enter ${param.name.toLowerCase()}`}
            className="resize-none min-h-[100px] mt-1"
          />
        </div>
      );
    
	case 'number':
      return (
        <div className="space-y-1">
          <Label htmlFor={param.id} className="text-sm font-medium">{param.name}</Label>
          <input
            type="number"
            id={param.id}
            value={paramValue ?? ''}
            onChange={(e) => onChange(param.id, e.target.value === '' ? null : Number(e.target.value))}
            placeholder={`Enter ${param.name.toLowerCase()}`}
            className="w-full border border-input bg-background px-3 py-2 text-sm rounded-md shadow-sm"
          />
        </div>
      );
	
    default:
      return null;
  }
};

export default ParamInput;

import React, { useState, useEffect } from 'react';
import { 
  Dialog, 
  DialogContent, 
  DialogHeader, 
  DialogTitle, 
  DialogFooter,
  DialogDescription
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from '@/components/ui/use-toast';
import { Trash2 } from 'lucide-react';
import apiService from '@/utils/api';

interface SetVarsModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  destination: string;
  contextVars: Record<string, any>;
  onSave: () => void;
}

export const SetVarsModal: React.FC<SetVarsModalProps> = ({
  open,
  onOpenChange,
  destination,
  contextVars,
  onSave
}) => {
  const { toast } = useToast();
  const [createNew, setCreateNew] = useState(false);
  const [selectedVar, setSelectedVar] = useState<string>('');
  const [newVarName, setNewVarName] = useState('');
  const [varValue, setVarValue] = useState<string>('');
  const [activeTab, setActiveTab] = useState('value');
  const [valueType, setValueType] = useState<'string' | 'number' | 'boolean' | 'object'>('string');
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [varNames, setVarNames] = useState<string[]>([]);

  // Initialize variable names from contextVars
  useEffect(() => {
    if (contextVars) {
      setVarNames(Object.keys(contextVars));
    }
  }, [contextVars]);

  // Update varValue when selectedVar changes
  useEffect(() => {
    if (selectedVar && contextVars[selectedVar] !== undefined) {
      // Determine the type and set the appropriate value and type selector
      const value = contextVars[selectedVar];
      
      if (value === null) {
        setValueType('string');
        setVarValue('');
      } else if (typeof value === 'boolean') {
        setValueType('boolean');
        setVarValue(value.toString());
      } else if (typeof value === 'number') {
        setValueType('number');
        setVarValue(value.toString());
      } else if (typeof value === 'object') {
        setValueType('object');
        setVarValue(JSON.stringify(value, null, 2));
      } else {
        setValueType('string');
        setVarValue(value.toString());
      }
    } else {
      // Default for new variables
      setValueType('string');
      setVarValue('');
    }
  }, [selectedVar, contextVars]);

  const handleVarSelection = (value: string) => {
    if (value === 'create-new') {
      setCreateNew(true);
      setSelectedVar('');
    } else {
      setCreateNew(false);
      setSelectedVar(value);
    }
  };

  const parseValue = () => {
    if (valueType === 'boolean') {
      return varValue.toLowerCase() === 'true';
    } else if (valueType === 'number') {
      return Number(varValue);
    } else if (valueType === 'object') {
      try {
        return JSON.parse(varValue);
      } catch (e) {
        toast({
          title: 'Invalid JSON',
          description: 'Please enter valid JSON for object type',
          variant: 'destructive'
        });
        return null;
      }
    } else {
      return varValue;
    }
  };

  const handleSave = async () => {
    const varName = createNew ? newVarName : selectedVar;
    
    if (!varName) {
      toast({
        title: 'Missing variable name',
        description: 'Please select or enter a variable name',
        variant: 'destructive'
      });
      return;
    }

    try {
      setSaving(true);
      
      const parsedValue = parseValue();
      if (parsedValue === undefined) {
        return; // Error already shown in parseValue
      }

      // Call API to set the context variable
      const response = await apiService.setSchedulerContextVar(
        destination,
        varName,
        parsedValue
      );

      if (response.status === 'success') {
        toast({
          title: 'Variable set',
          description: `Successfully set ${varName}`,
        });
        
        // Just notify parent of change, don't close
        onSave();
      } else {
        throw new Error(response.error || 'Failed to set variable');
      }
    } catch (error) {
      console.error('Error setting variable:', error);
      toast({
        title: 'Error',
        description: error.message || 'Failed to set variable',
        variant: 'destructive'
      });
    } finally {
      setSaving(false);
    }
  };
  
  const handleDelete = async () => {
    if (!selectedVar) {
      toast({
        title: 'No variable selected',
        description: 'Please select a variable to delete',
        variant: 'destructive'
      });
      return;
    }
    
    try {
      setDeleting(true);
      
      // Debug logs
      console.log(`Deleting variable ${selectedVar} from destination ${destination}`);
      console.log('Using endpoint:', `${apiService.getApiUrl()}/schedulers/${destination}/context`);
      console.log('Request payload:', { var_name: selectedVar, var_value: null });
      
      // Call API with null value to delete the variable
      const response = await apiService.setSchedulerContextVar(
        destination,
        selectedVar,
        null
      );
      
      // Debug logs
      console.log('Delete response:', response);
      
      if (response.status === 'success' && response.deleted) {
        toast({
          title: 'Variable deleted',
          description: `Successfully deleted ${selectedVar}`,
        });
        
        // Reset modal state
        setSelectedVar('');
        setVarValue('');
        setValueType('string');
        
        // Remove the deleted variable from the list
        setVarNames(varNames.filter(name => name !== selectedVar));
        
        // Notify parent of change
        onSave();
        
        // Close modal if no more variables
        if (varNames.length <= 1) {
          onOpenChange(false);
        }
      } else {
        throw new Error(response.error || 'Failed to delete variable');
      }
    } catch (error) {
      console.error('Error deleting variable:', error);
      console.error('Full error:', JSON.stringify(error));
      toast({
        title: 'Error',
        description: error.message || 'Failed to delete variable',
        variant: 'destructive'
      });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Set Context Variable</DialogTitle>
          <DialogDescription>
            Edit existing variables or create new ones.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Variable Selection */}
          <div>
            <Label htmlFor="var-selector">Variable</Label>
            <Select 
              value={createNew ? 'create-new' : selectedVar} 
              onValueChange={handleVarSelection}
            >
              <SelectTrigger id="var-selector">
                <SelectValue placeholder="Select a variable" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectLabel>Existing Variables</SelectLabel>
                  {varNames.map(name => (
                    <SelectItem key={name} value={name}>{name}</SelectItem>
                  ))}
                  <SelectItem value="create-new">Create new variable</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>

          {/* New Variable Name Input */}
          {createNew && (
            <div>
              <Label htmlFor="new-var-name">New Variable Name</Label>
              <Input
                id="new-var-name"
                value={newVarName}
                onChange={(e) => setNewVarName(e.target.value)}
                placeholder="Enter variable name"
              />
            </div>
          )}

          {/* Variable Value Section */}
          <div>
            <div className="flex justify-between items-center mb-2">
              <Label>Value</Label>
              <Select value={valueType} onValueChange={(v: any) => setValueType(v)}>
                <SelectTrigger className="w-32">
                  <SelectValue placeholder="Type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="string">String</SelectItem>
                  <SelectItem value="number">Number</SelectItem>
                  <SelectItem value="boolean">Boolean</SelectItem>
                  <SelectItem value="object">Object/Array</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="value">Value</TabsTrigger>
                <TabsTrigger value="preview">Preview</TabsTrigger>
              </TabsList>
              
              <TabsContent value="value" className="space-y-4">
                {valueType === 'boolean' ? (
                  <Select 
                    value={varValue} 
                    onValueChange={setVarValue}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select boolean value" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="true">true</SelectItem>
                      <SelectItem value="false">false</SelectItem>
                    </SelectContent>
                  </Select>
                ) : valueType === 'object' ? (
                  <div className="relative">
                    <textarea
                      className="w-full min-h-[150px] p-2 font-mono text-sm border rounded-md"
                      value={varValue}
                      onChange={(e) => setVarValue(e.target.value)}
                      placeholder="Enter JSON object or array"
                    />
                  </div>
                ) : (
                  <Input
                    type={valueType === 'number' ? 'number' : 'text'}
                    value={varValue}
                    onChange={(e) => setVarValue(e.target.value)}
                    placeholder={`Enter ${valueType} value`}
                  />
                )}
              </TabsContent>

              <TabsContent value="preview">
                <div className="p-2 border rounded-md bg-muted min-h-[40px] font-mono text-sm overflow-auto">
                  {valueType === 'object' ? (
                    (() => {
                      try {
                        const parsed = JSON.parse(varValue || '{}');
                        return JSON.stringify(parsed, null, 2);
                      } catch {
                        return <span className="text-red-500">Invalid JSON</span>;
                      }
                    })()
                  ) : (
                    <span>{varValue}</span>
                  )}
                </div>
              </TabsContent>
            </Tabs>
          </div>
        </div>

        <DialogFooter className="flex justify-between items-center">
          <div>
            {!createNew && selectedVar && (
              <Button 
                variant="destructive" 
                size="sm"
                onClick={handleDelete}
                disabled={deleting || saving}
              >
                <Trash2 className="h-4 w-4 mr-2" />
                {deleting ? 'Deleting...' : 'Delete'}
              </Button>
            )}
          </div>
          <div className="flex space-x-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={saving || deleting}>
              {saving ? 'Saving...' : 'Save'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}; 
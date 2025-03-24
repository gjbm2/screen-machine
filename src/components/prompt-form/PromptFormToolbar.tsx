
import React from 'react';
import { Button } from '@/components/ui/button';
import { Upload, Settings, SendHorizontal } from 'lucide-react';
import BatchControl from './BatchControl';
import WorkflowSelector from './WorkflowSelector';
import RefinerSelector from './RefinerSelector';
import { useVerboseDebug } from '@/hooks/use-verbose-debug';

interface PromptFormToolbarProps {
  isLoading: boolean;
  batchSize: number;
  selectedWorkflow: string;
  selectedRefiner: string;
  onImageUpload: (files: File[]) => void;
  onWorkflowChange: (workflowId: string) => void;
  onRefinerChange: (refinerId: string) => void;
  incrementBatchSize: () => void;
  decrementBatchSize: () => void;
  toggleAdvancedOptions: () => void;
  handleSubmit: () => void;
  prompt: string;
  isButtonDisabled: boolean;
  workflows: any[];
  refiners?: any[];
  isCompact?: boolean;
  hasUploadedImages?: boolean;
}

const PromptFormToolbar: React.FC<PromptFormToolbarProps> = ({
  isLoading,
  batchSize,
  selectedWorkflow,
  selectedRefiner,
  onImageUpload,
  onWorkflowChange,
  onRefinerChange,
  incrementBatchSize,
  decrementBatchSize,
  toggleAdvancedOptions,
  handleSubmit,
  prompt,
  isButtonDisabled,
  workflows,
  refiners = [],
  isCompact = false,
  hasUploadedImages = false
}) => {
  const { logVerbose } = useVerboseDebug();
  
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  
  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const filesArray = Array.from(e.target.files);
      logVerbose(`Files selected: ${filesArray.map(f => f.name).join(', ')}`);
      onImageUpload(filesArray);
      
      // Reset the input value to allow selecting the same file again
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };
  
  const handleUploadClick = () => {
    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  };
  
  const handleGenerateClick = () => {
    logVerbose(`Generate clicked with batch size: ${batchSize}`);
    logVerbose(`Current workflow: ${selectedWorkflow}`);
    logVerbose(`Current refiner: ${selectedRefiner}`);
    handleSubmit();
  };
  
  return (
    <div className="flex flex-wrap gap-2 mt-2 items-center">
      <div className="flex-1 flex flex-wrap gap-2">
        <input 
          type="file"
          ref={fileInputRef}
          onChange={handleFileInputChange}
          accept="image/*"
          multiple
          className="hidden"
        />
        
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8"
          onClick={handleUploadClick}
        >
          <Upload className="h-3.5 w-3.5 mr-1" />
          {!isCompact && 'Upload Image'}
        </Button>
        
        {!isCompact && (
          <WorkflowSelector
            selectedWorkflow={selectedWorkflow}
            workflows={workflows}
            onWorkflowChange={onWorkflowChange}
            disabled={isLoading}
          />
        )}
        
        {!isCompact && refiners.length > 0 && (
          <RefinerSelector
            selectedRefiner={selectedRefiner}
            refiners={refiners}
            onRefinerChange={onRefinerChange}
            disabled={isLoading}
          />
        )}
        
        <BatchControl
          batchSize={batchSize}
          incrementBatchSize={incrementBatchSize}
          decrementBatchSize={decrementBatchSize}
          isCompact={isCompact}
        />
        
        <Button
          variant="outline"
          size="sm"
          className="h-8"
          onClick={toggleAdvancedOptions}
        >
          <Settings className="h-3.5 w-3.5 mr-1" />
          {!isCompact && 'Advanced'}
        </Button>
      </div>
      
      <Button
        type="button"
        onClick={handleGenerateClick}
        disabled={isButtonDisabled}
        className="h-8"
        size="sm"
      >
        <SendHorizontal className="h-3.5 w-3.5 mr-1" />
        Generate
      </Button>
    </div>
  );
};

export default PromptFormToolbar;

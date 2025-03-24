
import React from 'react';
import { Button } from '@/components/ui/button';
import { 
  Send, 
  UploadCloud, 
  Settings, 
  Loader2
} from 'lucide-react';
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from '@/components/ui/tooltip';
import RefinerSelector from './RefinerSelector';
import BatchControl from './BatchControl';
import WorkflowSelector from './WorkflowSelector';

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
  isCompact?: boolean;
  hasUploadedImages?: boolean;
  isVerboseDebug?: boolean;
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
  isCompact = false,
  hasUploadedImages = false,
  isVerboseDebug = false
}) => {
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) {
      onImageUpload(Array.from(e.target.files));
      // Clear the input to allow uploading the same file again
      e.target.value = '';
    }
  };
  
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 mt-2">
      <div className="flex flex-wrap items-center gap-2">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                onClick={() => document.getElementById('file-upload')?.click()}
              >
                <UploadCloud className="h-4 w-4 mr-2" />
                <span>Upload</span>
                <input
                  id="file-upload"
                  type="file"
                  accept="image/*"
                  multiple
                  className="hidden"
                  onChange={handleFileChange}
                />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Upload reference image</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        
        <WorkflowSelector 
          workflows={workflows}
          selectedWorkflow={selectedWorkflow}
          onWorkflowChange={onWorkflowChange}
          isCompact={isCompact}
        />
        
        <RefinerSelector
          selectedRefiner={selectedRefiner}
          onRefinerChange={onRefinerChange}
          isCompact={isCompact}
        />
        
        <BatchControl 
          batchSize={batchSize}
          incrementBatchSize={incrementBatchSize}
          decrementBatchSize={decrementBatchSize}
          isCompact={isCompact}
          isVerboseDebug={isVerboseDebug}
        />
      </div>
      
      <div className="flex items-center gap-2">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                onClick={toggleAdvancedOptions}
              >
                <Settings className="h-4 w-4 mr-2" />
                <span>Advanced</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Show advanced options</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
        
        <Button
          variant="default"
          size="sm"
          className="h-8"
          onClick={handleSubmit}
          disabled={isButtonDisabled || isLoading}
        >
          {isLoading ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              <span>Generating...</span>
            </>
          ) : (
            <>
              <Send className="h-4 w-4 mr-2" />
              <span>Generate</span>
            </>
          )}
        </Button>
      </div>
    </div>
  );
};

export default PromptFormToolbar;

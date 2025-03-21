
import React from 'react';
import { Button } from '@/components/ui/button';
import { Settings, ArrowUp } from 'lucide-react';
import ImageUploader from '@/components/prompt/ImageUploader';
import WorkflowIconSelector from '@/components/prompt/WorkflowIconSelector';
import RefinerSelector from '@/components/prompt/RefinerSelector';
import BatchControl from './BatchControl';
import { ToolbarProps } from './types';

const PromptFormToolbar: React.FC<ToolbarProps> = ({
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
  isCompact
}) => {
  return (
    <div className="flex flex-wrap items-center gap-1 sm:gap-2 justify-between">
      <div className="flex flex-wrap items-center gap-1 sm:gap-2">
        <ImageUploader
          isLoading={isButtonDisabled}
          onImageUpload={onImageUpload}
          onWorkflowChange={onWorkflowChange}
          hideLabel={isCompact}
        />
        
        <WorkflowIconSelector
          workflows={workflows}
          selectedWorkflow={selectedWorkflow}
          onWorkflowChange={onWorkflowChange}
          hideWorkflowName={isCompact}
        />
        
        <RefinerSelector
          selectedRefiner={selectedRefiner}
          onRefinerChange={onRefinerChange}
        />
        
        <BatchControl 
          batchSize={batchSize}
          incrementBatchSize={incrementBatchSize}
          decrementBatchSize={decrementBatchSize}
        />

        <Button 
          type="button"
          variant="outline" 
          size="icon"
          onClick={toggleAdvancedOptions}
          className="h-[48px] w-[48px] text-muted-foreground hover:bg-purple-500/10 text-purple-700"
          aria-label="Settings"
        >
          <Settings className="h-5 w-5" />
        </Button>
      </div>

      <div className="ml-auto mt-2 sm:mt-0">
        <Button 
          type="submit" 
          className={`h-12 w-12 rounded-full transition-all hover:shadow-md flex items-center justify-center btn-shine ${
            !prompt.trim() && isCompact ? 'bg-gray-300 text-gray-600' : 'bg-primary text-primary-foreground'
          }`}
          disabled={isButtonDisabled || (!prompt.trim() && isCompact)}
          onClick={() => handleSubmit()}
        >
          <ArrowUp className="h-6 w-6" />
        </Button>
      </div>
    </div>
  );
};

export default PromptFormToolbar;


import React from 'react';
import { Button } from '@/components/ui/button';
import { Settings, ArrowUp, Image, Plus, Minus } from 'lucide-react';
import ImageUploader from '@/components/prompt/ImageUploader';
import WorkflowIconSelector from '@/components/prompt/WorkflowIconSelector';
import RefinerSelector from '@/components/prompt/RefinerSelector';
import BatchControl from './BatchControl';
import { ToolbarProps } from './types';
import { useIsMobile } from '@/hooks/use-mobile';

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
  isCompact,
  hasUploadedImages = false
}) => {
  const isMobile = useIsMobile();
  
  // The button should be enabled if there's a prompt OR uploaded images
  const shouldDisableButton = isLoading || (!prompt.trim() && !hasUploadedImages);
  
  return (
    <div className="flex items-center justify-between overflow-x-auto py-1">
      <div className="flex flex-nowrap items-center gap-1 sm:gap-2">
        <ImageUploader
          isLoading={isLoading}
          onImageUpload={onImageUpload}
          onWorkflowChange={onWorkflowChange}
          hideLabel={true}
        />
        
        <WorkflowIconSelector
          workflows={workflows}
          selectedWorkflow={selectedWorkflow}
          onWorkflowChange={onWorkflowChange}
          hideWorkflowName={true}
        />
        
        <RefinerSelector
          selectedRefiner={selectedRefiner}
          onRefinerChange={onRefinerChange}
        />
        
        <BatchControl 
          batchSize={batchSize}
          incrementBatchSize={incrementBatchSize}
          decrementBatchSize={decrementBatchSize}
          isCompact={true}
        />

        <Button 
          type="button"
          variant="outline" 
          size="icon"
          onClick={toggleAdvancedOptions}
          className="h-[28px] w-[28px] text-muted-foreground hover:bg-purple-500/10 text-purple-700 shrink-0"
          aria-label="Settings"
        >
          <Settings className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="ml-auto">
        <Button 
          type="submit" 
          className={`h-12 w-12 rounded-full transition-all hover:shadow-md flex items-center justify-center btn-shine ${
            shouldDisableButton && isCompact ? 'bg-gray-300 text-gray-600' : 'bg-primary text-primary-foreground'
          }`}
          disabled={shouldDisableButton}
          onClick={handleSubmit}
        >
          <ArrowUp className="h-6 w-6" />
        </Button>
      </div>
    </div>
  );
};

export default PromptFormToolbar;

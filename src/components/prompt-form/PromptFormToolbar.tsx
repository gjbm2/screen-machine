
import React from 'react';
import { Button } from '@/components/ui/button';
import { Settings, ArrowUp, Image } from 'lucide-react';
import ImageUploader from '@/components/prompt/ImageUploader';
import WorkflowIconSelector from '@/components/prompt/WorkflowIconSelector';
import RefinerSelector from '@/components/prompt/RefinerSelector';
import PublishSelector from './PublishSelector';
import { ToolbarProps } from './types';
import { useIsMobile } from '@/hooks/use-mobile';

const PromptFormToolbar: React.FC<ToolbarProps> = ({
  isLoading,
  selectedWorkflow,
  selectedRefiner,
  selectedPublish,
  onImageUpload,
  onWorkflowChange,
  onRefinerChange,
  onPublishChange,
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
          hideLabel={isMobile}
        />
        
        <WorkflowIconSelector
          workflows={workflows}
          selectedWorkflow={selectedWorkflow}
          onWorkflowChange={onWorkflowChange}
          hideWorkflowName={isMobile}
        />
        
        <RefinerSelector
          selectedRefiner={selectedRefiner}
          onRefinerChange={onRefinerChange}
        />
        
        <PublishSelector 
          selectedPublish={selectedPublish || 'none'}
          onPublishChange={onPublishChange}
          isCompact={isMobile}
        />

        <Button 
          type="button"
          variant="outline" 
          size={isMobile ? "icon" : "sm"}
          onClick={toggleAdvancedOptions}
          className={`${isMobile ? "h-[28px] w-[28px]" : "h-[28px] px-2"} text-muted-foreground hover:bg-purple-500/10 text-purple-700 shrink-0`}
          aria-label="Advanced Settings"
        >
          <Settings className="h-3.5 w-3.5" />
          {!isMobile && <span className="ml-1.5 text-xs">Advanced</span>}
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

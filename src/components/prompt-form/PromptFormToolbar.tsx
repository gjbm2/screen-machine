
import React from 'react';
import { Button } from '@/components/ui/button';
import { Settings, ArrowUp, Image } from 'lucide-react';
import ImageUploader from '@/components/prompt/ImageUploader';
import WorkflowIconSelector from '@/components/prompt/WorkflowIconSelector';
import RefinerSelector from '@/components/prompt/RefinerSelector';
import PublishSelector from './PublishSelector';
import { ToolbarProps, WorkflowProps } from './types';
import { useIsMobile } from '@/hooks/use-mobile';
import { Workflow } from '@/types/workflows';

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
  
  // Common style for all toolbar buttons to ensure consistency
  const toolbarButtonClass = "h-[28px] text-muted-foreground bg-white";
  
  return (
    <div className="flex items-center justify-between overflow-x-auto py-1">
      <div className="flex flex-nowrap items-center gap-1 sm:gap-2">
		<ImageUploader
		  isLoading={isLoading}
		  onImageUpload={onImageUpload}
		  onWorkflowChange={onWorkflowChange}
		  availableWorkflows={workflows}
		  selectedWorkflowId={selectedWorkflow}
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
		  onClick={toggleAdvancedOptions}
		  className="h-[36px] border border-input hover:bg-purple-500/10 text-purple-700 flex items-center px-3 text-sm shrink-0"
		  aria-label="Advanced Settings"
		>
		  <Settings className="h-5 w-5" />
		  {!isMobile && (
			<span className="ml-2 text-sm">Advanced</span>
		  )}
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

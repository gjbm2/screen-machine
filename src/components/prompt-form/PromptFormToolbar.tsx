
import React from 'react';
import { Button } from '@/components/ui/button';
import { Settings, ArrowUp } from 'lucide-react';
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
  hasUploadedImages = false,
  workflowParams,
  refinerParams,
  globalParams,
  onRestoreFormState
}) => {
  const isMobile = useIsMobile();
  
  // Prepare current form state for session restoration
  const currentFormState = {
    prompt,
    selectedWorkflow,
    selectedRefiner,
    selectedPublish,
    workflowParams: workflowParams || {},
    refinerParams: refinerParams || {},
    globalParams: globalParams || {},
    referenceUrls: [] // This will be populated by the ImageUploader
  };
  
  // The button should be enabled if there's a prompt OR uploaded images
  const shouldDisableButton = isLoading || (!prompt.trim() && !hasUploadedImages);
  
  
  // Labelled chips that wrap (two rows on a phone) instead of bare icons: Add picture and "Show on" up front (where a
  // new picture goes is the main choice), then the style engine and the finish, then Advanced; Go on the right.
  return (
    <div className="flex items-end gap-2 pt-2">
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
        <ImageUploader
          isLoading={isLoading}
          onImageUpload={onImageUpload}
          hideLabel={false}
          currentFormState={currentFormState}
          onRestoreFormState={onRestoreFormState}
        />

        <PublishSelector
          selectedPublish={selectedPublish || 'none'}
          onPublishChange={onPublishChange}
          isCompact={isMobile}
        />

        <WorkflowIconSelector
          workflows={workflows}
          selectedWorkflow={selectedWorkflow}
          onWorkflowChange={onWorkflowChange}
        />

        <RefinerSelector
          selectedRefiner={selectedRefiner}
          onRefinerChange={onRefinerChange}
        />

        <Button
          type="button"
          variant="outline"
          onClick={toggleAdvancedOptions}
          className="h-9 shrink-0 gap-1.5 rounded-full border-input bg-card px-3 text-sm font-normal"
          aria-label="Advanced"
        >
          <Settings className="h-4 w-4" />
          <span>Advanced</span>
        </Button>
      </div>

      <Button
        type="button"
        aria-label="Make it"
        title="Make it"
        className={`h-12 w-12 shrink-0 rounded-full transition-all hover:shadow-md flex items-center justify-center ${
          shouldDisableButton && isCompact ? 'bg-muted text-muted-foreground' : 'bg-primary text-primary-foreground'
        }`}
        disabled={shouldDisableButton}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          handleSubmit();
        }}
      >
        <ArrowUp className="h-6 w-6" />
      </Button>
    </div>
  );
};

export default PromptFormToolbar;

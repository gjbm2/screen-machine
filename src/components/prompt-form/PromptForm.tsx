
import React from 'react';
import { Card } from '@/components/ui/card';
import AdvancedOptions from '@/components/AdvancedOptions';
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { useIsMobile, useWindowSize } from '@/hooks/use-mobile';
import PromptInput from '@/components/prompt/PromptInput';
import PromptExamples from '@/components/prompt/PromptExamples';
import ImagePreview from './ImagePreview';
import PromptFormToolbar from './PromptFormToolbar';
import usePromptForm from './usePromptForm';
import { PromptFormProps } from './types';

const PromptForm: React.FC<PromptFormProps> = ({ 
  onSubmit, 
  isLoading, 
  currentPrompt = null, 
  isFirstRun = true 
}) => {
  const isMobile = useIsMobile();
  const { width } = useWindowSize();
  const isCompact = width && width < 640;
  
  const {
    prompt,
    imageFiles,
    previewUrls,
    selectedWorkflow,
    selectedRefiner,
    workflowParams,
    refinerParams,
    globalParams,
    isAdvancedOptionsOpen,
    isButtonDisabled,
    batchSize,
    workflows,
    setPrompt,
    handleSubmit,
    handleExampleClick,
    handleStyleClick,
    handleImageUpload,
    clearAllImages,
    handleRemoveImage,
    handleClearPrompt,
    handleWorkflowChange,
    handleRefinerChange,
    handleParamChange,
    handleRefinerParamChange,
    handleGlobalParamChange,
    toggleAdvancedOptions,
    incrementBatchSize,
    decrementBatchSize
  } = usePromptForm(onSubmit, currentPrompt, isLoading);

  return (
    <div className={`animate-fade-up transition-all duration-500 ${isFirstRun ? 'mb-12' : 'mb-4'}`}>
      <Collapsible open={true}>
        <CollapsibleContent>
          <Card className="overflow-hidden glass border border-border/30">
            <form onSubmit={handleSubmit} className="p-1">
              <ImagePreview 
                previewUrls={previewUrls}
                handleRemoveImage={handleRemoveImage}
                clearAllImages={clearAllImages}
              />
              
              <div className="relative">
                <PromptInput
                  prompt={prompt}
                  isLoading={isLoading}
                  onPromptChange={setPrompt}
                  uploadedImages={previewUrls}
                  onClearPrompt={handleClearPrompt}
                  onSubmit={handleSubmit}
                />
              </div>
              
              <PromptExamples
                prompt={prompt}
                onExampleClick={handleExampleClick}
                onStyleClick={handleStyleClick}
                showMore={!isCompact}
              />
              
              <div className="p-2 pt-0 space-y-2">
                <PromptFormToolbar 
                  isLoading={isLoading}
                  batchSize={batchSize}
                  selectedWorkflow={selectedWorkflow}
                  selectedRefiner={selectedRefiner}
                  onImageUpload={handleImageUpload}
                  onWorkflowChange={handleWorkflowChange}
                  onRefinerChange={handleRefinerChange}
                  incrementBatchSize={incrementBatchSize}
                  decrementBatchSize={decrementBatchSize}
                  toggleAdvancedOptions={toggleAdvancedOptions}
                  handleSubmit={handleSubmit}
                  prompt={prompt}
                  isButtonDisabled={isButtonDisabled}
                  workflows={workflows}
                  isCompact={isCompact}
                />
              </div>
            </form>
          </Card>
        </CollapsibleContent>
      </Collapsible>

      <AdvancedOptions
        workflows={workflows}
        selectedWorkflow={selectedWorkflow}
        onWorkflowChange={handleWorkflowChange}
        params={workflowParams}
        onParamChange={handleParamChange}
        globalParams={globalParams}
        onGlobalParamChange={handleGlobalParamChange}
        selectedRefiner={selectedRefiner}
        onRefinerChange={handleRefinerChange}
        refinerParams={refinerParams}
        onRefinerParamChange={handleRefinerParamChange}
        isOpen={isAdvancedOptionsOpen}
        onOpenChange={setIsAdvancedOptionsOpen}
      />
    </div>
  );
};

export default PromptForm;

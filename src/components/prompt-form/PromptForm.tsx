import React, { useState, useEffect, useRef } from 'react';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';
import usePromptForm from './usePromptForm';
import PromptInput from '@/components/prompt/PromptInput';
import PromptFormToolbar from './PromptFormToolbar';
import { PromptFormProps, WorkflowProps } from './types';
import useExternalImageUrls from '@/hooks/use-external-images';
import { Workflow } from '@/types/workflows';

const PromptForm: React.FC<PromptFormProps> = ({
  onSubmit,
  isLoading = false,
  currentPrompt = '',
  isFirstRun = true,
  onOpenAdvancedOptions,
  selectedWorkflow: externalSelectedWorkflow,
  selectedRefiner: externalSelectedRefiner,
  selectedPublish: externalSelectedPublish,
  workflowParams: externalWorkflowParams,
  refinerParams: externalRefinerParams,
  globalParams: externalGlobalParams,
  onWorkflowChange: externalWorkflowChange,
  onRefinerChange: externalRefinerChange,
  onPublishChange: externalPublishChange,
}) => {
  const [imageFiles, setImageFiles] = useState<Array<File | string>>([]);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [prompt, setPrompt] = useState(currentPrompt || '');
  const [isAdvancedOptionsOpen, setIsAdvancedOptionsOpen] = useState(false);
  const [localLoading, setLocalLoading] = useState(false);
  const lastReceivedPrompt = useRef(currentPrompt);
  const isInitialMount = useRef(true);

  const {
    selectedWorkflow,
    selectedRefiner,
    selectedPublish,
    workflowParams,
    globalParams,
    refinerParams,
    workflows,
    refiners,
    publishDestinations,
    handleWorkflowChange,
    handleRefinerChange,
    handlePublishChange,
    resetWorkflowParams,
    resetRefinerParams,
    updateWorkflowParam,
    updateRefinerParam,
    updateGlobalParam,
    updateFromAdvancedPanel,
    resetUserChangeFlags,
    setSelectedWorkflow,
    setSelectedRefiner,
    setSelectedPublish,
  } = usePromptForm();

  useEffect(() => {
    if (currentPrompt && currentPrompt !== lastReceivedPrompt.current) {
      setPrompt(currentPrompt);
      lastReceivedPrompt.current = currentPrompt;
    }
  }, [currentPrompt]);

  useEffect(() => {
    if (externalSelectedRefiner && externalSelectedRefiner !== selectedRefiner) {
      setSelectedRefiner(externalSelectedRefiner);
    }
  }, [externalSelectedRefiner, selectedRefiner, setSelectedRefiner]);
  
  useEffect(() => {
    if (externalSelectedWorkflow && externalSelectedWorkflow !== selectedWorkflow) {
      setSelectedWorkflow(externalSelectedWorkflow);
    }
  }, [externalSelectedWorkflow, selectedWorkflow, setSelectedWorkflow]);  

  useEffect(() => {
    if (externalSelectedPublish && externalSelectedPublish !== selectedPublish) {
      setSelectedPublish(externalSelectedPublish);
    }
  }, [externalSelectedPublish, selectedPublish, setSelectedPublish]);

  useEffect(() => {
    if (!isInitialMount.current) {
      updateFromAdvancedPanel({
        workflowParams: externalWorkflowParams,
        refinerParams: externalRefinerParams,
        globalParams: externalGlobalParams
      });
    } else {
      const initialValues = {
        selectedWorkflow: externalSelectedWorkflow,
        selectedRefiner: externalSelectedRefiner,
        workflowParams: externalWorkflowParams,
        refinerParams: externalRefinerParams,
        globalParams: externalGlobalParams
      };
      
      updateFromAdvancedPanel(initialValues);
      resetUserChangeFlags();
      isInitialMount.current = false;
    }
  }, [
    externalWorkflowParams, 
    externalRefinerParams, 
    externalGlobalParams,
    updateFromAdvancedPanel
  ]);

  useExternalImageUrls(setPreviewUrls);

  const handleImageUpload = async (files: Array<File | string>) => {
    console.log('handleImageUpload called with:', files);
    setImageFiles(files);
    const urls = await Promise.all(
      files.map(async (file) => {
        if (typeof file === 'string') {
          console.log('Processing string URL:', file);
          return file;
        }
        console.log('Processing File object:', file);
        return URL.createObjectURL(file);
      })
    );
    console.log('Setting preview URLs:', urls);
    setPreviewUrls(urls);
  };

  useEffect(() => {
    console.log('Setting up useImageAsPrompt event listener');
    
    const handleUseImageAsPrompt = (event: CustomEvent<{ 
      url: string; 
      preserveFavorites?: boolean;
      useReferenceUrl?: boolean;
      imageId?: string;
      source?: string;
    }>) => {
      console.log('====== USE IMAGE AS PROMPT EVENT RECEIVED ======');
      console.log('Event detail:', event.detail);
      console.log('Event source:', event.detail.source);
      console.log('=================================================');
      
      const { url, preserveFavorites, useReferenceUrl, source } = event.detail;
      console.log('Using image as prompt:', { url, preserveFavorites, useReferenceUrl, source });
      
      // Special handling for drag-and-drop operations
      if (source === 'drag-and-drop') {
        console.log('This is a drag-and-drop operation - handling specially');
        // Always use the handleImageUpload for drag and drop to preserve favorites
        handleImageUpload([url]);
        return;
      }
      
      // Standard event handling logic
      if (useReferenceUrl) {
        console.log('Using reference URL method for image:', url);
        handleImageUpload([url]);
      } else if (preserveFavorites) {
        console.log('Using preserveFavorites method for image:', url);
        setPreviewUrls([url]);
        setImageFiles([url]);
      } else {
        console.log('Using default method for image:', url);
        handleImageUpload([url]);
      }
    };

    window.addEventListener('useImageAsPrompt', handleUseImageAsPrompt as EventListener);
    return () => {
      console.log('Removing useImageAsPrompt event listener');
      window.removeEventListener('useImageAsPrompt', handleUseImageAsPrompt as EventListener);
    };
  }, [handleImageUpload]);

  const handleLocalWorkflowChange = (workflowId: string) => {
    handleWorkflowChange(workflowId);
    if (externalWorkflowChange) externalWorkflowChange(workflowId);
  };

  const handleLocalRefinerChange = (refinerId: string) => {
    handleRefinerChange(refinerId);
    if (externalRefinerChange) externalRefinerChange(refinerId);
  };

  const handleLocalPublishChange = (publishId: string) => {
    handlePublishChange(publishId);
    if (externalPublishChange) externalPublishChange(publishId);
  };

	  const handleSubmit = () => {
	  if (prompt.trim() === '' && imageFiles.length === 0 && previewUrls.length === 0) {
		toast.error('Please enter a prompt or upload an image');
		return;
	  }

	  if (!selectedWorkflow) {
		toast.error('Please select a workflow');
		return;
	  }

	  setLocalLoading(true);

	  const allImages: (File | string)[] = [
		...imageFiles,
		...previewUrls.filter(url => typeof url === 'string')  // ✅ ADD reference URLs
	  ];

	  const refinerToUse = selectedRefiner === 'none' ? undefined : selectedRefiner;
	  const publishToUse = selectedPublish === 'none' ? undefined : selectedPublish;
	  const currentGlobalParams = { ...globalParams };

	  onSubmit(
		prompt,
		allImages.length > 0 ? allImages : undefined,
		selectedWorkflow,
		workflowParams,
		currentGlobalParams,
		refinerToUse,
		refinerParams,
		publishToUse
	  );

	  setTimeout(() => {
		setLocalLoading(false);
	  }, 1000);
	};

  const handlePromptChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setPrompt(e.target.value);
    lastReceivedPrompt.current = e.target.value;
  };

  const handleClearPrompt = () => {
    setPrompt('');
    lastReceivedPrompt.current = '';
  };

  const handleRemoveImage = (index: number) => {
    const url = previewUrls[index];
    URL.revokeObjectURL(url);

    setPreviewUrls(prev => prev.filter((_, i) => i !== index));
    setImageFiles(prev => prev.filter((_, i) => i !== index));
  };

  const clearAllImages = () => {
    previewUrls.forEach(url => URL.revokeObjectURL(url));
    setImageFiles([]);
    setPreviewUrls([]);
  };

  const toggleAdvancedOptions = () => {
    if (onOpenAdvancedOptions) {
      onOpenAdvancedOptions();
    } else {
      setIsAdvancedOptionsOpen(!isAdvancedOptionsOpen);
    }
  };

  const isButtonDisabled = localLoading || (prompt.trim() === '' && imageFiles.length === 0 && previewUrls.length === 0);

  return (
    <div className="w-full mb-8">
      <Card className="p-4 relative">
        <PromptInput 
          prompt={prompt} 
          onPromptChange={handlePromptChange} 
          onClearPrompt={handleClearPrompt}
          onClearAllImages={clearAllImages}
          onRemoveImage={handleRemoveImage}
          isLoading={localLoading}
          isFirstRun={isFirstRun}
          onSubmit={handleSubmit}
          uploadedImages={previewUrls}
        />
        <PromptFormToolbar 
          isLoading={localLoading}
          selectedWorkflow={selectedWorkflow}
          selectedRefiner={selectedRefiner}
          selectedPublish={selectedPublish}
          onImageUpload={handleImageUpload}
          onWorkflowChange={handleLocalWorkflowChange}
          onRefinerChange={handleLocalRefinerChange}
          onPublishChange={handleLocalPublishChange}
          toggleAdvancedOptions={toggleAdvancedOptions}
          handleSubmit={handleSubmit}
          prompt={prompt}
          isButtonDisabled={isButtonDisabled}
          workflows={workflows as unknown as WorkflowProps[]}
          isCompact={false}
          hasUploadedImages={previewUrls.length > 0}
        />
      </Card>
    </div>
  );
};

export default PromptForm;

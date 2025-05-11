import React, { useState, useEffect, useRef } from 'react';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';
import usePromptForm from './usePromptForm';
import PromptInput from '@/components/prompt/PromptInput';
import PromptFormToolbar from './PromptFormToolbar';
import { PromptFormProps, WorkflowProps } from './types';
import { useReferenceImages } from '@/contexts/ReferenceImagesContext';
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
  const [prompt, setPrompt] = useState(currentPrompt || '');
  const [isAdvancedOptionsOpen, setIsAdvancedOptionsOpen] = useState(false);
  const [localLoading, setLocalLoading] = useState(false);
  const lastReceivedPrompt = useRef(currentPrompt);
  const isInitialMount = useRef(true);

  // Use our new ReferenceImages context
  const { referenceUrls, addReferenceUrl, removeReferenceUrl, clearReferenceUrls } = useReferenceImages();

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

  const handleImageUpload = async (files: Array<File | string>) => {
    console.log('handleImageUpload called with:', files);
    setImageFiles(files);
    
    // Process each file - convert File objects to blob URLs
    for (const file of files) {
      if (typeof file === 'string') {
        console.log('Processing string URL:', file);
        addReferenceUrl(file, true);
      } else {
        console.log('Processing File object:', file);
        const blobUrl = URL.createObjectURL(file);
        addReferenceUrl(blobUrl, true);
      }
    }
  };

  useEffect(() => {
    console.log('Setting up useImageAsPrompt event listener');
    
    const handleUseImageAsPrompt = (event: CustomEvent<{ 
      url: string; 
      preserveFavorites?: boolean;
      useReferenceUrl?: boolean;
      imageId?: string;
      source?: string;
      append?: boolean;
    }>) => {
      console.log('====== USE IMAGE AS PROMPT EVENT RECEIVED ======');
      console.log('Event detail:', event.detail);
      console.log('Event source:', event.detail.source);
      console.log('=================================================');
      
      const { url, preserveFavorites, useReferenceUrl, source, append } = event.detail;
      console.log('Using image as prompt:', { url, preserveFavorites, useReferenceUrl, source, append });
      
      // Instead of modifying local state, use the context methods
      if (append) {
        console.log('Appending image to existing reference images:', url);
        addReferenceUrl(url, true);
      } else {
        console.log('Replacing all reference images with:', url);
        clearReferenceUrls();
        addReferenceUrl(url, false);
      }
      
      // Also update the imageFiles array for backward compatibility
      if (append) {
        setImageFiles(prev => [...prev, url]);
      } else {
        setImageFiles([url]);
      }
    };

    window.addEventListener('useImageAsPrompt', handleUseImageAsPrompt as EventListener);
    return () => {
      console.log('Removing useImageAsPrompt event listener');
      window.removeEventListener('useImageAsPrompt', handleUseImageAsPrompt as EventListener);
    };
  }, [addReferenceUrl, clearReferenceUrls]);

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
    if (prompt.trim() === '' && referenceUrls.length === 0) {
		toast.error('Please enter a prompt or upload an image');
		return;
	  }

	  if (!selectedWorkflow) {
		toast.error('Please select a workflow');
		return;
	  }

	  setLocalLoading(true);

    // Use only the referenceUrls from context as the source of truth
	  const allImages: (File | string)[] = referenceUrls;

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
    const url = referenceUrls[index];
    
    // Only revoke blob URLs that were created via URL.createObjectURL
    if (url.startsWith('blob:')) {
    URL.revokeObjectURL(url);
    }
    
    console.log('Removing image at index:', index, 'URL:', url);
    
    // Remove from context
    removeReferenceUrl(url);
    
    // Also update imageFiles for backward compatibility
    setImageFiles(prev => prev.filter(f => typeof f === 'object' || f !== url));
  };

  const clearAllImages = () => {
    // Revoke any blob URLs
    referenceUrls.forEach(url => {
      if (url.startsWith('blob:')) {
        URL.revokeObjectURL(url);
      }
    });
    
    // Clear from context
    clearReferenceUrls();
    
    // Also clear imageFiles for backward compatibility
    setImageFiles([]);
  };

  const toggleAdvancedOptions = () => {
    if (onOpenAdvancedOptions) {
      onOpenAdvancedOptions();
    } else {
      setIsAdvancedOptionsOpen(!isAdvancedOptionsOpen);
    }
  };

  const isButtonDisabled = localLoading || (prompt.trim() === '' && referenceUrls.length === 0);

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
          uploadedImages={referenceUrls}
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
          hasUploadedImages={referenceUrls.length > 0}
        />
      </Card>
    </div>
  );
};

export default PromptForm;

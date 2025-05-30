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
    
    // Process each file - but DON'T convert File objects to blob URLs for reference
    for (const file of files) {
      if (typeof file === 'string') {
        console.log('Processing string URL:', file);
        addReferenceUrl(file, true);
      } else {
        console.log('Processing File object:', file);
        // Don't create blob URLs for File objects - they need to be uploaded as files
        // The imageFiles state already contains the File objects
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
    if (prompt.trim() === '' && referenceUrls.length === 0 && imageFiles.length === 0) {
		toast.error('Please enter a prompt or upload an image');
		return;
	  }

	  if (!selectedWorkflow) {
		toast.error('Please select a workflow');
		return;
	  }

	  setLocalLoading(true);

    // Combine File objects from imageFiles with string URLs from referenceUrls
    const allImages: (File | string)[] = [
      ...imageFiles.filter(f => f instanceof File), // File objects
      ...referenceUrls // String URLs
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
    // Determine if this is a File object or a reference URL based on index
    const fileCount = imageFiles.filter(f => f instanceof File).length;
    
    if (index < fileCount) {
      // It's a File object
      const fileIndex = imageFiles.findIndex((f, i) => f instanceof File && i === index);
      if (fileIndex !== -1) {
        setImageFiles(prev => prev.filter((_, i) => i !== fileIndex));
      }
    } else {
      // It's a reference URL
      const urlIndex = index - fileCount;
      const url = referenceUrls[urlIndex];
      if (url) {
        removeReferenceUrl(url);
      }
    }
  };

  const clearAllImages = () => {
    // Revoke any blob URLs that might have been created
    referenceUrls.forEach(url => {
      if (url.startsWith('blob:')) {
        URL.revokeObjectURL(url);
      }
    });
    
    // Clear from context
    clearReferenceUrls();
    
    // Clear imageFiles
    setImageFiles([]);
  };

  const toggleAdvancedOptions = () => {
    if (onOpenAdvancedOptions) {
      onOpenAdvancedOptions();
    } else {
      setIsAdvancedOptionsOpen(!isAdvancedOptionsOpen);
    }
  };

  const isButtonDisabled = localLoading || (prompt.trim() === '' && referenceUrls.length === 0 && imageFiles.length === 0);

  // Create display URLs for File objects (for preview only)
  const displayUrls = React.useMemo(() => {
    const urls: string[] = [];
    
    // Add blob URLs for File objects (for display only)
    imageFiles.forEach(file => {
      if (file instanceof File) {
        const blobUrl = URL.createObjectURL(file);
        urls.push(blobUrl);
      }
    });
    
    // Add reference URLs
    urls.push(...referenceUrls);
    
    return urls;
  }, [imageFiles, referenceUrls]);

  // Clean up blob URLs when component unmounts or imageFiles change
  React.useEffect(() => {
    return () => {
      // Clean up any blob URLs we created for display
      displayUrls.forEach(url => {
        if (url.startsWith('blob:')) {
          URL.revokeObjectURL(url);
        }
      });
    };
  }, [displayUrls]);

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
          uploadedImages={displayUrls}
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
          hasUploadedImages={referenceUrls.length > 0 || imageFiles.length > 0}
        />
      </Card>
    </div>
  );
};

export default PromptForm;

import React, { useState, useEffect, useRef } from 'react';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';
import usePromptForm from './usePromptForm';
import PromptInput from '@/components/prompt/PromptInput';
import PromptFormToolbar from './PromptFormToolbar';
import { PromptFormProps, WorkflowProps } from './types';
import { useReferenceImages } from '@/contexts/ReferenceImagesContext';
import { Workflow } from '@/types/workflows';
import { useIsMobile } from '@/hooks/use-mobile';

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
  const isMobile = useIsMobile();

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

  // Enhanced blob URL management for mobile
  const blobUrlsRef = useRef<Map<File, string>>(new Map());
  const cleanupTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Mobile-specific: Delay blob URL cleanup to prevent premature revocation
  const scheduleCleanup = React.useCallback(() => {
    if (cleanupTimeoutRef.current) {
      clearTimeout(cleanupTimeoutRef.current);
    }
    
    // On mobile, wait longer before cleaning up to ensure images have loaded
    const delay = isMobile ? 5000 : 1000;
    
    cleanupTimeoutRef.current = setTimeout(() => {
      const currentFiles = new Set(imageFiles.filter(f => f instanceof File));
      
      // Revoke blob URLs for files that are no longer in the array
      blobUrlsRef.current.forEach((url, file) => {
        if (!currentFiles.has(file)) {
          try {
            URL.revokeObjectURL(url);
            blobUrlsRef.current.delete(file);
            console.log('[PromptForm] Cleaned up blob URL:', url);
          } catch (error) {
            console.warn('[PromptForm] Error revoking blob URL:', error);
          }
        }
      });
    }, delay);
  }, [imageFiles, isMobile]);

  const handleImageUpload = async (files: Array<File | string>) => {
    try {
      console.log('handleImageUpload called with:', files);
      
      // Prevent any potential form submission during upload
      if (isMobile) {
        // Add a small delay on mobile to ensure the file input has fully processed
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      
      setImageFiles(prev => [...prev, ...files]);
      
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
      
      // Schedule cleanup after processing
      scheduleCleanup();
      
    } catch (error) {
      console.error('Error in handleImageUpload:', error);
      toast.error('Error processing uploaded image');
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
      try {
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
      } catch (error) {
        console.error('Error in useImageAsPrompt handler:', error);
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

  const handleSubmit = (e?: React.MouseEvent | React.FormEvent) => {
    try {
      // Comprehensive event prevention
      if (e) {
        e.preventDefault();
        e.stopPropagation();
        // Type-safe stopImmediatePropagation
        if ('stopImmediatePropagation' in e) {
          (e as any).stopImmediatePropagation();
        }
      }
      
      // Additional mobile-specific prevention
      if (isMobile && e) {
        // Prevent any potential touch events from triggering navigation
        if ('touches' in e) {
          e.preventDefault();
        }
      }
      
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
    } catch (error) {
      console.error('Error in handleSubmit:', error);
      toast.error('Error submitting form');
      setLocalLoading(false);
    }
  };

  const handlePromptChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    try {
      setPrompt(e.target.value);
      lastReceivedPrompt.current = e.target.value;
    } catch (error) {
      console.error('Error in handlePromptChange:', error);
    }
  };

  const handleClearPrompt = () => {
    try {
      setPrompt('');
      lastReceivedPrompt.current = '';
    } catch (error) {
      console.error('Error in handleClearPrompt:', error);
    }
  };

  const handleRemoveImage = (index: number) => {
    try {
      // Determine if this is a File object or a reference URL based on index
      const fileCount = imageFiles.filter(f => f instanceof File).length;
      
      if (index < fileCount) {
        // It's a File object
        const fileIndex = imageFiles.findIndex((f, i) => f instanceof File && i === index);
        if (fileIndex !== -1) {
          const fileToRemove = imageFiles[fileIndex] as File;
          // Revoke the blob URL for this file
          const blobUrl = blobUrlsRef.current.get(fileToRemove);
          if (blobUrl) {
            try {
              URL.revokeObjectURL(blobUrl);
              blobUrlsRef.current.delete(fileToRemove);
            } catch (error) {
              console.warn('Error revoking blob URL:', error);
            }
          }
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
    } catch (error) {
      console.error('Error in handleRemoveImage:', error);
    }
  };

  const clearAllImages = () => {
    try {
      // Revoke any blob URLs that might have been created
      blobUrlsRef.current.forEach(url => {
        try {
          URL.revokeObjectURL(url);
        } catch (error) {
          console.warn('Error revoking blob URL:', error);
        }
      });
      blobUrlsRef.current.clear();
      
      // Clear from context
      clearReferenceUrls();
      
      // Clear imageFiles
      setImageFiles([]);
    } catch (error) {
      console.error('Error in clearAllImages:', error);
    }
  };

  const toggleAdvancedOptions = () => {
    try {
      if (onOpenAdvancedOptions) {
        onOpenAdvancedOptions();
      } else {
        setIsAdvancedOptionsOpen(!isAdvancedOptionsOpen);
      }
    } catch (error) {
      console.error('Error in toggleAdvancedOptions:', error);
    }
  };

  const isButtonDisabled = localLoading || (prompt.trim() === '' && referenceUrls.length === 0 && imageFiles.length === 0);

  // Create display URLs for File objects (for preview only)
  const displayUrls = React.useMemo(() => {
    const urls: string[] = [];
    
    try {
      // Add blob URLs for File objects (for display only)
      imageFiles.forEach(file => {
        if (file instanceof File) {
          // Check if we already have a blob URL for this file
          let blobUrl = blobUrlsRef.current.get(file);
          if (!blobUrl) {
            blobUrl = URL.createObjectURL(file);
            blobUrlsRef.current.set(file, blobUrl);
          }
          urls.push(blobUrl);
        }
      });
      
      // Add reference URLs
      urls.push(...referenceUrls);
    } catch (error) {
      console.error('Error creating display URLs:', error);
    }
    
    return urls;
  }, [imageFiles, referenceUrls]);

  // Clean up blob URLs when component unmounts
  React.useEffect(() => {
    return () => {
      if (cleanupTimeoutRef.current) {
        clearTimeout(cleanupTimeoutRef.current);
      }
      
      // Clean up any blob URLs we created for display
      blobUrlsRef.current.forEach(url => {
        try {
          URL.revokeObjectURL(url);
        } catch (error) {
          console.warn('Error revoking blob URL on unmount:', error);
        }
      });
      blobUrlsRef.current.clear();
    };
  }, []);

  // Schedule cleanup when imageFiles change
  React.useEffect(() => {
    scheduleCleanup();
  }, [scheduleCleanup]);

  // Prevent any form submission at the container level
  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Type-safe stopImmediatePropagation
    if ('stopImmediatePropagation' in e) {
      (e as any).stopImmediatePropagation();
    }
    
    // Call our submit handler instead
    handleSubmit(e);
  };

  return (
    <div 
      className="w-full mb-8"
      onSubmit={handleFormSubmit}
      // Add additional mobile-specific event prevention
      onTouchStart={isMobile ? (e) => e.stopPropagation() : undefined}
    >
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

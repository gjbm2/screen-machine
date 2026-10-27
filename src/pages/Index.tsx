import React, { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import ImageDisplay from '@/components/image-display/ImageDisplay';
import PromptForm from '@/components/prompt-form/PromptForm';
import MainLayout from '@/components/layout/MainLayout';
import AdvancedOptionsContainer from '@/components/advanced/AdvancedOptionsContainer';
import { useImageGeneration } from '@/hooks/image-generation/use-image-generation';
import { useConsoleManagement } from '@/hooks/use-console-management';
import typedWorkflows from '@/data/typedWorkflows';
import { useSearchParams, useNavigate } from 'react-router-dom';
import type { PublishDestination } from '@/shared/api';
import {
  DndContext,
  useSensor,
  useSensors,
  MouseSensor,
  TouchSensor,
  KeyboardSensor,
  closestCenter,
} from '@dnd-kit/core';
import { sortableKeyboardCoordinates } from '@dnd-kit/sortable';
import { DROP_ZONES } from '@/dnd/dropZones';
import { getReferenceUrl } from '@/utils/image-utils';
import { useUploadedImages } from '@/hooks/image-generation/use-uploaded-images';
import { useReferenceImages } from '@/contexts/ReferenceImagesContext';
import { useReferenceImagesAdapter } from '@/hooks/context-adapter';
import { JobsDock } from '@/components/jobs/JobsDock';

const Index = () => {
  const [advancedOptionsOpen, setAdvancedOptionsOpen] = useState(false);
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [publishDestinations, setPublishDestinations] = useState<PublishDestination[]>([]);
  
  const { 
    consoleVisible, 
    consoleLogs, 
    toggleConsole, 
    clearConsole,
    addLog: addConsoleLog 
  } = useConsoleManagement();
  
  const [selectedRefiner, setSelectedRefiner] = useState('auto');
  const [refinerParams, setRefinerParams] = useState<Record<string, any>>({});
  const [selectedPublish, setSelectedPublish] = useState('none');

  const {
    generatedImages,
    activeGenerations,
    imageUrl,
    currentPrompt,
    uploadedImageUrls,
    currentWorkflow,
    currentParams,
    currentGlobalParams,
    imageContainerOrder,
    expandedContainers,
    lastBatchId,
    isFirstRun,
    fullscreenRefreshTrigger,
    setCurrentPrompt,
    setUploadedImageUrls,
    setCurrentWorkflow,
    setCurrentParams,
    setCurrentGlobalParams,
    setImageContainerOrder,
    setExpandedContainers,
    handleSubmitPrompt,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleReorderContainers,
    handleDeleteContainer,
    setGeneratedImages,
    removeUrl
  } = useImageGeneration(addConsoleLog);
  
  const { addReferenceUrl, referenceUrls } = useReferenceImages();

  const processUrlParam = (value: string): any => {
    if ((value.startsWith('"') && value.endsWith('"')) || 
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    
    try {
      if (value.startsWith('[') || value.startsWith('{')) {
        return JSON.parse(value);
      } else if (value === 'true') {
        return true;
      } else if (value === 'false') {
        return false;
      } else if (!isNaN(Number(value))) {
        return Number(value);
      } else {
        return value;
      }
    } catch (e) {
      console.error(`Failed to parse param value=${value}`, e);
      return value;
    }
  };

  useEffect(() => {
    const promptParam = searchParams.get('prompt');
    if (promptParam) {
      const processedPrompt = processUrlParam(promptParam);
      setCurrentPrompt(processedPrompt);
      addConsoleLog({
        type: 'info',
        message: `URL parameter: prompt=${processedPrompt}`
      });
    }
    
    const workflowParam = searchParams.get('workflow');
    if (workflowParam) {
      const workflowExists = typedWorkflows.some(w => w.id === workflowParam);
      if (workflowExists) {
        setCurrentWorkflow(workflowParam);
        addConsoleLog({
          type: 'info',
          message: `URL parameter: workflow=${workflowParam}`
        });
      } else {
        toast.error(`Workflow '${workflowParam}' not found`);
      }
    }
    
    const publishParam = searchParams.get('publish');
    if (publishParam) {
      setSelectedPublish(publishParam);
      
      addConsoleLog({
        type: 'info',
        message: `URL parameter: publish=${publishParam}`
      });
    }
    
    const refinerParam = searchParams.get('refiner');
    if (refinerParam) {
      setSelectedRefiner(refinerParam);
      addConsoleLog({
        type: 'info',
        message: `URL parameter: refiner=${refinerParam}`
      });
    }
    
    const workflowParamsObj: Record<string, any> = {};
    searchParams.forEach((value, key) => {
      if (!['prompt', 'workflow', 'refiner', 'publish', 'script', 'run'].includes(key) && !key.startsWith('refiner_')) {
        try {
          workflowParamsObj[key] = processUrlParam(value);
          
          addConsoleLog({
            type: 'info',
            message: `URL parameter: ${key}=${value} (workflow param)`
          });
        } catch (e) {
          workflowParamsObj[key] = value;
          console.error(`Failed to parse param ${key}=${value}`, e);
        }
      }
    });
    
    if (Object.keys(workflowParamsObj).length > 0) {
      setCurrentParams(prev => ({
        ...prev,
        ...workflowParamsObj
      }));
    }
    
    const refinerParamsObj: Record<string, any> = {};
    searchParams.forEach((value, key) => {
      if (key.startsWith('refiner_')) {
        const paramName = key.replace('refiner_', '');
        try {
          refinerParamsObj[paramName] = processUrlParam(value);
          
          addConsoleLog({
            type: 'info',
            message: `URL parameter: ${key}=${value} (refiner param)`
          });
        } catch (e) {
          refinerParamsObj[paramName] = value;
          console.error(`Failed to parse refiner param ${key}=${value}`, e);
        }
      }
    });
    
    if (Object.keys(refinerParamsObj).length > 0) {
      setRefinerParams(prev => ({
        ...prev,
        ...refinerParamsObj
      }));
    }
    
    const scriptParam = searchParams.get('script');
    if (scriptParam) {
      addConsoleLog({
        type: 'info',
        message: `URL parameter: script=${scriptParam} (not implemented yet)`
      });
    }
    
    const hasRunParam = searchParams.get('run');
    if (hasRunParam) {
      const timer = setTimeout(() => {
        const currentPromptValue = promptParam ? processUrlParam(promptParam) : currentPrompt;
        
        if (currentPromptValue || Object.keys(workflowParamsObj).length > 0) {
          addConsoleLog({
            type: 'info',
            message: `URL parameter: run=true (auto-generating)`
          });
          
          handleSubmitPrompt(
            currentPromptValue, 
            undefined, 
            workflowParam || currentWorkflow,
            { ...currentParams, ...workflowParamsObj },
            currentGlobalParams,
            refinerParam || selectedRefiner,
            { ...refinerParams, ...refinerParamsObj },
            publishParam || selectedPublish
          );
          
          if (searchParams.has('run')) {
            const newParams = new URLSearchParams(searchParams);
            newParams.delete('run');
            navigate(`/?${newParams.toString()}`, { replace: true });
          }
        } else {
          toast.error('Cannot auto-generate: No prompt or parameters provided');
        }
      }, 500);
      
      return () => clearTimeout(timer);
    }
  }, [
    searchParams, 
    addConsoleLog, 
    setCurrentPrompt, 
    setCurrentWorkflow, 
    setCurrentParams, 
    setSelectedRefiner, 
    setSelectedPublish, 
    handleSubmitPrompt, 
    currentPrompt, 
    currentWorkflow, 
    currentParams, 
    currentGlobalParams, 
    selectedRefiner, 
    refinerParams, 
    selectedPublish, 
    navigate
  ]);

  const handleOpenAdvancedOptions = useCallback(() => {
    console.log('Opening advanced options panel');
    setAdvancedOptionsOpen(true);
  }, []);

  const handleAdvancedOptionsOpenChange = useCallback((open: boolean) => {
    console.log('Advanced options panel open state changing to:', open);
    setAdvancedOptionsOpen(open);
  }, []);

  const handleRefinerChange = useCallback((refiner: string) => {
    console.log('Index: Refiner changed to:', refiner);
    setSelectedRefiner(refiner);
  }, []);

  const handleRefinerParamChange = useCallback((paramId: string, value: any) => {
    console.log('Index: Refiner param changed:', paramId, value);
    setRefinerParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  }, []);

  const handlePublishChange = useCallback((publishId: string) => {
    console.log('Index: Publish destination changed to:', publishId);
    setSelectedPublish(publishId);
  }, []);

  const handlePromptSubmit = async (
    prompt: string,
    imageFiles?: File[] | string[],
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>,
    publish?: string
  ) => {
    try {
      console.log('Index: handlePromptSubmit called with:');
      console.log('- prompt:', prompt);
      console.log('- imageFiles:', imageFiles?.length, imageFiles?.map(f => typeof f === 'string' ? `string(${f.substring(0, 50)}...)` : `file(${f.name})`));
      console.log('- workflow:', workflow);
      console.log('- params:', params);
      console.log('- globalParams:', globalParams);
      console.log('- refiner:', refiner);
      console.log('- refinerParams:', refinerParams);
      console.log('- publish:', publish);

      setCurrentPrompt(prompt);

      if (workflow) {
        setCurrentWorkflow(workflow);
      }

      if (refiner) {
        setSelectedRefiner(refiner);
      }

      if (publish) {
        setSelectedPublish(publish);
      }

      let effectiveParams = params ? { ...params } : { ...currentParams };

      if (publish) {
        if (publish !== 'none') {
          console.log('Adding publish destination to params:', publish);
          effectiveParams.publish_destination = publish;
        } else {
          console.log('Removing publish destination from params (Not published selected)');
          delete effectiveParams.publish_destination;
        }
      }

      setCurrentParams(effectiveParams);

      if (globalParams) {
        setCurrentGlobalParams(globalParams);
      }

      if (refinerParams) {
        setRefinerParams(refinerParams);
      }

      // Use the imageFiles parameter as-is without combining with referenceUrls
      await handleSubmitPrompt(
        prompt,
        imageFiles,
        workflow,
        effectiveParams,
        globalParams,
        refiner,
        refinerParams
      );
    } catch (error) {
      console.error('Error submitting prompt:', error);
      toast.error('Error generating image');
    }
  };

  // Add DnD sensors
  const sensors = useSensors(
    useSensor(MouseSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(TouchSensor, {
      activationConstraint: {
        delay: 200,
        tolerance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  // Handle drag end events
  const handleDragEnd = (event: any) => {
    const { active, over } = event;
    if (!active || !over) return;
    
    // For tab drops, let ImageDisplay handle it and return early
    if (typeof over.id === 'string' && over.id.startsWith(DROP_ZONES.TAB_PREFIX)) {
      console.log('Tab drop detected in Index - letting ImageDisplay handle it');
      return;
    }
    
    // Handle drops on the prompt area
    if (over.id === DROP_ZONES.PROMPT) {
      const imageId = active.id;
      console.log('Image dropped on prompt area, ID:', imageId);
      
      if (active.data?.current) {
        console.log('ACTIVE DATA FOR DND:', JSON.stringify(active.data.current, null, 2));
        
        // Use our shared utility function with active data
        const referenceUrl = getReferenceUrl(active.data.current);
        if (referenceUrl) {
          console.log('Using reference URL:', referenceUrl);
          // Use the context function
          addReferenceUrl(referenceUrl, true);
          toast.success('Image added as reference');
          return;
        }
      }
      
      // Try to find image in generated images as fallback
      const generatedImage = generatedImages.find(img => 
        img.id === imageId || 
        img.url.includes(imageId)
      );
      
      if (generatedImage) {
        console.log('Found in generated images, using URL:', generatedImage.url);
        // Use the existing function which will trigger the event system
        handleUseGeneratedAsInput(generatedImage.url, true);
        return;
      }
      
      console.error('Could not find image URL, fallback to ID:', imageId);
      toast.error('Failed to add image as reference');
      return;
    }
  };

  // Use our adapter to sync global state with context
  useReferenceImagesAdapter();

  return (
    <>
      <MainLayout
        onToggleConsole={toggleConsole}
        consoleVisible={consoleVisible}
        onOpenAdvancedOptions={handleOpenAdvancedOptions}
        consoleLogs={consoleLogs}
        onClearConsole={clearConsole}
        isFirstRun={isFirstRun}
      >
        <DndContext 
          sensors={sensors} 
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
        <PromptForm 
          onSubmit={handlePromptSubmit}
          isLoading={activeGenerations.length > 0}
          currentPrompt={currentPrompt}
          isFirstRun={isFirstRun}
          onOpenAdvancedOptions={handleOpenAdvancedOptions}
          selectedWorkflow={currentWorkflow}
          selectedRefiner={selectedRefiner}
          selectedPublish={selectedPublish}
          workflowParams={currentParams}
          refinerParams={refinerParams}
          globalParams={currentGlobalParams}
          onWorkflowChange={setCurrentWorkflow}
          onRefinerChange={handleRefinerChange}
          onPublishChange={handlePublishChange}
        />
        
        <ImageDisplay 
          imageUrl={imageUrl}
          prompt={currentPrompt}
          isLoading={activeGenerations.length > 0}
          uploadedImages={uploadedImageUrls}
          generatedImages={generatedImages}
          imageContainerOrder={imageContainerOrder}
          expandedContainers={expandedContainers}
          setExpandedContainers={setExpandedContainers}
          workflow={currentWorkflow}
          generationParams={currentParams}
          onUseGeneratedAsInput={handleUseGeneratedAsInput}
          onCreateAgain={handleCreateAgain}
          onReorderContainers={handleReorderContainers}
          onDeleteImage={handleDeleteImage}
          onDeleteContainer={handleDeleteContainer}
          fullscreenRefreshTrigger={fullscreenRefreshTrigger}
                     publishDestinations={publishDestinations.map(dest => dest.id)}
        />
        </DndContext>
      </MainLayout>
      
      <AdvancedOptionsContainer
        isOpen={advancedOptionsOpen}
        onOpenChange={handleAdvancedOptionsOpenChange}
        workflows={typedWorkflows}
        selectedWorkflow={currentWorkflow}
        currentParams={currentParams}
        currentGlobalParams={currentGlobalParams}
        onWorkflowChange={setCurrentWorkflow}
        onParamsChange={setCurrentParams}
        onGlobalParamChange={(paramId: string, value: any) => {
          setCurrentGlobalParams(prev => ({
            ...prev,
            [paramId]: value
          }));
        }}
        selectedRefiner={selectedRefiner}
        refinerParams={refinerParams}
        onRefinerChange={handleRefinerChange}
        onRefinerParamChange={handleRefinerParamChange}
      />

      {/* every job being made, live from the job tracker (docs/JOBS.md) */}
      <JobsDock />
    </>
  );
};

export default Index;

import React, { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import ImageDisplay from '@/components/image-display/ImageDisplay';
import PromptForm from '@/components/prompt-form/PromptForm';
import ResizableConsole from '@/components/debug/ResizableConsole';
import HeaderSection from '@/components/main/HeaderSection';
import { useImageGeneration } from '@/hooks/image-generation/use-image-generation';
import { ScrollArea } from '@/components/ui/scroll-area';
import AboutDialog from '@/components/about/AboutDialog';
import AdvancedOptions from '@/components/AdvancedOptions';
import IntroText from '@/components/IntroText';
import Footer from '@/components/Footer';

const Index = () => {
  const [consoleVisible, setConsoleVisible] = useState(false);
  const [consoleLogs, setConsoleLogs] = useState<any[]>([]);
  const [showAboutDialog, setShowAboutDialog] = useState(false);
  const [advancedOptionsOpen, setAdvancedOptionsOpen] = useState(false);
  
  // This will store console logs for debug purposes
  const consoleLogsRef = React.useRef<any[]>([]);
  
  // Add logging for debugging the advanced panel issue
  useEffect(() => {
    console.log('Advanced options panel open state:', advancedOptionsOpen);
  }, [advancedOptionsOpen]);

  const addConsoleLog = useCallback((log: any) => {
    setConsoleLogs((prevLogs) => [...prevLogs, log]);
    consoleLogsRef.current = [...consoleLogsRef.current, log];
  }, []);

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
    isFirstRun,
    fullscreenRefreshTrigger,
    setCurrentPrompt,
    setUploadedImageUrls,
    setCurrentWorkflow,
    setCurrentParams,
    setCurrentGlobalParams,
    setImageContainerOrder,
    handleSubmitPrompt,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleDownloadImage,
    handleDeleteImage,
    handleReorderContainers,
    handleDeleteContainer
  } = useImageGeneration(addConsoleLog);

  const toggleConsole = () => {
    setConsoleVisible(!consoleVisible);
  };

  const handleOpenAdvancedOptions = useCallback(() => {
    console.log('Opening advanced options panel');
    setAdvancedOptionsOpen(true);
  }, []);

  const handleCloseAdvancedOptions = useCallback(() => {
    console.log('Closing advanced options panel');
    // Add setTimeout to ensure state updates correctly
    setTimeout(() => {
      setAdvancedOptionsOpen(false);
    }, 0);
  }, []);

  // Create a proper handler for global param changes
  const handleGlobalParamChange = useCallback((paramId: string, value: any) => {
    console.log('Global param change:', paramId, value);
    setCurrentGlobalParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  }, [setCurrentGlobalParams]);

  const handlePromptSubmit = async (
    prompt: string,
    imageFiles?: File[] | string[],
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>
  ) => {
    try {
      setCurrentPrompt(prompt);
      
      if (workflow) {
        setCurrentWorkflow(workflow);
      }
      
      if (params) {
        setCurrentParams(params);
      }
      
      if (globalParams) {
        setCurrentGlobalParams(globalParams);
      }
      
      if (imageFiles && imageFiles.length > 0) {
        const fileUrls = imageFiles
          .filter((file): file is string => typeof file === 'string')
          .map(url => url);
          
        setUploadedImageUrls(fileUrls);
      }
      
      await handleSubmitPrompt(prompt, imageFiles);
    } catch (error) {
      console.error('Error submitting prompt:', error);
      toast.error('Error generating image');
    }
  };

  const handleClearConsole = useCallback(() => {
    setConsoleLogs([]);
  }, []);

  return (
    <main className="flex flex-col min-h-screen p-4 md:p-6 max-w-screen-2xl mx-auto">
      <HeaderSection 
        onToggleConsole={toggleConsole}
        isConsoleVisible={consoleVisible}
        onOpenAdvancedOptions={handleOpenAdvancedOptions}
        onOpenAboutDialog={() => setShowAboutDialog(true)}
      />
      
      <ScrollArea className="flex-1 max-h-full overflow-y-auto pr-4">
        {isFirstRun && <IntroText />}
        
        <PromptForm 
          onSubmit={handlePromptSubmit}
          isLoading={activeGenerations.length > 0}
          currentPrompt={currentPrompt}
          isFirstRun={isFirstRun}
          onOpenAdvancedOptions={handleOpenAdvancedOptions}
        />
        
        <ImageDisplay 
          imageUrl={imageUrl}
          prompt={currentPrompt}
          isLoading={activeGenerations.length > 0}
          uploadedImages={uploadedImageUrls}
          generatedImages={generatedImages}
          imageContainerOrder={imageContainerOrder}
          workflow={currentWorkflow}
          generationParams={currentParams}
          onUseGeneratedAsInput={handleUseGeneratedAsInput}
          onCreateAgain={handleCreateAgain}
          onReorderContainers={handleReorderContainers}
          onDeleteImage={handleDeleteImage}
          onDeleteContainer={handleDeleteContainer}
          fullscreenRefreshTrigger={fullscreenRefreshTrigger}
        />
        
        <Footer />
      </ScrollArea>
      
      {consoleVisible && (
        <ResizableConsole 
          logs={consoleLogs}
          isVisible={consoleVisible}
          onClose={toggleConsole}
        />
      )}
      
      <AboutDialog 
        open={showAboutDialog} 
        onOpenChange={setShowAboutDialog}
      />
      
      <AdvancedOptions
        workflows={[]}
        selectedWorkflow={currentWorkflow}
        onWorkflowChange={setCurrentWorkflow}
        params={currentParams}
        onParamChange={setCurrentParams}
        globalParams={currentGlobalParams}
        onGlobalParamChange={handleGlobalParamChange}
        selectedRefiner={'none'}
        onRefinerChange={() => {}}
        refinerParams={{}}
        onRefinerParamChange={() => {}}
        isOpen={advancedOptionsOpen}
        onOpenChange={handleCloseAdvancedOptions}
      />
    </main>
  );
};

export default Index;

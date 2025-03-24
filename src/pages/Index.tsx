
import React, { useEffect, useState } from 'react';
import PromptForm from '@/components/PromptForm';
import ImageDisplay from '@/components/image-display/ImageDisplay';
import ResizableConsole from '@/components/debug/ResizableConsole';
import Footer from '@/components/Footer';
import HeaderSection from '@/components/main/HeaderSection';
import IntroSection from '@/components/main/IntroSection';
import useConsole from '@/hooks/use-console';
import useImageGeneration from '@/hooks/image-generation';
import useIntroText from '@/hooks/use-intro-text';
import AdvancedOptions from '@/components/AdvancedOptions';
import { toast } from 'sonner';

const Index = () => {
  const { randomIntroText } = useIntroText();
  const { 
    consoleLogs, 
    isConsoleVisible, 
    addConsoleLog, 
    handleCloseConsole, 
    toggleConsole,
    setIsConsoleVisible
  } = useConsole();
  
  const [isAdvancedOptionsOpen, setIsAdvancedOptionsOpen] = useState(false);
  
  const {
    activeGenerations,
    imageUrl,
    currentPrompt,
    uploadedImageUrls,
    currentWorkflow,
    currentParams,
    currentGlobalParams,
    generatedImages,
    imageContainerOrder,
    isFirstRun,
    fullscreenRefreshTrigger,
    setCurrentGlobalParams,
    handleSubmitPrompt,
    handleUseGeneratedAsInput,
    handleCreateAgain,
    handleReorderContainers,
    handleDeleteImage,
    handleDeleteContainer
  } = useImageGeneration(addConsoleLog);

  // Connect console visibility to global params
  useEffect(() => {
    if (currentGlobalParams.showConsoleOutput) {
      setIsConsoleVisible(true);
    }
  }, [currentGlobalParams.showConsoleOutput, setIsConsoleVisible]);

  const handleToggleConsole = () => {
    const newState = toggleConsole();
    setCurrentGlobalParams(prev => ({
      ...prev,
      showConsoleOutput: newState
    }));
  };

  const handleConsoleClose = () => {
    handleCloseConsole();
    setCurrentGlobalParams(prev => ({
      ...prev,
      showConsoleOutput: false
    }));
  };

  const handleOpenAdvancedOptions = () => {
    setIsAdvancedOptionsOpen(true);
  };

  // Fix: Make sure this correctly updates the state and doesn't cause any closure issues
  const handleCloseAdvancedOptions = (open: boolean) => {
    // Add a small delay to ensure React state updates properly
    setTimeout(() => {
      setIsAdvancedOptionsOpen(open);
    }, 0);
  };

  const [aboutDialogOpen, setAboutDialogOpen] = useState(false);
  const handleOpenAboutDialog = () => {
    setAboutDialogOpen(true);
  };

  // Create a wrapper function that matches the expected signature
  const handleGlobalParamChange = (paramId: string, value: any) => {
    setCurrentGlobalParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  };

  const handleRunScript = async (scriptFilename: string) => {
    try {
      addConsoleLog(`Running script: ${scriptFilename}`);
      
      const response = await fetch('/api/run-script', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ filename: scriptFilename }),
      });
      
      const data = await response.json();
      
      if (response.ok) {
        toast.success(`Script executed: ${scriptFilename}`);
        addConsoleLog(`Script execution result: ${data.message}`);
      } else {
        toast.error(`Script execution failed: ${data.error}`);
        addConsoleLog(`Script execution error: ${data.error}`);
      }
    } catch (error) {
      console.error('Error running script:', error);
      toast.error('Failed to run script');
      addConsoleLog(`Script execution error: ${error instanceof Error ? error.message : String(error)}`);
    }
  };

  return (
    <div className="min-h-screen hero-gradient flex flex-col">
      <div className="container max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 flex-grow">
        <HeaderSection 
          onToggleConsole={handleToggleConsole} 
          isConsoleVisible={isConsoleVisible}
          onOpenAdvancedOptions={handleOpenAdvancedOptions}
          onOpenAboutDialog={handleOpenAboutDialog}
          onRunScript={handleRunScript}
        />
        
        {isFirstRun && <IntroSection introText={randomIntroText} />}
        
        <div className={`${isFirstRun ? 'mt-4' : 'mt-8'} transition-all duration-500`}>
          <PromptForm 
            onSubmit={handleSubmitPrompt} 
            isLoading={activeGenerations.length > 0}
            currentPrompt={currentPrompt}
            isFirstRun={isFirstRun}
            onOpenAdvancedOptions={handleOpenAdvancedOptions}
          />
        </div>
        
        <div className="mb-20">
          <ImageDisplay 
            imageUrl={imageUrl}
            prompt={currentPrompt}
            isLoading={activeGenerations.length > 0}
            uploadedImages={uploadedImageUrls}
            generatedImages={generatedImages}
            imageContainerOrder={imageContainerOrder}
            workflow={currentWorkflow}
            onUseGeneratedAsInput={handleUseGeneratedAsInput}
            onCreateAgain={handleCreateAgain}
            onReorderContainers={handleReorderContainers}
            onDeleteImage={handleDeleteImage}
            onDeleteContainer={handleDeleteContainer}
            generationParams={{...currentParams, ...currentGlobalParams}}
            fullscreenRefreshTrigger={fullscreenRefreshTrigger}
          />
        </div>
      </div>
      
      <Footer />
      
      <ResizableConsole 
        logs={consoleLogs}
        isVisible={isConsoleVisible}
        onClose={handleConsoleClose}
      />

      <AdvancedOptions 
        workflows={[]} // Keep existing workflows data
        selectedWorkflow={currentWorkflow}
        onWorkflowChange={() => {}} 
        params={currentParams}
        onParamChange={() => {}} 
        globalParams={currentGlobalParams}
        onGlobalParamChange={handleGlobalParamChange} 
        isOpen={isAdvancedOptionsOpen}
        onOpenChange={handleCloseAdvancedOptions}
      />
    </div>
  );
};

export default Index;

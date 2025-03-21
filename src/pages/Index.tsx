
import React, { useEffect } from 'react';
import PromptForm from '@/components/PromptForm';
import ImageDisplay from '@/components/image-display/ImageDisplay';
import ResizableConsole from '@/components/debug/ResizableConsole';
import Footer from '@/components/Footer';
import HeaderSection from '@/components/main/HeaderSection';
import IntroSection from '@/components/main/IntroSection';
import useConsole from '@/hooks/use-console';
import useImageGeneration from '@/hooks/image-generation';
import useIntroText from '@/hooks/use-intro-text';

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

  return (
    <div className="min-h-screen hero-gradient flex flex-col">
      <div className="container max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 flex-grow">
        <HeaderSection 
          onToggleConsole={handleToggleConsole} 
          isConsoleVisible={isConsoleVisible} 
        />
        
        {isFirstRun && <IntroSection introText={randomIntroText} />}
        
        <div className={`${isFirstRun ? 'mt-4' : 'mt-8'} transition-all duration-500`}>
          <PromptForm 
            onSubmit={handleSubmitPrompt} 
            isLoading={activeGenerations.length > 0}
            currentPrompt={currentPrompt}
            isFirstRun={isFirstRun}
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
          />
        </div>
      </div>
      
      <Footer />
      
      <ResizableConsole 
        logs={consoleLogs}
        isVisible={isConsoleVisible}
        onClose={handleConsoleClose}
      />
    </div>
  );
};

export default Index;

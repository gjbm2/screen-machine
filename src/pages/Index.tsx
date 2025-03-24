
import React, { useState } from "react";
import MainLayout from "@/components/layout/MainLayout";
import IntroSection from "@/components/main/IntroSection";
import HeaderSection from "@/components/main/HeaderSection";
import PromptForm from "@/components/prompt-form/PromptForm";
import ImageDisplay from "@/components/image-display/ImageDisplay";
import { Link } from "react-router-dom";
import useIntroText from "@/hooks/use-intro-text";
import { useConsoleManagement } from "@/hooks/use-console-management";

export default function Index() {
  const { randomIntroText } = useIntroText();
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [currentPrompt, setCurrentPrompt] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [generatedImages, setGeneratedImages] = useState<any[]>([]);
  const [uploadedImages, setUploadedImages] = useState<string[]>([]);
  const [imageContainerOrder, setImageContainerOrder] = useState<string[]>([]);
  
  // Console management
  const {
    consoleVisible,
    setConsoleVisible,
    consoleLogs,
    clearConsoleLogs,
    addConsoleLog
  } = useConsoleManagement();
  
  const [showAdvancedOptions, setShowAdvancedOptions] = useState(false);
  
  const handleToggleConsole = () => {
    setConsoleVisible(!consoleVisible);
  };
  
  const handleOpenAdvancedOptions = () => {
    setShowAdvancedOptions(!showAdvancedOptions);
  };
  
  const handleSubmit = (
    prompt: string,
    images?: File[] | string[],
    workflow?: string,
    workflowParams?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>
  ) => {
    // Mock implementation for the handleSubmit function
    console.log("Submit form with prompt:", prompt);
    setCurrentPrompt(prompt);
    setIsLoading(true);
    
    // Simulate a loading state for 2 seconds
    setTimeout(() => {
      setIsLoading(false);
      // Create a mock batch ID
      const batchId = `batch-${Date.now()}`;
      // Add the new batch to the order
      setImageContainerOrder(prev => [batchId, ...prev]);
      
      // Create mock generated images
      const mockImages = [
        {
          batchId,
          batchIndex: 0,
          prompt,
          url: "https://via.placeholder.com/512x512?text=Generated+Image",
          status: "completed",
          timestamp: Date.now()
        }
      ];
      
      setGeneratedImages(prev => [...mockImages, ...prev]);
    }, 2000);
  };
  
  const handleUseGeneratedAsInput = (url: string) => {
    setImageUrl(url);
  };
  
  const handleCreateAgain = (batchId?: string) => {
    console.log("Create again with batch ID:", batchId);
  };
  
  const handleReorderContainers = (sourceIndex: number, destinationIndex: number) => {
    // Implementation for reordering containers
    setImageContainerOrder(prev => {
      const newOrder = [...prev];
      const [removed] = newOrder.splice(sourceIndex, 1);
      newOrder.splice(destinationIndex, 0, removed);
      return newOrder;
    });
  };
  
  const handleDeleteImage = (batchId: string, index: number) => {
    // Implementation for deleting an image
    console.log(`Delete image in batch ${batchId} at index ${index}`);
  };
  
  const handleDeleteContainer = (batchId: string) => {
    // Implementation for deleting a container
    setImageContainerOrder(prev => prev.filter(id => id !== batchId));
    setGeneratedImages(prev => prev.filter(img => img.batchId !== batchId));
  };

  return (
    <MainLayout
      onToggleConsole={handleToggleConsole}
      consoleVisible={consoleVisible}
      onOpenAdvancedOptions={handleOpenAdvancedOptions}
      consoleLogs={consoleLogs}
      onClearConsole={clearConsoleLogs}
    >
      <div className="container mx-auto px-4 max-w-screen-xl">
        <HeaderSection 
          onToggleConsole={handleToggleConsole}
          isConsoleVisible={consoleVisible}
          onOpenAdvancedOptions={handleOpenAdvancedOptions}
        />
        
        <div className="flex flex-wrap gap-2 mb-6 mt-2">
          <Link 
            to="/display" 
            className="text-blue-600 hover:text-blue-800 hover:underline text-sm flex items-center"
          >
            Display Mode
          </Link>
          <span className="text-gray-400">|</span>
          <Link 
            to="/metadata" 
            className="text-blue-600 hover:text-blue-800 hover:underline text-sm flex items-center"
          >
            Metadata Extractor
          </Link>
        </div>
        
        <IntroSection introText={randomIntroText} />
        
        <PromptForm
          onSubmit={handleSubmit}
          isLoading={isLoading}
          currentPrompt={currentPrompt || ""}
          isFirstRun={true}
          onOpenAdvancedOptions={handleOpenAdvancedOptions}
        />
        
        <ImageDisplay
          imageUrl={imageUrl}
          prompt={currentPrompt}
          isLoading={isLoading}
          uploadedImages={uploadedImages}
          generatedImages={generatedImages}
          imageContainerOrder={imageContainerOrder}
          workflow={null}
          onUseGeneratedAsInput={handleUseGeneratedAsInput}
          onCreateAgain={handleCreateAgain}
          onReorderContainers={handleReorderContainers}
          onDeleteImage={handleDeleteImage}
          onDeleteContainer={handleDeleteContainer}
        />
      </div>
    </MainLayout>
  );
}

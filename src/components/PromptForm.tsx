import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';
import { Settings, X, Rocket, Plus, Minus } from 'lucide-react';
import AdvancedOptions from '@/components/AdvancedOptions';
import workflowsData from '@/data/workflows.json';
import globalOptionsData from '@/data/global-options.json';
import refinersData from '@/data/refiners.json';
import { Workflow } from '@/types/workflows';
import PromptInput from '@/components/prompt/PromptInput';
import PromptExamples from '@/components/prompt/PromptExamples';
import ImageUploader from '@/components/prompt/ImageUploader';
import WorkflowIconSelector from '@/components/prompt/WorkflowIconSelector';
import RefinerSelector from '@/components/prompt/RefinerSelector';
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel";
import { useIsMobile } from '@/hooks/use-mobile';

interface PromptFormProps {
  onSubmit: (prompt: string, imageFiles?: File[] | string[], workflow?: string, params?: Record<string, any>, globalParams?: Record<string, any>, refiner?: string) => void;
  isLoading: boolean;
  currentPrompt?: string | null;
}

const PromptForm = ({ onSubmit, isLoading, currentPrompt = null }: PromptFormProps) => {
  const [prompt, setPrompt] = useState('');
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string>('text-to-image');
  const [selectedRefiner, setSelectedRefiner] = useState<string>('none');
  const [workflowParams, setWorkflowParams] = useState<Record<string, any>>({});
  const [globalParams, setGlobalParams] = useState<Record<string, any>>({});
  const [isAdvancedOptionsOpen, setIsAdvancedOptionsOpen] = useState(false);
  const [isButtonDisabled, setIsButtonDisabled] = useState(false);
  const [batchSize, setBatchSize] = useState(1);
  const workflows = workflowsData as Workflow[];
  const isMobile = useIsMobile();
  
  useEffect(() => {
    if (currentPrompt !== null) {
      setPrompt(currentPrompt);
    }
  }, [currentPrompt]);
  
  useEffect(() => {
    const currentWorkflow = workflows.find(w => w.id === selectedWorkflow);
    if (currentWorkflow) {
      const defaultParams: Record<string, any> = {};
      currentWorkflow.params.forEach(param => {
        if (param.default !== undefined) {
          defaultParams[param.id] = param.default;
        }
      });
      setWorkflowParams(defaultParams);
    }
  }, [selectedWorkflow, workflows]);
  
  useEffect(() => {
    const defaultGlobalParams: Record<string, any> = {};
    globalOptionsData.forEach((param: any) => {
      if (param.default !== undefined) {
        defaultGlobalParams[param.id] = param.default;
      }
    });
    setGlobalParams(defaultGlobalParams);
  }, []);
  
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!prompt.trim() && imageFiles.length === 0) {
      toast.error('Please enter a prompt or upload at least one image');
      return;
    }
    
    // Temporarily disable button for 1 second
    setIsButtonDisabled(true);
    setTimeout(() => {
      setIsButtonDisabled(false);
    }, 1000);
    
    // Include batch size in global params
    const updatedGlobalParams = {
      ...globalParams,
      batchSize
    };
    
    onSubmit(
      prompt, 
      imageFiles.length > 0 ? imageFiles : undefined, 
      selectedWorkflow, 
      workflowParams, 
      updatedGlobalParams,
      selectedRefiner !== 'none' ? selectedRefiner : undefined
    );
  };

  const handleExampleClick = (example: string) => {
    setPrompt(example);
  };
  
  const handleStyleClick = (newPrompt: string) => {
    setPrompt(newPrompt);
  };

  const handleImageUpload = (files: File[]) => {
    if (files.length > 0) {
      const newUrls = files.map(file => URL.createObjectURL(file));
      
      setImageFiles(prev => [...prev, ...files]);
      setPreviewUrls(prev => [...prev, ...newUrls]);
    }
  };

  const clearAllImages = () => {
    previewUrls.forEach(url => URL.revokeObjectURL(url));
    setImageFiles([]);
    setPreviewUrls([]);
  };

  const handleRemoveImage = (index: number) => {
    URL.revokeObjectURL(previewUrls[index]);
    
    setImageFiles(prev => prev.filter((_, i) => i !== index));
    setPreviewUrls(prev => prev.filter((_, i) => i !== index));
    
    if (imageFiles.length === 1) {
      handleWorkflowChange('text-to-image');
    }
  };

  const handleWorkflowChange = (workflowId: string) => {
    setSelectedWorkflow(workflowId);
    
    const currentWorkflow = workflows.find(w => w.id === workflowId);
    if (currentWorkflow) {
      const defaultParams: Record<string, any> = {};
      currentWorkflow.params.forEach(param => {
        if (param.default !== undefined) {
          defaultParams[param.id] = param.default;
        }
      });
      setWorkflowParams(defaultParams);
    }
  };
  
  const handleRefinerChange = (refinerId: string) => {
    setSelectedRefiner(refinerId);
  };

  const handleParamChange = (paramId: string, value: any) => {
    setWorkflowParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  };

  const handleGlobalParamChange = (paramId: string, value: any) => {
    setGlobalParams(prev => ({
      ...prev,
      [paramId]: value
    }));
  };

  const toggleAdvancedOptions = () => {
    setIsAdvancedOptionsOpen(!isAdvancedOptionsOpen);
  };
  
  const incrementBatchSize = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (batchSize < 9) {
      setBatchSize(prev => prev + 1);
    }
  };
  
  const decrementBatchSize = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (batchSize > 1) {
      setBatchSize(prev => prev - 1);
    }
  };

  return (
    <div className="animate-fade-up">
      <Card className="overflow-hidden glass border border-border/30">
        <form onSubmit={handleSubmit} className="p-1">
          {previewUrls.length > 0 && (
            <div className="relative p-4 pb-2">
              <Carousel className="w-full">
                <CarouselContent>
                  {previewUrls.map((url, index) => (
                    <CarouselItem key={index} className="basis-full md:basis-1/2 lg:basis-1/3">
                      <div className="relative rounded-lg overflow-hidden h-48 border border-border/30">
                        <img 
                          src={url} 
                          alt={`Uploaded image ${index + 1}`} 
                          className="w-full h-full object-contain"
                        />
                        <button
                          type="button"
                          onClick={() => handleRemoveImage(index)}
                          className="absolute top-2 right-2 bg-foreground/20 text-background hover:bg-foreground/30 p-1 rounded-full"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                      <p className="mt-2 text-xs text-muted-foreground truncate">
                        {imageFiles[index]?.name}
                      </p>
                    </CarouselItem>
                  ))}
                </CarouselContent>
                {previewUrls.length > 1 && (
                  <>
                    <CarouselPrevious className="left-1" />
                    <CarouselNext className="right-1" />
                  </>
                )}
              </Carousel>
              {previewUrls.length > 1 && (
                <div className="flex justify-end mt-2">
                  <Button 
                    type="button" 
                    variant="outline" 
                    size="sm" 
                    onClick={clearAllImages}
                    className="text-xs"
                  >
                    Clear All Images
                  </Button>
                </div>
              )}
            </div>
          )}
          
          <PromptInput
            prompt={prompt}
            isLoading={isLoading}
            onPromptChange={setPrompt}
            uploadedImages={previewUrls}
          />
          
          <PromptExamples
            prompt={prompt}
            onExampleClick={handleExampleClick}
            onStyleClick={handleStyleClick}
          />
          
          <div className="p-3 pt-0 space-y-3">
            <div className="flex justify-between items-center">
              <AdvancedOptions
                workflows={workflows}
                selectedWorkflow={selectedWorkflow}
                onWorkflowChange={handleWorkflowChange}
                params={workflowParams}
                onParamChange={handleParamChange}
                globalParams={globalParams}
                onGlobalParamChange={handleGlobalParamChange}
                isOpen={isAdvancedOptionsOpen}
                onOpenChange={setIsAdvancedOptionsOpen}
              />
            </div>
            
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-2">
                <WorkflowIconSelector
                  workflows={workflows}
                  selectedWorkflow={selectedWorkflow}
                  onWorkflowChange={handleWorkflowChange}
                  hideWorkflowName={true}
                />
                
                <RefinerSelector
                  selectedRefiner={selectedRefiner}
                  onRefinerChange={handleRefinerChange}
                />
                
                <Button 
                  type="button"
                  variant="outline" 
                  size="icon"
                  onClick={toggleAdvancedOptions}
                  className="h-[36px] w-[36px] text-muted-foreground"
                  aria-label="Settings"
                >
                  <Settings className="h-4 w-4" />
                </Button>
              </div>

              <ImageUploader
                isLoading={isButtonDisabled}
                onImageUpload={handleImageUpload}
                onWorkflowChange={handleWorkflowChange}
              />
              
              <div className="relative flex-1">
                <Button 
                  type="submit" 
                  className="btn-shine rounded-l-full pr-12 pl-4 sm:pl-6 transition-all hover:shadow-md h-[48px] text-lg font-medium w-full flex items-center gap-2"
                  disabled={isButtonDisabled}
                >
                  <Rocket className="h-5 w-5" />
                  {!isMobile && "Generate"}
                  <span className="inline-flex items-center justify-center bg-primary-foreground/20 text-primary-foreground rounded-md px-1.5 py-0.5 text-xs ml-2">
                    x{batchSize}
                  </span>
                </Button>
                
                <div className="absolute right-0 top-0 bottom-0 flex flex-col h-[48px] overflow-hidden rounded-r-full border-l border-primary-foreground/20">
                  <Button 
                    type="button"
                    className="h-[24px] rounded-none rounded-tr-full px-2 bg-primary hover:bg-primary/90 text-primary-foreground hover:text-primary-foreground"
                    onClick={incrementBatchSize}
                  >
                    <Plus className="h-3 w-3" />
                  </Button>
                  <Button 
                    type="button"
                    className="h-[24px] rounded-none rounded-br-full px-2 bg-primary hover:bg-primary/90 text-primary-foreground hover:text-primary-foreground"
                    onClick={decrementBatchSize}
                  >
                    <Minus className="h-3 w-3" />
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </form>
      </Card>
    </div>
  );
};

export default PromptForm;

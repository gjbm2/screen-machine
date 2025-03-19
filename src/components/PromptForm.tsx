
import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { toast } from 'sonner';
import { X } from 'lucide-react';
import AdvancedOptions from '@/components/AdvancedOptions';
import workflowsData from '@/data/workflows.json';
import { Workflow } from '@/types/workflows';
import PromptInput from '@/components/prompt/PromptInput';
import PromptExamples from '@/components/prompt/PromptExamples';
import ImageUploader from '@/components/prompt/ImageUploader';
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel";

interface PromptFormProps {
  onSubmit: (prompt: string, imageFiles?: File[], workflow?: string, params?: Record<string, any>) => void;
  isLoading: boolean;
}

const PromptForm = ({ onSubmit, isLoading }: PromptFormProps) => {
  const [prompt, setPrompt] = useState('');
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string>('text-to-image');
  const [workflowParams, setWorkflowParams] = useState<Record<string, any>>({});
  const workflows = workflowsData as Workflow[];
  
  // Initialize default workflow parameters
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
  
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!prompt.trim() && imageFiles.length === 0) {
      toast.error('Please enter a prompt or upload at least one image');
      return;
    }
    
    onSubmit(prompt, imageFiles.length > 0 ? imageFiles : undefined, selectedWorkflow, workflowParams);
  };

  const handleExampleClick = (example: string) => {
    setPrompt(example);
  };
  
  const handleStyleClick = (newPrompt: string) => {
    setPrompt(newPrompt);
  };

  const handleImageUpload = (files: File[]) => {
    if (files.length > 0) {
      // Create URLs for each new file
      const newUrls = files.map(file => URL.createObjectURL(file));
      
      // Add new files to existing ones
      setImageFiles(prev => [...prev, ...files]);
      setPreviewUrls(prev => [...prev, ...newUrls]);
    }
  };

  const clearAllImages = () => {
    // Clean up all object URLs to avoid memory leaks
    previewUrls.forEach(url => URL.revokeObjectURL(url));
    setImageFiles([]);
    setPreviewUrls([]);
  };

  const handleRemoveImage = (index: number) => {
    // Clean up the object URL for the removed image
    URL.revokeObjectURL(previewUrls[index]);
    
    // Remove the image from both arrays
    setImageFiles(prev => prev.filter((_, i) => i !== index));
    setPreviewUrls(prev => prev.filter((_, i) => i !== index));
    
    // If removing the last image, switch back to text-to-image workflow
    if (imageFiles.length === 1) {
      handleWorkflowChange('text-to-image');
    }
  };

  const handleWorkflowChange = (workflowId: string) => {
    setSelectedWorkflow(workflowId);
    
    // Reset params when workflow changes
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

  const handleParamChange = (paramId: string, value: any) => {
    setWorkflowParams(prev => ({
      ...prev,
      [paramId]: value
    }));
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
          />
          
          <PromptExamples
            prompt={prompt}
            onExampleClick={handleExampleClick}
            onStyleClick={handleStyleClick}
          />
          
          <div className="p-3 pt-0 space-y-3">
            <div className="flex gap-3">
              <ImageUploader
                isLoading={isLoading}
                onImageUpload={handleImageUpload}
                onWorkflowChange={handleWorkflowChange}
              />
              
              <Button 
                type="submit" 
                className="btn-shine rounded-full px-6 transition-all hover:shadow-md flex-1"
                disabled={isLoading}
              >
                {isLoading ? 'Generating...' : 'Generate'}
              </Button>
            </div>
            
            <AdvancedOptions
              workflows={workflows}
              selectedWorkflow={selectedWorkflow}
              onWorkflowChange={handleWorkflowChange}
              params={workflowParams}
              onParamChange={handleParamChange}
            />
          </div>
        </form>
      </Card>
    </div>
  );
};

export default PromptForm;

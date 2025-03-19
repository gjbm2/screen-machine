
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

interface PromptFormProps {
  onSubmit: (prompt: string, imageFile?: File | null, workflow?: string, params?: Record<string, any>) => void;
  isLoading: boolean;
}

const PromptForm = ({ onSubmit, isLoading }: PromptFormProps) => {
  const [prompt, setPrompt] = useState('');
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
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
    
    if (!prompt.trim() && !imageFile) {
      toast.error('Please enter a prompt or upload an image');
      return;
    }
    
    onSubmit(prompt, imageFile, selectedWorkflow, workflowParams);
  };

  const handleExampleClick = (example: string) => {
    setPrompt(example);
  };
  
  const handleStyleClick = (newPrompt: string) => {
    setPrompt(newPrompt);
  };

  const handleImageUpload = (file: File | null) => {
    if (file) {
      setImageFile(file);
      const imageUrl = URL.createObjectURL(file);
      setPreviewUrl(imageUrl);
    } else {
      clearUploadedImage();
    }
  };

  const clearUploadedImage = () => {
    setImageFile(null);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      setPreviewUrl(null);
    }
  };

  const handleRemoveImage = () => {
    clearUploadedImage();
    // If removing an image, switch back to text-to-image workflow
    handleWorkflowChange('text-to-image');
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
          {previewUrl && (
            <div className="relative p-4 pb-0">
              <div className="relative rounded-lg overflow-hidden h-40 border border-border/30">
                <img 
                  src={previewUrl} 
                  alt="Uploaded image preview" 
                  className="w-full h-full object-contain"
                />
                <button
                  type="button"
                  onClick={handleRemoveImage}
                  className="absolute top-2 right-2 bg-foreground/20 text-background hover:bg-foreground/30 p-1 rounded-full"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                {imageFile?.name}
              </p>
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

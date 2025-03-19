import React, { useState, useRef, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { Upload, X } from 'lucide-react';
import AdvancedOptions from '@/components/AdvancedOptions';
import workflowsData from '@/data/workflows.json';
import { Workflow } from '@/types/workflows';

interface PromptFormProps {
  onSubmit: (prompt: string, imageFile?: File | null, workflow?: string, params?: Record<string, any>) => void;
  isLoading: boolean;
}

const EXAMPLE_PROMPTS = [
  "A peaceful lakeside cabin at sunset with mountains in the background",
  "A futuristic cityscape with flying cars and neon lights",
  "A magical forest with glowing mushrooms and fairy lights",
  "An astronaut riding a horse on Mars"
];

const PromptForm = ({ onSubmit, isLoading }: PromptFormProps) => {
  const [prompt, setPrompt] = useState('');
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string>('text-to-image');
  const [workflowParams, setWorkflowParams] = useState<Record<string, any>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);
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

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Check if file is an image
    if (!file.type.startsWith('image/')) {
      toast.error('Please upload an image file');
      return;
    }

    // Check file size (max 5MB)
    if (file.size > 5 * 1024 * 1024) {
      toast.error('Image size should be less than 5MB');
      return;
    }

    setImageFile(file);
    const imageUrl = URL.createObjectURL(file);
    setPreviewUrl(imageUrl);
    
    // If uploading an image, automatically switch to image-to-image workflow
    if (workflows.find(w => w.id === 'image-to-image')) {
      setSelectedWorkflow('image-to-image');
    }
  };

  const handleRemoveImage = () => {
    setImageFile(null);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      setPreviewUrl(null);
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
    
    // If removing an image, switch back to text-to-image workflow
    if (selectedWorkflow === 'image-to-image') {
      setSelectedWorkflow('text-to-image');
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

  const triggerFileInput = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className="animate-fade-up">
      <Card className="overflow-hidden glass border border-border/30">
        <form onSubmit={handleSubmit} className="p-1">
          {previewUrl && (
            <div className="relative p-4 pb-0">
              <div className="relative rounded-lg overflow-hidden h-32 border border-border/30">
                <img 
                  src={previewUrl} 
                  alt="Uploaded image preview" 
                  className="w-full h-full object-cover"
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
          
          <Textarea
            placeholder="Describe the image you want to create..."
            className="min-h-[120px] resize-none border-0 bg-transparent p-4 text-base placeholder:text-muted-foreground/50 focus-visible:ring-0"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={isLoading}
          />
          
          <div className="px-4 pb-3">
            <p className="text-xs text-muted-foreground mb-2">Try an example:</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLE_PROMPTS.map((example, index) => (
                <button
                  key={index}
                  type="button"
                  className="text-xs bg-secondary/50 hover:bg-secondary px-2 py-1 rounded-full text-foreground/70 transition-colors"
                  onClick={() => handleExampleClick(example)}
                >
                  {example.length > 30 ? `${example.slice(0, 30)}...` : example}
                </button>
              ))}
            </div>
          </div>
          
          <div className="p-3 pt-0 space-y-3">
            <div className="flex gap-3">
              <input
                type="file"
                ref={fileInputRef}
                className="hidden"
                accept="image/*"
                onChange={handleImageUpload}
                disabled={isLoading}
              />
              <Button 
                type="button" 
                variant="outline"
                onClick={triggerFileInput}
                className="text-sm flex items-center gap-2 flex-1"
                disabled={isLoading}
              >
                <Upload className="h-4 w-4" />
                Upload Image
              </Button>
              
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

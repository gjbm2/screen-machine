import React, { useState, useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { Wand2, Upload, Image as ImageIcon, Settings, Hash, Workflow, Sparkles } from 'lucide-react';
import PromptInput from '@/components/prompt/PromptInput';
import WorkflowIconSelector from '@/components/prompt/WorkflowIconSelector';
import AdvancedOptions from '@/components/AdvancedOptions';
import { Card, CardContent } from '@/components/ui/card';
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle, DrawerTrigger } from '@/components/ui/drawer';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Separator } from '@/components/ui/separator';
import PromptExamples from '@/components/prompt/PromptExamples';
import ImageUploader from '@/components/prompt/ImageUploader';
import RefinerSelector from '@/components/prompt/RefinerSelector';
import { Badge } from '@/components/ui/badge';
import { ReferenceImageData } from '@/types/workflows';
import workflowsConfig from '@/data/workflows.json';

interface PromptFormProps {
  onSubmit: (
    prompt: string, 
    imageFiles?: (File | string)[], 
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>
  ) => void;
  isLoading: boolean;
  currentPrompt?: string | null;
  isFirstRun?: boolean;
}

const PromptForm: React.FC<PromptFormProps> = ({ 
  onSubmit, 
  isLoading, 
  currentPrompt = '',
  isFirstRun = false
}) => {
  const [prompt, setPrompt] = useState(currentPrompt || '');
  const [workflow, setWorkflow] = useState<string | null>(null);
  const [params, setParams] = useState<Record<string, any>>({});
  const [globalParams, setGlobalParams] = useState<Record<string, any>>({});
  const [refiner, setRefiner] = useState<string | null>(null);
  const [refinerParams, setRefinerParams] = useState<Record<string, any>>({});
  const [uploadedImages, setUploadedImages] = useState<string[]>([]);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const [isAdvancedOptionsOpen, setIsAdvancedOptionsOpen] = useState(false);
  const [isWorkflowPopoverOpen, setIsWorkflowPopoverOpen] = useState(false);
  const [isRefinerPopoverOpen, setIsRefinerPopoverOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('recommended');
  const [imageFiles, setImageFiles] = useState<(File | string)[] | null>(null);
  const [referenceImageData, setReferenceImageData] = useState<ReferenceImageData | null>(null);
  
  const promptInputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setPrompt(currentPrompt || '');
  }, [currentPrompt]);

  const handleSubmit = () => {
    if (!isLoading && prompt.trim()) {
      onSubmit(
        prompt,
        imageFiles || undefined,
        workflow || undefined,
        params,
        globalParams,
        refiner || undefined,
        refinerParams
      );
    }
  };

  const handleWorkflowSelect = (selectedWorkflow: string) => {
    setWorkflow(selectedWorkflow);
    setIsWorkflowPopoverOpen(false);
  };

  const handleRefinerSelect = (selectedRefiner: string) => {
    setRefiner(selectedRefiner);
    setIsRefinerPopoverOpen(false);
  };

  const handleParamsChange = (newParams: Record<string, any>) => {
    setParams(newParams);
  };

  const handleGlobalParamsChange = (newGlobalParams: Record<string, any>) => {
    setGlobalParams(newGlobalParams);
  };

  const handleRefinerParamsChange = (newRefinerParams: Record<string, any>) => {
    setRefinerParams(newRefinerParams);
  };

  const handleImageUpload = (files: File[], urls: string[]) => {
    setImageFiles(files);
    setUploadedImages(urls);
  };

  const handleClearImage = () => {
    setImageFiles(null);
    setUploadedImages([]);
  };

  const handleClearPrompt = () => {
    setPrompt('');
  };

  const handleWorkflowToggle = () => {
    setIsWorkflowPopoverOpen(!isWorkflowPopoverOpen);
  };

  const handleRefinerToggle = () => {
    setIsRefinerPopoverOpen(!isRefinerPopoverOpen);
  };

  const handleAdvancedOptionsToggle = () => {
    setIsAdvancedOptionsOpen(!isAdvancedOptionsOpen);
  };

  const handleTabChange = (value: string) => {
    setActiveTab(value);
  };

  const handleImageSelected = (event: Event) => {
    const customEvent = event as CustomEvent<{ files: File[]; urls: string[] }>;
    const files = customEvent.detail.files;
    const urls = customEvent.detail.urls;
    handleImageUpload(files, urls);
  };

  useEffect(() => {
    document.addEventListener('image-selected', handleImageSelected as EventListener);
    return () => {
      document.removeEventListener('image-selected', handleImageSelected as EventListener);
    };
  }, []);

  const handleReferenceImageChange = (data: ReferenceImageData | null) => {
    setReferenceImageData(data);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!isLoading && prompt.trim()) {
        handleSubmit();
      }
    }
  };

  return (
    <div className="w-full" onKeyDown={handleKeyDown}>
      <Card className={`${isFirstRun ? 'border-primary' : ''} transition-all`}>
        <CardContent className="p-0">
          <PromptInput 
            prompt={prompt} 
            isLoading={isLoading}
            uploadedImages={uploadedImages}
            onPromptChange={setPrompt}
            onSubmit={handleSubmit}
          />
        </CardContent>
      </Card>
    </div>
  );
};

export default PromptForm;

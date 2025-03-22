
import React from 'react';
import { Workflow } from '@/types/workflows';

export interface PromptFormProps {
  onSubmit: (
    prompt: string, 
    imageFiles?: File[] | string[], 
    workflow?: string, 
    workflowParams?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>
  ) => void;
  isLoading?: boolean;
  currentPrompt?: string;
  isFirstRun?: boolean;
}

export interface ToolbarProps {
  isLoading: boolean;
  batchSize: number;
  selectedWorkflow?: string;
  selectedRefiner?: string;
  onImageUpload: (files: File[]) => void;
  onWorkflowChange: (workflow: string) => void;
  onRefinerChange: (refiner: string) => void;
  incrementBatchSize: () => void;
  decrementBatchSize: () => void;
  toggleAdvancedOptions: () => void;
  handleSubmit: () => void;
  prompt: string;
  isButtonDisabled: boolean;
  workflows: any[];
  isCompact: boolean;
  hasUploadedImages?: boolean;
}

export interface AdvancedOptionsProps {
  workflows: any[];
  selectedWorkflow?: string;
  onWorkflowChange: (workflow: string) => void;
  params: Record<string, any>;
  onParamChange: (param: string, value: any) => void;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  globalParams: Record<string, any>;
  onGlobalParamChange: (param: string, value: any) => void;
  selectedRefiner?: string;
  onRefinerChange: (refiner: string) => void;
  refinerParams: Record<string, any>;
  onRefinerParamChange: (param: string, value: any) => void;
}

export interface BatchControlProps {
  batchSize: number;
  incrementBatchSize: () => void;
  decrementBatchSize: () => void;
  isCompact?: boolean;
}

export interface ImagePreviewSectionProps {
  previewUrls: string[];
  handleRemoveImage: (index: number) => void;
  clearAllImages: () => void;
}

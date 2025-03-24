
import { ReactNode } from 'react';

export interface PromptFormProps {
  onSubmit: (
    prompt: string,
    imageFiles?: File[] | string[],
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>
  ) => void;
  isLoading: boolean;
  currentPrompt?: string;
  selectedWorkflow?: string;
  isFirstRun?: boolean;
  activeTab?: string;
  compact?: boolean;
  onOpenAdvancedOptions?: () => void;
}

export interface FileUploadResult {
  fileUrl: string;
  fileName: string;
  file?: File;
}

// Missing interfaces that we need to add:
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

export interface ToolbarProps {
  isLoading: boolean;
  batchSize: number;
  selectedWorkflow: string;
  selectedRefiner: string;
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

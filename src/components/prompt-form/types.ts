
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

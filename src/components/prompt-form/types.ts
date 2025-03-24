
import React from 'react';

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
  isLoading?: boolean;
  currentPrompt?: string;
  isFirstRun?: boolean;
  onOpenAdvancedOptions?: () => void;
  isVerboseDebug?: boolean;
}

export interface ImagePreviewSectionProps {
  previewUrls: string[];
  handleRemoveImage: (index: number) => void;
  clearAllImages: () => void;
}

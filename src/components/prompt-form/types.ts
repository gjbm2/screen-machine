
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
  selectedWorkflow: string;
  selectedRefiner: string;
  onImageUpload: (files: File[]) => void;
  onWorkflowChange: (workflow: string) => void;
  onRefinerChange: (refiner: string) => void;
  incrementBatchSize: (e: React.MouseEvent) => void;
  decrementBatchSize: (e: React.MouseEvent) => void;
  toggleAdvancedOptions: () => void;
  handleSubmit: () => void;
  prompt: string;
  isButtonDisabled: boolean;
  workflows: Workflow[];
  isCompact: boolean;
}

export interface BatchControlProps {
  batchSize: number;
  incrementBatchSize: (e: React.MouseEvent) => void;
  decrementBatchSize: (e: React.MouseEvent) => void;
  isCompact?: boolean;
}

import { ReactNode } from 'react';
import { Workflow as TypedWorkflow } from '@/types/workflows';

export type Workflow = TypedWorkflow;

export interface WorkflowProps {
  id: string;
  name: string;
  icon: string;
  description?: string;
  default?: boolean;
  input?: ('text' | 'image')[];
  async?: boolean;
  runpod_id?: string;
  processing_stages?: string[];
  params: any[];
}

export interface PromptFormProps {
  onSubmit: (
    prompt: string,
    imageFiles?: (File | string)[],
    workflow?: string,
    workflowParams?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>,
    publishDestination?: string,
    batchId?: string
  ) => void;
  isLoading?: boolean;
  currentPrompt?: string;
  isFirstRun?: boolean;
  onOpenAdvancedOptions?: () => void;
  selectedWorkflow?: string;
  selectedRefiner?: string;
  selectedPublish?: string;
  workflowParams?: Record<string, any>;
  refinerParams?: Record<string, any>;
  globalParams?: Record<string, any>;
  onWorkflowChange?: (workflowId: string) => void;
  onRefinerChange?: (refinerId: string) => void;
  onPublishChange?: (publishId: string) => void;
}

export interface ToolbarProps {
  isLoading: boolean;
  selectedWorkflow: string;
  selectedRefiner: string;
  selectedPublish: string;
  onImageUpload: (files: File[]) => void;
  onWorkflowChange: (workflowId: string) => void;
  onRefinerChange: (refinerId: string) => void;
  onPublishChange: (publishId: string) => void;
  toggleAdvancedOptions: () => void;
  handleSubmit: () => void;
  prompt: string;
  isButtonDisabled: boolean;
  workflows: WorkflowProps[];
  isCompact: boolean;
  hasUploadedImages?: boolean;
}

export interface AdvancedOptionsPanelProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  workflows: WorkflowProps[];
  selectedWorkflow: string;
  currentParams: Record<string, any>;
  currentGlobalParams: Record<string, any>;
  onWorkflowChange: (workflowId: string) => void;
  onParamsChange: (params: Record<string, any>) => void;
  onGlobalParamChange: (paramId: string, value: any) => void;
  selectedRefiner: string;
  refinerParams: Record<string, any>;
  onRefinerChange: (refinerId: string) => void;
  onRefinerParamChange: (paramId: string, value: any) => void;
}

export interface PublishDestinationProps {
  id: string;
  name: string;
  icon: string;
  type: string;
  description: string;
}

export interface PublishSelectorProps {
  selectedPublish: string;
  onPublishChange: (publishId: string) => void;
  isCompact?: boolean;
}

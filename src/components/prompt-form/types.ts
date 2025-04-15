
export interface WorkflowProps {
  id: string;
  name: string;
  description: string;
  icon?: string;
  default?: boolean;
  input?: ('text' | 'image')[];
  async?: boolean;
  params?: Array<{
    id: string;
    name: string;
    type: string;
    options?: string[];
    default?: any;
  }>;
}

export interface PromptFormProps {
  onSubmit: (
    prompt: string,
    imageFiles?: (File | string)[],
    workflow?: string,
    params?: Record<string, any>,
    globalParams?: Record<string, any>,
    refiner?: string,
    refinerParams?: Record<string, any>,
    publish?: string
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

export interface ImagePreviewSectionProps {
  previewUrls: string[];
  handleRemoveImage: (index: number) => void;
  clearAllImages: () => void;
}

export interface ToolbarProps {
  isLoading: boolean;
  selectedWorkflow: string;
  selectedRefiner: string;
  selectedPublish?: string;
  onImageUpload: (files: File[]) => void;
  onWorkflowChange: (workflowId: string) => void;
  onRefinerChange: (refinerId: string) => void;
  onPublishChange?: (publishId: string) => void;
  toggleAdvancedOptions: () => void;
  handleSubmit: () => void;
  prompt: string;
  isButtonDisabled: boolean;
  workflows: WorkflowProps[];
  isCompact: boolean;
  hasUploadedImages?: boolean;
}

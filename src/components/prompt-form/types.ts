
export interface PromptFormProps {
  onSubmit: (
    prompt: string,
    imageFiles?: File[] | string[],
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
  // Add props for external state
  selectedWorkflow?: string;
  selectedRefiner?: string;
  workflowParams?: Record<string, any>;
  refinerParams?: Record<string, any>;
  globalParams?: Record<string, any>;
}

export interface WorkflowProps {
  id: string;
  name: string;
  description?: string;
  params?: WorkflowParam[]; // Add the params property to match Workflow interface
}

export interface WorkflowParam {
  id: string;
  name: string;
  type: 'select' | 'checkbox' | 'range' | 'text';
  options?: string[];
  default?: string | boolean | number;
}

export interface RefinerProps {
  id: string;
  name: string;
  description?: string;
  icon?: string;
}

// Add missing type for ImagePreviewSection
export interface ImagePreviewSectionProps {
  previewUrls: string[];
  handleRemoveImage: (index: number) => void;
  clearAllImages: () => void;
}

// Add missing type for ToolbarProps
export interface ToolbarProps {
  isLoading: boolean;
  selectedWorkflow: string;
  selectedRefiner: string;
  selectedPublish: string;
  onImageUpload: (files: File[]) => void;
  onWorkflowChange: (workflow: string) => void;
  onRefinerChange: (refiner: string) => void;
  onPublishChange: (publish: string) => void;
  toggleAdvancedOptions: () => void;
  handleSubmit: () => void;
  prompt: string;
  isButtonDisabled: boolean;
  workflows: WorkflowProps[]; // Keep this as WorkflowProps[] since that's what the component expects
  isCompact: boolean;
  hasUploadedImages?: boolean;
}

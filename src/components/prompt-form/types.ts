
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
}

export interface RefinerProps {
  id: string;
  name: string;
  description?: string;
  icon?: string;
}

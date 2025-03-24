
export interface WorkflowProps {
  id: string;
  name: string;
  description: string;
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
  selectedWorkflow?: string;
  selectedRefiner?: string;
  workflowParams?: Record<string, any>;
  refinerParams?: Record<string, any>;
  globalParams?: Record<string, any>;
  onWorkflowChange?: (workflowId: string) => void;
  onRefinerChange?: (refinerId: string) => void;
}

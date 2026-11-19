
export interface WorkflowParam {
  id: string;
  name: string;
  type: "select" | "checkbox" | "range" | "text" | "number";
  options?: string[] | { value: string; label: string }[];
  default?: string | number | boolean;
  /** A number field's bounds. */
  min?: number;
  max?: number;
}

export interface Workflow {
  id: string;
  name: string;
  icon?: string;
  /** One short line for people choosing it (the generator's workflow menu). */
  summary?: string;
  /** What it is, for the people who maintain it (models, steps, history). */
  description: string;
  default?: boolean;
  input?: string[];
  async?: boolean;
  params: WorkflowParam[];

  // NEW optional fields seen in the JSON
  runpod_id?: string;
  alexavisible?: boolean;
  style_guidance?: string;
  style_descriptor?: string;
  uses_images?: number;
  processing_stages?: { name: string; weight: number }[];
}

// Add the ImageGenerationStatus type that was removed
export type ImageGenerationStatus = 'generating' | 'completed' | 'error' | 'failed' | 'to_update';

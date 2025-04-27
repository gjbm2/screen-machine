declare module '@jsonforms/react' {
  import { ReactNode } from 'react';

  export interface JsonFormsProps {
    schema: any;
    data: any;
    uischema?: any;
    renderers?: any[];
    cells?: any[];
    onChange?: (state: { data: any; errors: any[] }) => void;
    config?: {
      restrict?: boolean;
      trim?: boolean;
      showUnfocusedDescription?: boolean;
      hideRequiredAsterisk?: boolean;
    };
  }

  export const JsonForms: React.FC<JsonFormsProps>;
}

declare module '@jsonforms/material-renderers' {
  export const materialRenderers: any[];
  export const materialCells: any[];
} 
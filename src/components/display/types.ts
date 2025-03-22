
export type ShowMode = 'fit' | 'fill' | 'actual';

export interface DisplayParams {
  output: string | null;
  showMode: ShowMode;
  refreshInterval: number;
  backgroundColor: string;
  debugMode: boolean;
}


export type ShowMode = 'fit' | 'fill' | 'actual' | 'stretch';
export type PositionMode = 'top-left' | 'top-center' | 'top-right' | 'center-left' | 'center' | 'center-right' | 'bottom-left' | 'bottom-center' | 'bottom-right';
export type CaptionPosition = 'top-left' | 'top-center' | 'top-right' | 'bottom-left' | 'bottom-center' | 'bottom-right';

export interface DisplayParams {
  output: string | null;
  showMode: ShowMode;
  position: PositionMode;
  refreshInterval: number;
  backgroundColor: string;
  debugMode: boolean;
  data?: string | null;
  caption?: string | null;
  captionPosition?: CaptionPosition;
  captionSize?: string;
  captionColor?: string;
  captionFont?: string;
}

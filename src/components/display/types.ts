
export type ShowMode = 'fit' | 'fill' | 'actual' | 'stretch';
export type PositionMode = 'top-left' | 'top-center' | 'top-right' | 'center-left' | 'center' | 'center-right' | 'bottom-left' | 'bottom-center' | 'bottom-right';
export type CaptionPosition = 'top-left' | 'top-center' | 'top-right' | 'middle-left' | 'middle-center' | 'middle-right' | 'bottom-left' | 'bottom-center' | 'bottom-right';
export type TransitionType = 'cut' | 'fade-fast' | 'fade-slow';

export interface DisplayParams {
  output: string | null;
  showMode: ShowMode;
  position: PositionMode;
  refreshInterval: number;
  backgroundColor: string;
  debugMode: boolean;
  data?: any;
  caption: string | null;
  captionPosition: CaptionPosition;
  captionSize: string;
  captionColor: string;
  captionFont: string;
  captionBgColor: string;
  captionBgOpacity: number;
  transition: TransitionType;
}

export interface MetadataEntry {
  key: string;
  value: string;
}

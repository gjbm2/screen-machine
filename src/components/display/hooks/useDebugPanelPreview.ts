
import { useEffect } from 'react';
import { DisplayParams } from '../types';

interface UseDebugPanelPreviewProps {
  showMode: DisplayParams['showMode']; 
  position: DisplayParams['position'];
  backgroundColor: string;
  captionPosition: DisplayParams['captionPosition'];
  captionSize: string;
  captionColor: string;
  captionFont: string;
  captionBgColor: string;
  captionBgOpacity: number;
  previewCaption: string | null;
  onApplyCaption: (caption: string | null) => void;
  imageUrl: string | null;
}

export const useDebugPanelPreview = ({
  showMode, 
  position, 
  backgroundColor, 
  captionPosition, 
  captionSize, 
  captionColor, 
  captionFont, 
  captionBgColor, 
  captionBgOpacity, 
  previewCaption,
  onApplyCaption,
  imageUrl
}: UseDebugPanelPreviewProps) => {
  
  // Update the preview whenever relevant settings change
  useEffect(() => {
    if (imageUrl) {
      console.log('[useDebugPanelPreview] Applying caption with preview settings');
      onApplyCaption(previewCaption);
    }
  }, [
    showMode, 
    position, 
    backgroundColor, 
    captionPosition, 
    captionSize, 
    captionColor, 
    captionFont, 
    captionBgColor, 
    captionBgOpacity, 
    previewCaption,
    onApplyCaption,
    imageUrl
  ]);
};

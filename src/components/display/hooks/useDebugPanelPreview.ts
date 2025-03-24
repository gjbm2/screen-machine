
import { useEffect } from 'react';

interface UseDebugPanelPreviewProps {
  previewCaption: string | null;
  onApplyCaption: (caption: string | null) => void;
  imageUrl: string | null;
}

export const useDebugPanelPreview = ({
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
  }, [previewCaption, onApplyCaption, imageUrl]);
};

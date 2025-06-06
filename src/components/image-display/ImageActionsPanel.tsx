
import React from 'react';
import { Button } from '@/components/ui/button';
import ImageActions from '@/components/ImageActions';

interface ImageActionsPanelProps {
  show: boolean;
  imageUrl: string;
  onCreateAgain?: () => void;
  onUseAsInput?: () => void;
  onDeleteImage?: () => void;
  generationInfo?: {
    prompt: string;
    workflow: string;
    params?: Record<string, any>;
  };
  title?: string; // Title field
  referenceImageUrl?: string; // Added referenceImageUrl property
}

const ImageActionsPanel: React.FC<ImageActionsPanelProps> = ({ 
  show,
  imageUrl,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  generationInfo,
  title, // Use title from props
  referenceImageUrl // Add referenceImageUrl parameter
}) => {
  if (!show) return null;

  return (
    <div className="absolute bottom-0 left-0 right-0 bg-black/80 flex flex-col items-center justify-center p-2 z-20">
      {/* Action Buttons */}
      <div className="flex gap-2 justify-center">
        <ImageActions
          imageUrl={imageUrl}
          onCreateAgain={onCreateAgain}
          onUseAsInput={onUseAsInput}
          onDeleteImage={onDeleteImage}
          generationInfo={generationInfo}
          alwaysVisible={true}
          title={title} // Pass title through
        />
      </div>
    </div>
  );
};

export default ImageActionsPanel;

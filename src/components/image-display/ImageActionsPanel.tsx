
import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import ImageActions from '@/components/ImageActions';
import ReferenceImageSection from './ReferenceImageSection';

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
  referenceImageUrl?: string;
}

const ImageActionsPanel: React.FC<ImageActionsPanelProps> = ({ 
  show,
  imageUrl,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  generationInfo,
  referenceImageUrl
}) => {
  const [isReferenceDialogOpen, setIsReferenceDialogOpen] = useState(false);

  const handleReferenceImageClick = () => {
    setIsReferenceDialogOpen(true);
  };

  if (!show) return null;

  return (
    <div className="absolute inset-0 bg-black/80 flex flex-col items-center justify-center p-4 z-20">
      {/* Reference Image Section - now appears above the prompt */}
      {referenceImageUrl && (
        <ReferenceImageSection 
          referenceImageUrl={referenceImageUrl}
          onReferenceImageClick={handleReferenceImageClick}
        />
      )}
      
      {/* Prompt Information */}
      {generationInfo?.prompt && (
        <div className="text-white text-center mt-4 mb-4">
          <p className="text-sm text-gray-300 mb-1">Prompt:</p>
          <p className="text-sm">{generationInfo.prompt}</p>
        </div>
      )}
      
      {/* Workflow Information */}
      {generationInfo?.workflow && (
        <p className="text-xs text-gray-400 mb-4">{generationInfo.workflow}</p>
      )}
      
      {/* Action Buttons */}
      <div className="flex gap-2 justify-center">
        <ImageActions
          imageUrl={imageUrl}
          onCreateAgain={onCreateAgain}
          onUseAsInput={onUseAsInput}
          onDeleteImage={onDeleteImage}
          generationInfo={generationInfo}
          alwaysVisible={true}
        />
      </div>
    </div>
  );
};

export default ImageActionsPanel;

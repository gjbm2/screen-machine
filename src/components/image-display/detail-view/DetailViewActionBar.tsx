
import React from 'react';
import ImageActions from '@/components/ImageActions';

interface DetailViewActionBarProps {
  imageUrl: string;
  onCreateAgain: () => void;
  onUseAsInput?: () => void;
  onDeleteImage: () => void;
  generationInfo: {
    prompt: string;
    workflow: string;
    params?: Record<string, any>;
    referenceImageUrl?: string;
  };
}

const DetailViewActionBar: React.FC<DetailViewActionBarProps> = ({
  imageUrl,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  generationInfo
}) => {
  return (
    <div className="flex justify-center py-3 bg-background/80 backdrop-blur-sm">
      <ImageActions
        imageUrl={imageUrl}
        onCreateAgain={onCreateAgain}
        onUseAsInput={onUseAsInput}
        onDeleteImage={onDeleteImage}
        generationInfo={generationInfo}
        alwaysVisible={true}
        isFullScreen={true}
      />
    </div>
  );
};

export default DetailViewActionBar;

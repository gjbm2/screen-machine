
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
    <div className="flex justify-center space-x-3 py-2">
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

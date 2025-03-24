
import React from 'react';
import { ShowMode, PositionMode } from '../../types';

interface ImageDisplayProps {
  imageUrl: string | null;
  imageKey: number;
  showMode: ShowMode;
  position: PositionMode;
  backgroundColor: string;
  onImageError: () => void;
  onImageLoad: (e: React.SyntheticEvent<HTMLImageElement>) => void;
  imageDimensions: { width: number; height: number };
  imageRef: React.RefObject<HTMLImageElement>;
  getImageStyle: () => React.CSSProperties;
}

export const ImageDisplay: React.FC<ImageDisplayProps> = ({
  imageUrl,
  imageKey,
  onImageError,
  onImageLoad,
  imageRef,
  getImageStyle
}) => {
  if (!imageUrl) {
    return (
      <div className="w-full h-full flex items-center justify-center text-gray-500">
        No image to display
      </div>
    );
  }

  return (
    <img
      ref={imageRef}
      key={`image-${imageKey}`}
      src={imageUrl}
      alt="Preview"
      className="max-w-full max-h-full"
      onLoad={onImageLoad}
      onError={onImageError}
      style={getImageStyle()}
    />
  );
};

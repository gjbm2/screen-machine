import React from 'react';
import { Maximize2 } from 'lucide-react';
import { ImageItem } from '@/types/image-types';

// Temporary stub until HoverActionBar is implemented
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore
import HoverActionBar from './HoverActionBar';

export interface ImageCardProps {
  image: ImageItem;
  // Dragging handled by parent useSortable; no internal sensors.
  /** Called when user clicks the main image (non-drag) */
  onClick?: (img: ImageItem) => void;
  /** Additional class names */
  className?: string;
  /** Index within its group – needed for fullscreen navigation */
  index?: number;
}

const ImageCard: React.FC<ImageCardProps> = ({
  image,
  onClick,
  className = '',
  index = 0,
}) => {
  const style: React.CSSProperties = {
    cursor: 'grab',
  };

  const handleClick = () => onClick?.(image);

  return (
    <div style={style} className={`relative group ${className}`}>
      {/* image */}
      <img
        src={image.urlThumb || image.urlFull}
        alt={image.promptKey}
        className="w-full h-auto object-cover rounded-md select-none"
        draggable={false}
        onClick={handleClick}
      />

      {/* top-right expand icon */}
      {/* onExpand && (
        <button
          onClick={handleExpand}
          className="absolute top-1 right-1 bg-black/60 hover:bg-black/80 text-white rounded-full p-0.5 opacity-0 group-hover:opacity-100 transition-opacity"
        >
          <Maximize2 className="h-4 w-4" />
        </button>
      )} */}

      {/* hover action bar (desktop) or tap reveal */}
      {HoverActionBar ? <HoverActionBar image={image} /> : null}
    </div>
  );
};

export default ImageCard; 

import React from 'react';
import { Button } from '@/components/ui/button';
import { Trash2 } from 'lucide-react';
import { useIsMobile } from '@/hooks/use-mobile';

interface ThumbnailGalleryProps {
  images: any[];
  batchId: string;
  activeIndex: number;
  onThumbnailClick: (index: number) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onCreateAgain?: () => void;
}

const ThumbnailGallery: React.FC<ThumbnailGalleryProps> = ({
  images,
  batchId,
  activeIndex,
  onThumbnailClick,
  onDeleteImage,
  onCreateAgain
}) => {
  const isMobile = useIsMobile();
  
  // If no images, don't render
  if (!images || images.length <= 1) return null;
  
  // Determine grid columns based on device
  const gridCols = isMobile ? 'grid-cols-4' : 'grid-cols-6 md:grid-cols-8';
  
  return (
    <div className={`grid ${gridCols} gap-1 mt-1`}>
      {images.map((image, index) => (
        <div 
          key={`${batchId}-thumb-${index}`}
          className={`relative aspect-square cursor-pointer rounded overflow-hidden border-2 ${activeIndex === index ? 'border-primary' : 'border-transparent'}`}
          onClick={() => onThumbnailClick(index)}
        >
          {image.url && (
            <div className="relative group w-full h-full">
              <img 
                src={image.url} 
                alt={image.prompt || `Generated image ${index + 1}`}
                className="w-full h-full object-cover"
              />
              
              <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-all duration-200 opacity-0 group-hover:opacity-100">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute bottom-0 right-0 h-6 w-6 bg-black/70 hover:bg-red-600/80 text-white rounded-tl rounded-br-none"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteImage(batchId, index);
                  }}
                >
                  <Trash2 className="h-3 w-3" />
                </Button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

export default ThumbnailGallery;

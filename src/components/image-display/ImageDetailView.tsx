
import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import ImageActions from '@/components/ImageActions';
import ThumbnailGallery from './ThumbnailGallery';
import ReferenceImagesSection from './ReferenceImagesSection';
import NavigationControls from './NavigationControls';

interface ImageDetailViewProps {
  batchId: string;
  images: Array<{
    url: string;
    prompt?: string;
    workflow: string;
    batchIndex?: number;
    params?: Record<string, any>;
    referenceImageUrl?: string;
  }>;
  activeIndex?: number;
  onSetActiveIndex?: (index: number) => void;
  onNavigatePrev?: (e: React.MouseEvent) => void;
  onNavigateNext?: (e: React.MouseEvent) => void;
  onToggleExpand?: (batchId: string) => void;
  onDeleteImage?: (batchId: string, index: number) => void;
  onCreateAgain?: (batchId: string) => void;
  onUseAsInput?: (imageUrl: string) => void;
  allImages?: any[];
  isNavigatingAllImages?: boolean;
  onNavigateGlobal?: (index: number) => void;
  currentGlobalIndex?: number;
}

const ImageDetailView = ({ 
  batchId,
  images,
  activeIndex = 0,
  onSetActiveIndex,
  onNavigatePrev,
  onNavigateNext,
  onToggleExpand,
  onDeleteImage,
  onCreateAgain,
  onUseAsInput,
  allImages,
  isNavigatingAllImages = false,
  onNavigateGlobal,
  currentGlobalIndex
}: ImageDetailViewProps) => {
  const [currentIndex, setCurrentIndex] = useState(activeIndex);
  
  useEffect(() => {
    setCurrentIndex(activeIndex);
  }, [activeIndex]);

  const handleThumbnailClick = (index: number) => {
    setCurrentIndex(index);
    if (onSetActiveIndex) {
      onSetActiveIndex(index);
    }
  };
  
  const currentImage = images[currentIndex];
  
  // Add keyboard navigation for left and right arrow keys
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft') {
        if (onNavigatePrev) {
          onNavigatePrev(new Event('keydown') as any);
        }
      } else if (event.key === 'ArrowRight') {
        if (onNavigateNext) {
          onNavigateNext(new Event('keydown') as any);
        }
      }
    };
    
    window.addEventListener('keydown', handleKeyDown);
    
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [onNavigatePrev, onNavigateNext]);

  return (
    <div className="flex flex-col h-full">
      <Card className="flex-grow flex flex-col">
        <CardContent className="flex-grow flex flex-col p-4">
          {currentImage ? (
            <>
              <div className="relative">
                <img
                  src={currentImage.url}
                  alt={`Batch image ${currentIndex + 1}`}
                  className="w-full h-auto object-contain rounded-md"
                />
              </div>
              
              <div className="mt-4">
                <ImageActions
                  imageUrl={currentImage.url}
                  onCreateAgain={onCreateAgain ? () => onCreateAgain(batchId) : undefined}
                  onUseAsInput={onUseAsInput ? () => onUseAsInput(currentImage.url) : undefined}
                  generationInfo={{
                    prompt: currentImage.prompt || '',
                    workflow: currentImage.workflow || '',
                    params: currentImage.params
                  }}
                  isFullScreen={true}
                />
              </div>
            </>
          ) : (
            <p className="text-center text-muted-foreground">
              No image selected.
            </p>
          )}
        </CardContent>
      </Card>
      
      {currentImage?.referenceImageUrl && (
        <ReferenceImagesSection referenceImageUrl={currentImage.referenceImageUrl} />
      )}
      
      <div className="mt-4">
        <ThumbnailGallery
          images={images}
          batchId={batchId}
          activeIndex={currentIndex}
          onThumbnailClick={handleThumbnailClick}
          onDeleteImage={onDeleteImage}
          onCreateAgain={onCreateAgain}
        />
      </div>
      
      <NavigationControls
        activeIndex={currentIndex}
        totalImages={images.length}
        onNavigatePrev={onNavigatePrev}
        onNavigateNext={onNavigateNext}
        isNavigatingAllImages={isNavigatingAllImages}
        onNavigateGlobal={onNavigateGlobal}
        currentGlobalIndex={currentGlobalIndex}
        allImages={allImages}
      />
    </div>
  );
};

export default ImageDetailView;

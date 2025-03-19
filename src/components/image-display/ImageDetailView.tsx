
import React from 'react';
import { Button } from '@/components/ui/button';
import { ChevronUp } from 'lucide-react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent } from '@/components/ui/dropdown-menu';
import ImageActions from '@/components/ImageActions';
import NavigationControls from './NavigationControls';
import ThumbnailGallery from './ThumbnailGallery';
import ImageBatchItem from './ImageBatchItem';

interface ImageDetailViewProps {
  batchId: string;
  images: Array<{
    url: string;
    prompt?: string;
    workflow: string;
    status?: 'generating' | 'completed' | 'error';
    params?: Record<string, any>;
  }>;
  activeIndex: number;
  onSetActiveIndex: (index: number) => void;
  onNavigatePrev: (e: React.MouseEvent) => void;
  onNavigateNext: (e: React.MouseEvent) => void;
  onToggleExpand: (batchId: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onCreateAgain: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
}

const ImageDetailView: React.FC<ImageDetailViewProps> = ({
  batchId,
  images,
  activeIndex,
  onSetActiveIndex,
  onNavigatePrev,
  onNavigateNext,
  onToggleExpand,
  onDeleteImage,
  onCreateAgain,
  onUseAsInput
}) => {
  const activeImage = images[activeIndex];
  
  return (
    <div className="p-4 space-y-4">
      {/* Selected image view */}
      <div className="aspect-square relative bg-secondary/10 rounded-md overflow-hidden max-w-lg mx-auto group">
        <ImageBatchItem 
          image={activeImage}
          batchId={batchId}
          index={activeIndex}
          total={images.length}
          onCreateAgain={onCreateAgain}
          onUseAsInput={onUseAsInput}
          onDeleteImage={onDeleteImage}
        />
        
        {/* Navigation controls in expanded view */}
        {images.length > 1 && (
          <NavigationControls 
            onPrevious={onNavigatePrev}
            onNext={onNavigateNext}
          />
        )}
      </div>
      
      {/* Action buttons in expanded view */}
      <div className="flex flex-wrap justify-center gap-2 mt-4">
        <TooltipProvider>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm">Actions</Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="center">
              <ImageActions 
                imageUrl={activeImage.url} 
                onUseAsInput={() => onUseAsInput && onUseAsInput(activeImage.url)}
                onCreateAgain={() => onCreateAgain(batchId)}
                generationInfo={{
                  prompt: activeImage.prompt || '',
                  workflow: activeImage.workflow,
                  params: activeImage.params
                }}
                isFullScreen
              />
            </DropdownMenuContent>
          </DropdownMenu>
        </TooltipProvider>
      </div>
      
      {/* Thumbnail gallery */}
      <ThumbnailGallery 
        images={images}
        batchId={batchId}
        activeIndex={activeIndex}
        onThumbnailClick={onSetActiveIndex}
        onDeleteImage={onDeleteImage}
        onCreateAgain={onCreateAgain}
      />
      
      {/* Compact roll-up button attached to bottom of container */}
      <div className="flex justify-center">
        <Button 
          variant="ghost" 
          size="sm"
          className="rounded-t-none rounded-b-lg bg-card hover:bg-accent/20 text-xs h-7 px-3 -mt-1 border-t border-x shadow"
          onClick={() => onToggleExpand(batchId)}
        >
          <ChevronUp className="h-4 w-4 mr-1" />
          Roll Up
        </Button>
      </div>
    </div>
  );
};

export default ImageDetailView;

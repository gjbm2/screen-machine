
import React from 'react';
import { Card } from '@/components/ui/card';
import { Collapsible, CollapsibleContent } from '@/components/ui/collapsible';
import SortableImageContainer from './SortableImageContainer';
import ImageBatchItem from './ImageBatchItem';
import NavigationControls from './NavigationControls';
import ImageDetailView from './ImageDetailView';
import { DropdownMenu } from '@/components/ui/dropdown-menu';

interface ImageBatchProps {
  batchId: string;
  images: Array<{
    url: string;
    prompt?: string;
    workflow: string;
    timestamp: number;
    params?: Record<string, any>;
    batchId?: string;
    batchIndex?: number;
    status?: 'generating' | 'completed' | 'error';
  }>;
  isExpanded: boolean;
  activeIndex: number;
  activeBatchId: string | null;
  onSetActiveBatchId: (id: string | null) => void;
  onSetActiveImageIndex: (batchId: string, index: number) => void;
  onToggleExpandBatch: (id: string) => void;
  onNavigatePrevImage: (batchId: string, imagesCount: number) => void;
  onNavigateNextImage: (batchId: string, imagesCount: number) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onCreateAgain: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
  extraComponents?: React.ReactNode;
}

const ImageBatch: React.FC<ImageBatchProps> = ({
  batchId,
  images,
  isExpanded,
  activeIndex,
  activeBatchId,
  onSetActiveBatchId,
  onSetActiveImageIndex,
  onToggleExpandBatch,
  onNavigatePrevImage,
  onNavigateNextImage,
  onDeleteImage,
  onCreateAgain,
  onUseAsInput,
  extraComponents
}) => {
  if (!images || images.length === 0) return null;
  
  const activeImage = images[activeIndex];
  const isActive = activeBatchId === batchId;
  
  return (
    <Collapsible 
      key={batchId} 
      open={isExpanded}
      className={`overflow-hidden rounded-lg bg-card border ${
        isExpanded ? 'col-span-1 sm:col-span-2 md:col-span-3 lg:col-span-4 xl:col-span-5' : 'col-span-1'
      }`}
      id={`batch-${batchId}`}
    >
      <SortableImageContainer 
        batchId={batchId} 
        batch={{ images }}
        isExpanded={isExpanded}
        toggleExpand={onToggleExpandBatch}
      >
        {!isExpanded && (
          <Card 
            className="overflow-hidden relative border-0 rounded-none"
            onMouseEnter={() => onSetActiveBatchId(batchId)}
            onMouseLeave={() => onSetActiveBatchId(null)}
          >
            {/* Collapsed view (carousel-like navigation) */}
            <ImageBatchItem 
              image={activeImage} 
              batchId={batchId} 
              index={activeIndex}
              total={images.length}
              onCreateAgain={onCreateAgain}
              onUseAsInput={onUseAsInput}
              onDeleteImage={onDeleteImage}
            />
            
            {/* Navigation controls */}
            {images.length > 1 && (
              <NavigationControls 
                onPrevious={(e) => {
                  e.stopPropagation();
                  onNavigatePrevImage(batchId, images.length);
                }}
                onNext={(e) => {
                  e.stopPropagation();
                  onNavigateNextImage(batchId, images.length);
                }}
              />
            )}
          </Card>
        )}
        {isExpanded && (
          <Card 
            className="overflow-hidden relative border-0 rounded-none"
            onMouseEnter={() => onSetActiveBatchId(batchId)}
            onMouseLeave={() => onSetActiveBatchId(null)}
          >
            {/* Empty content for expanded view */}
          </Card>
        )}
      </SortableImageContainer>
      
      {/* Expanded content */}
      <CollapsibleContent>
        <ImageDetailView 
          batchId={batchId}
          images={images}
          activeIndex={activeIndex}
          onSetActiveIndex={(index) => onSetActiveImageIndex(batchId, index)}
          onNavigatePrev={(e) => {
            e.stopPropagation();
            onNavigatePrevImage(batchId, images.length);
          }}
          onNavigateNext={(e) => {
            e.stopPropagation();
            onNavigateNextImage(batchId, images.length);
          }}
          onToggleExpand={onToggleExpandBatch}
          onDeleteImage={onDeleteImage}
          onCreateAgain={onCreateAgain}
          onUseAsInput={onUseAsInput}
        />
      </CollapsibleContent>
    </Collapsible>
  );
};

export default ImageBatch;

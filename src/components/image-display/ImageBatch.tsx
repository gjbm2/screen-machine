
import React, { useState, useEffect } from 'react';
import ImageBatchItem from './ImageBatchItem';
import SortableImageContainer from './SortableImageContainer';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Trash2, Plus } from 'lucide-react';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { ViewMode } from './ImageDisplay';
import { Table, TableBody, TableRow, TableCell } from '@/components/ui/table';
import { Skeleton } from "@/components/ui/skeleton";
import LoadingPlaceholder from './LoadingPlaceholder';
import GenerationFailedPlaceholder from './GenerationFailedPlaceholder';
import { ImageGenerationStatus } from '@/types/workflows';

interface Image {
  url: string;
  prompt: string;
  workflow: string;
  batchIndex: number;
  status: ImageGenerationStatus;
  referenceImageUrl?: string;
}

interface ImageBatchProps {
  batchId: string;
  images: Image[];
  isExpanded: boolean;
  toggleExpand: (id: string) => void;
  onImageClick: (url: string, prompt: string) => void;
  onCreateAgain: () => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: () => void;
  activeImageUrl: string | null;
  viewMode: ViewMode;
  onFullScreenClick?: (image: any) => void;
}

const ImageBatch: React.FC<ImageBatchProps> = ({
  batchId,
  images,
  isExpanded,
  toggleExpand,
  onImageClick,
  onCreateAgain,
  onDeleteImage,
  onDeleteContainer,
  activeImageUrl,
  viewMode,
  onFullScreenClick
}) => {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [activeImageIndex, setActiveImageIndex] = useState(0);
  
  // Reset active image index when images change
  useEffect(() => {
    if (images.length > 0 && activeImageIndex >= images.length) {
      setActiveImageIndex(0);
    }
  }, [images, activeImageIndex]);
  
  if (!images || images.length === 0) {
    return null;
  }
  
  const anyGenerating = images.some(img => img.status === 'generating');
  const completedImages = images.filter(img => img.status === 'completed');
  const failedImages = images.filter(img => img.status === 'failed' || img.status === 'error');
  
  // Always show containers regardless of generation status
  const allGeneratingWithoutUrl = images.every(img => img.status === 'generating' && !img.url);

  // Show all images in grid view for small mode
  if (viewMode === 'small' && completedImages.length === 0 && !anyGenerating && failedImages.length === 0) {
    return null;
  }
  
  const handleNavigatePrev = () => {
    if (activeImageIndex > 0) {
      setActiveImageIndex(activeImageIndex - 1);
    }
  };
  
  const handleNavigateNext = () => {
    if (activeImageIndex < completedImages.length - 1) {
      setActiveImageIndex(activeImageIndex + 1);
    }
  };

  const handleCreateAgain = () => {
    onCreateAgain();
  };

  const handleRetry = () => {
    onCreateAgain();
  };

  const handleFullScreenClick = (image: any) => {
    if (onFullScreenClick) {
      onFullScreenClick({
        ...completedImages[activeImageIndex],
        batchId,
        batchIndex: completedImages[activeImageIndex].batchIndex || activeImageIndex
      });
    } else if (!isExpanded) {
      // If not expanded and no fullscreen handler provided, expand
      toggleExpand(batchId);
    }
  };
  
  return (
    <SortableImageContainer 
      batchId={batchId}
      batch={{ images }}
      isExpanded={isExpanded}
      toggleExpand={toggleExpand}
      viewMode={viewMode}
    >
      {viewMode === 'table' ? (
        <Card className="rounded-t-none">
          <CardContent className="p-1">
            <Table>
              <TableBody>
                {completedImages.map((image, index) => (
                  <TableRow key={`${batchId}-${index}`}>
                    <TableCell className="p-1">
                      <div className="w-16 h-16 overflow-hidden rounded">
                        <img 
                          src={image.url}
                          alt={image.prompt}
                          className="w-full h-full object-cover"
                          onClick={() => onImageClick(image.url, image.prompt)}
                        />
                      </div>
                    </TableCell>
                    <TableCell className="p-1">
                      <p className="text-xs truncate text-muted-foreground max-w-md">{image.prompt}</p>
                    </TableCell>
                    <TableCell className="p-1 text-right">
                      <Button 
                        variant="ghost" 
                        size="icon" 
                        className="h-8 w-8"
                        onClick={() => onDeleteImage(batchId, image.batchIndex)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : isExpanded ? (
        <Card className="rounded-t-none">
          <CardContent className="p-2">
            {/* Replace with image detail content but use full screen trigger for any full screen actions */}
            <div className="space-y-2">
              {/* Main image display */}
              <div className="aspect-square relative bg-secondary/10 rounded-md overflow-hidden max-w-full mx-auto">
                {completedImages.length > 0 ? (
                  <ImageBatchItem
                    image={completedImages[activeImageIndex]}
                    batchId={batchId}
                    index={activeImageIndex}
                    total={completedImages.length}
                    onCreateAgain={handleCreateAgain}
                    onUseAsInput={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
                    onDeleteImage={onDeleteImage}
                    onFullScreen={() => handleFullScreenClick(completedImages[activeImageIndex])}
                    onImageClick={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
                    onNavigatePrev={completedImages.length > 1 ? handleNavigatePrev : undefined}
                    onNavigateNext={completedImages.length > 1 ? handleNavigateNext : undefined}
                    viewMode="normal"
                    showActions={true}
                  />
                ) : anyGenerating ? (
                  <LoadingPlaceholder prompt={images[0]?.prompt || null} />
                ) : failedImages.length > 0 ? (
                  <GenerationFailedPlaceholder 
                    prompt={failedImages[0]?.prompt || null} 
                    onRetry={handleRetry}
                  />
                ) : null}
              </div>

              {/* Navigation controls for expanded view are handled by ImageBatchItem */}
              
              {/* Thumbnail gallery */}
              {completedImages.length > 1 && (
                <div className="flex flex-wrap gap-1 justify-center pt-1">
                  {completedImages.map((image, idx) => (
                    <div 
                      key={`thumb-${batchId}-${idx}`}
                      className={`w-14 h-14 rounded-md overflow-hidden cursor-pointer border-2 ${
                        idx === activeImageIndex ? 'border-primary' : 'border-transparent'
                      }`}
                      onClick={() => setActiveImageIndex(idx)}
                    >
                      <img 
                        src={image.url} 
                        alt={`Thumbnail ${idx + 1}`}
                        className="w-full h-full object-cover"
                      />
                    </div>
                  ))}
                </div>
              )}
              
              {/* Compact roll-up button */}
              <div className="flex justify-center">
                <Button 
                  variant="ghost" 
                  size="sm"
                  className="rounded-lg bg-card hover:bg-accent/20 text-xs h-7 px-3 border shadow"
                  onClick={() => toggleExpand(batchId)}
                >
                  Roll Up
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card className="rounded-t-none">
          <CardContent className="p-2">
            <div className="grid gap-1 grid-cols-1">
              {completedImages.length > 0 ? (
                <ImageBatchItem
                  key={`${batchId}-${activeImageIndex}`}
                  image={completedImages[activeImageIndex]}
                  batchId={batchId}
                  index={activeImageIndex}
                  total={completedImages.length}
                  onCreateAgain={handleCreateAgain}
                  onUseAsInput={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
                  onDeleteImage={onDeleteImage}
                  onFullScreen={() => handleFullScreenClick(completedImages[activeImageIndex])}
                  onImageClick={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
                  onNavigatePrev={completedImages.length > 1 ? handleNavigatePrev : undefined}
                  onNavigateNext={completedImages.length > 1 ? handleNavigateNext : undefined}
                  viewMode={viewMode}
                  showActions={true}
                  isRolledUp={true}
                />
              ) : anyGenerating ? (
                <LoadingPlaceholder prompt={images[0]?.prompt || null} />
              ) : failedImages.length > 0 ? (
                <GenerationFailedPlaceholder 
                  prompt={failedImages[0]?.prompt || null} 
                  onRetry={handleRetry}
                />
              ) : null}
            </div>
          </CardContent>
        </Card>
      )}
      
      <Dialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete All Images</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete all images in this batch? This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDeleteDialog(false)}>Cancel</Button>
            <Button 
              variant="destructive" 
              onClick={() => {
                onDeleteContainer();
                setShowDeleteDialog(false);
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SortableImageContainer>
  );
};

export default ImageBatch;

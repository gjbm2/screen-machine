
import React, { useState, useEffect } from 'react';
import ImageBatchItem from './ImageBatchItem';
import SortableImageContainer from './SortableImageContainer';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Trash2, Plus } from 'lucide-react';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { ViewMode } from './ImageDisplay';
import { Table, TableBody, TableRow, TableCell } from '@/components/ui/table';
import ImageDetailView from './ImageDetailView';
import { Skeleton } from "@/components/ui/skeleton";
import LoadingPlaceholder from './LoadingPlaceholder';

interface Image {
  url: string;
  prompt: string;
  workflow: string;
  batchIndex: number;
  status: 'generating' | 'completed' | 'error';
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
  
  // Always show containers regardless of generation status
  const allGeneratingWithoutUrl = images.every(img => img.status === 'generating' && !img.url);

  // Show all images in grid view for small mode
  if (viewMode === 'small' && completedImages.length === 0 && !anyGenerating) {
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

  const handleFullScreenClick = (image: any) => {
    if (onFullScreenClick) {
      onFullScreenClick(image);
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
          <CardContent className="p-2">
            <Table>
              <TableBody>
                {completedImages.map((image, index) => (
                  <TableRow key={`${batchId}-${index}`}>
                    <TableCell className="p-2">
                      <div className="w-16 h-16 overflow-hidden rounded">
                        <img 
                          src={image.url}
                          alt={image.prompt}
                          className="w-full h-full object-cover"
                          onClick={() => onImageClick(image.url, image.prompt)}
                        />
                      </div>
                    </TableCell>
                    <TableCell className="p-2">
                      <p className="text-xs truncate text-muted-foreground max-w-md">{image.prompt}</p>
                    </TableCell>
                    <TableCell className="p-2 text-right">
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
          <CardContent className="p-4">
            <ImageDetailView
              batchId={batchId}
              images={completedImages.length > 0 ? completedImages : images}
              activeIndex={activeImageIndex < completedImages.length ? activeImageIndex : 0}
              onSetActiveIndex={setActiveImageIndex}
              onNavigatePrev={handleNavigatePrev}
              onNavigateNext={handleNavigateNext}
              onToggleExpand={() => toggleExpand(batchId)}
              onDeleteImage={onDeleteImage}
              onCreateAgain={handleCreateAgain}
              onUseAsInput={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
            />
          </CardContent>
        </Card>
      ) : (
        <Card className="rounded-t-none">
          <CardContent className="p-4">
            <div className="grid gap-4 grid-cols-1">
              {completedImages.length > 0 ? (
                <ImageBatchItem
                  key={`${batchId}-${activeImageIndex}`}
                  image={completedImages[activeImageIndex]}
                  batchId={batchId}
                  index={activeImageIndex}
                  total={completedImages.length}
                  onCreateAgain={() => onCreateAgain()}
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

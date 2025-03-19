
import React, { useState } from 'react';
import ImageBatchItem from './ImageBatchItem';
import SortableImageContainer from './SortableImageContainer';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Trash2, Plus } from 'lucide-react';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { ViewMode } from './ImageDisplay';

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
  viewMode
}) => {
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  
  if (!images || images.length === 0) {
    return null;
  }
  
  const anyGenerating = images.some(img => img.status === 'generating');
  const mostRecentImage = images[0];
  
  // If in small mode and not expanded, just show the first image
  if (viewMode === 'small' && !isExpanded) {
    return (
      <SortableImageContainer
        batchId={batchId}
        batch={{ images }}
        isExpanded={isExpanded}
        toggleExpand={toggleExpand}
        viewMode={viewMode}
      >
        <div 
          className="cursor-pointer"
          onClick={() => toggleExpand(batchId)}
        >
          <ImageBatchItem
            key={`${batchId}-0`}
            image={mostRecentImage}
            batchId={batchId}
            index={0}
            total={images.length}
            onImageClick={(url: string) => onImageClick(mostRecentImage.url, mostRecentImage.prompt)}
            onDeleteImage={onDeleteImage}
          />
        </div>
      </SortableImageContainer>
    );
  }
  
  // Do not display the container at all if all images are generating
  if (images.every(img => img.status === 'generating')) {
    return null;
  }
  
  const completedImages = images.filter(img => img.status === 'completed');
  
  return (
    <SortableImageContainer 
      batchId={batchId}
      batch={{ images }}
      isExpanded={isExpanded}
      toggleExpand={toggleExpand}
      viewMode={viewMode}
    >
      <Card className={`rounded-t-none ${viewMode === 'table' ? 'p-0' : ''}`}>
        <CardContent className={`${viewMode === 'table' ? 'p-2' : 'p-4'}`}>
          {viewMode === 'table' ? (
            <div className="space-y-2">
              {completedImages.map((image, index) => (
                <div key={`${batchId}-${index}`} className="flex items-center border-b border-border pb-2 last:border-0 last:pb-0">
                  <div className="w-16 h-16 mr-3 overflow-hidden rounded">
                    <img 
                      src={image.url}
                      alt={image.prompt}
                      className="w-full h-full object-cover"
                      onClick={() => onImageClick(image.url, image.prompt)}
                    />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs truncate text-muted-foreground">{image.prompt}</p>
                  </div>
                  <Button 
                    variant="ghost" 
                    size="icon" 
                    className="h-8 w-8 ml-2"
                    onClick={() => onDeleteImage(batchId, image.batchIndex)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <div className={`grid gap-4 ${viewMode === 'fullWidth' ? 'grid-cols-1' : 'grid-cols-2 md:grid-cols-3 lg:grid-cols-4'}`}>
              {completedImages.map((image, index) => (
                <ImageBatchItem
                  key={`${batchId}-${index}`}
                  image={image}
                  batchId={batchId}
                  index={index}
                  total={completedImages.length}
                  onImageClick={(url: string) => onImageClick(image.url, image.prompt)}
                  onDeleteImage={onDeleteImage}
                />
              ))}
              {anyGenerating && (
                <div className="relative aspect-square rounded-lg bg-muted animate-pulse flex items-center justify-center">
                  <div className="text-sm text-muted-foreground">Generating...</div>
                </div>
              )}
            </div>
          )}
          
          <div className="flex justify-between mt-4">
            <Button 
              variant="outline" 
              size="sm" 
              className="text-xs"
              onClick={onCreateAgain}
            >
              <Plus className="h-3 w-3 mr-1" /> Create Another
            </Button>
            
            <Button
              variant="ghost"
              size="sm"
              className="text-xs text-destructive hover:text-destructive"
              onClick={() => setShowDeleteDialog(true)}
            >
              <Trash2 className="h-3 w-3 mr-1" /> Delete All
            </Button>
          </div>
        </CardContent>
      </Card>
      
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

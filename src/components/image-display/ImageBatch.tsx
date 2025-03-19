import React, { useState } from 'react';
import ImageBatchItem from './ImageBatchItem';
import SortableImageContainer from './SortableImageContainer';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Trash2, Plus } from 'lucide-react';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { ViewMode } from './ImageDisplay';
import { Table, TableBody, TableRow, TableCell } from '@/components/ui/table';
import ImageDetailView from './ImageDetailView';

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
  const [activeImageIndex, setActiveImageIndex] = useState(0);
  
  if (!images || images.length === 0) {
    return null;
  }
  
  const anyGenerating = images.some(img => img.status === 'generating');
  const completedImages = images.filter(img => img.status === 'completed');
  
  if (images.every(img => img.status === 'generating')) {
    return null;
  }

  if (viewMode === 'small') {
    return (
      <>
        {completedImages.map((image, index) => (
          <ImageBatchItem
            key={`${batchId}-${index}`}
            image={image}
            batchId={batchId}
            index={index}
            total={completedImages.length}
            onDeleteImage={onDeleteImage}
            onImageClick={(url) => onImageClick(url, image.prompt)}
            viewMode="small"
            showActions={false}
          />
        ))}
      </>
    );
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
          ) : isExpanded ? (
            <ImageDetailView
              batchId={batchId}
              images={completedImages}
              activeIndex={activeImageIndex}
              onSetActiveIndex={setActiveImageIndex}
              onNavigatePrev={handleNavigatePrev}
              onNavigateNext={handleNavigateNext}
              onToggleExpand={toggleExpand}
              onDeleteImage={onDeleteImage}
              onCreateAgain={() => onCreateAgain()}
              onUseAsInput={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
            />
          ) : (
            <div className="grid gap-4 grid-cols-1">
              <ImageBatchItem
                key={`${batchId}-${activeImageIndex}`}
                image={completedImages[activeImageIndex]}
                batchId={batchId}
                index={activeImageIndex}
                total={completedImages.length}
                onCreateAgain={() => onCreateAgain()}
                onUseAsInput={(url) => onImageClick(url, completedImages[activeImageIndex]?.prompt || '')}
                onDeleteImage={onDeleteImage}
                onFullScreen={() => toggleExpand(batchId)}
                onImageClick={() => toggleExpand(batchId)}
                onNavigatePrev={completedImages.length > 1 ? handleNavigatePrev : undefined}
                onNavigateNext={completedImages.length > 1 ? handleNavigateNext : undefined}
                viewMode={viewMode}
                showActions={true}
              />
              
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
              onClick={() => onCreateAgain()}
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

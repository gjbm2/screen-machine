import React, { useState } from 'react';
import { ExpandableContainer } from '@/components/common';
import SortableImageGrid from '@/components/common/SortableImageGrid';
import { ImageItem } from '@/types/image-types';
import { GripVertical, Trash2, RefreshCw, MoreVertical, Copy, Share, Maximize2, ExternalLink, ChevronLeft, ChevronRight } from 'lucide-react';
import { GenerateAgainCard } from './GenerateAgainCard';
import styles from './recent.module.css';
import { useDraggable, useDroppable } from '@dnd-kit/core';
import { CSS } from '@dnd-kit/utilities';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';
import { Sparkles } from 'lucide-react';

// Add type declaration for window.generateImage
declare global {
  interface Window {
    generateImage: (params: {
      prompt: string;
      batch_id: string;
      workflow?: string;
      [key: string]: any;
    }) => Promise<void>;
  }
}

export interface RecentBatchPanelProps {
  batchId: string;
  images: ImageItem[];
  initialSelectedIndex?: number;
  initialCollapsed?: boolean;
  forceSelectId?: string;
  onDeleteImage?: (img: ImageItem) => void;
  onDeleteBatch?: (batchId: string) => void;
  onToggleFavorite?: (img: ImageItem) => void;
  onImageClick?: (img: ImageItem) => void;
  onCopyTo?: (img: ImageItem, destId: string) => void;
  onPublish?: (img: ImageItem, destId: string) => void;
  onUseAsPrompt?: (img: ImageItem) => void;
  onGenerateAgain?: (batchId: string) => void;
  publishDestinations?: Array<{id: string, name: string, headless: boolean}>;
  onSelectImage?: (batchId: string, imgId: string) => void;
  onCollapseChange?: (batchId: string, collapsed: boolean) => void;
}

export const RecentBatchPanel: React.FC<RecentBatchPanelProps> = ({
  batchId,
  images,
  initialSelectedIndex = 0,
  initialCollapsed = false,
  forceSelectId,
  onDeleteImage,
  onDeleteBatch,
  onToggleFavorite,
  onImageClick,
  onCopyTo,
  onPublish,
  onUseAsPrompt,
  onGenerateAgain,
  publishDestinations,
  onSelectImage,
  onCollapseChange
}) => {
  // Create a stable key for this component instance based on batchId
  const instanceKey = React.useRef(`batch-${batchId}-${Date.now()}`).current;
  
  // Track selected index and ID to persist across polling
  const [selectedIndex, setSelectedIndex] = useState<number>(
    initialSelectedIndex < images.length ? initialSelectedIndex : 0
  );
  const [selectedId, setSelectedId] = useState<string>(
    images[initialSelectedIndex]?.id || images[0]?.id || ''
  );
  
  const [isCollapsed, setIsCollapsed] = useState<boolean>(initialCollapsed);
  
  // The currently selected image to display on top
  const selectedImage = React.useMemo(() => {
    const byId = images.find(img => img.id === selectedId);
    return byId || images[selectedIndex] || images[0];
  }, [images, selectedId, selectedIndex]);
  
  // Draggable for selected image
  const {
    attributes: dragAttributes,
    listeners: dragListeners,
    setNodeRef: dragRefInternal,
    transform,
    isDragging,
  } = useDraggable({
    id: `selected:${selectedImage?.id || 'unknown'}`,
    data: {
      raw_url: selectedImage?.raw_url || selectedImage?.urlFull,
      thumbnail_url: selectedImage?.urlThumb,
      image: selectedImage,
      bucketId: '_recent'
    },
  });

  const { setNodeRef: dropRefInternal } = useDroppable({ id: `selected:${selectedImage?.id || 'unknown'}` });

  const mergedRef = (node: HTMLElement | null) => {
    dragRefInternal(node);
    dropRefInternal(node);
  };

  const dragStyle: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition: isDragging ? undefined : 'transform 0.2s ease',
    opacity: isDragging ? 0.6 : 1,
    cursor: isDragging ? 'grabbing' : 'grab',
    boxShadow: isDragging ? '0 4px 14px rgba(0,0,0,0.3)' : undefined,
    zIndex: isDragging ? 50 : undefined,
  };
  
  const handleToggle = (id: string) => {
    const newState = !isCollapsed;
    setIsCollapsed(newState);
    onCollapseChange?.(batchId, newState);
  };
  
  const handleDelete = (img: ImageItem) => {
    if (onDeleteImage) {
      onDeleteImage(img);
      
      // If the deleted image was selected, select another one
      if (img.id === selectedImage.id) {
        // Find a new index that's valid
        const newIndex = selectedIndex >= images.length - 1 ? Math.max(0, images.length - 2) : selectedIndex;
        setSelectedIndex(newIndex);
      }
    }
  };
  
  // Handle thumbnail clicks - guaranteed to work
  const handleThumbnailClick = (img: ImageItem) => {
    console.log('Thumbnail click handler called with:', img.id);
    
    // Find the image's index
    const imgIndex = images.findIndex(image => image.id === img.id);
    if (imgIndex !== -1) {
      console.log(`Setting selected index to ${imgIndex} for image:`, img.id);
      
      // Force a state update using the functional form
      setSelectedIndex(imgIndex);
      setSelectedId(img.id);

      if(onSelectImage) onSelectImage(batchId, img.id);

      // Ensure panel expanded if it was collapsed
      if(isCollapsed){
        setIsCollapsed(false);
        onCollapseChange?.(batchId, false);
      }
    }
  };
  
  // When images prop updates, make sure the selected ID is still present
  React.useEffect(() => {
    if (!selectedId) return;
    const idx = images.findIndex(img => img.id === selectedId);
    if (idx !== -1) {
      setSelectedIndex(idx);
    } else {
      setSelectedIndex(0);
      setSelectedId(images[0]?.id || '');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [images]);

  // When selectedId changes (e.g., after restoring from storage), update selectedIndex
  React.useEffect(() => {
    if (!selectedId) return;
    const idx = images.findIndex(img => img.id === selectedId);
    if (idx !== -1 && idx !== selectedIndex) {
      setSelectedIndex(idx);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);
  
  // Format batch title with the prompt + counter
  const batchTitle = React.useMemo(() => {
    const prompt = images[0]?.promptKey || 'No prompt';
    const idx = images.findIndex(img => img.id === selectedId);
    return `${prompt} (${idx + 1}/${images.length})`;
  }, [images, selectedId]);
  
  // No headerExtras – count now inside label, refresh hidden for cleaner header
  const headerExtras = null;
  
  // Custom header start (left side)
  const headerStart = (
    <div className="drag-handle cursor-grab mr-2 shrink-0">
      <GripVertical className="h-4 w-4" />
    </div>
  );
  
  // Context menu items for the panel header
  const contextMenuItems = React.useMemo(() => [
    {
      label: 'Delete Batch',
      onClick: () => {
        if (onDeleteBatch) {
          onDeleteBatch(batchId);
        }
      },
      icon: <Trash2 className="h-4 w-4" />,
      variant: 'destructive' as const,
    },
  ], [onDeleteBatch, batchId]);
  
  const [touchStart, setTouchStart] = useState<number | null>(null);
  const [touchEnd, setTouchEnd] = useState<number | null>(null);
  const [isMobile, setIsMobile] = useState(false);

  // Detect mobile devices
  React.useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth <= 768);
    };
    
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Handle navigation
  const handlePrev = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (selectedIndex > 0) {
      setSelectedIndex(selectedIndex - 1);
    }
  };

  const handleNext = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (selectedIndex < images.length - 1) {
      setSelectedIndex(selectedIndex + 1);
    }
  };

  // Persist selectedId across unmounts/polls using localStorage
  React.useEffect(() => {
    const stored = localStorage.getItem(`recent_selected_${batchId}`);
    if (stored) {
      setSelectedId(stored);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => {
    if (selectedId) {
      localStorage.setItem(`recent_selected_${batchId}`, selectedId);
    }
  }, [selectedId, batchId]);

  // Keep selectedId in sync when index changes via arrows or other means
  React.useEffect(() => {
    if (images[selectedIndex] && images[selectedIndex].id !== selectedId) {
      setSelectedId(images[selectedIndex].id);

      if(onSelectImage) onSelectImage(batchId, images[selectedIndex].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndex, images]);

  // If parent forces a selection (e.g., new image), apply it
  React.useEffect(() => {
    if (forceSelectId && forceSelectId !== selectedId) {
      const idx = images.findIndex(i => i.id === forceSelectId);
      if (idx !== -1) {
        setSelectedIndex(idx);
        setSelectedId(forceSelectId);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forceSelectId, images]);

  // Collapsed view – show only thumbnails
  if (isCollapsed) {
    return (
      <ExpandableContainer
        id={`batch-${batchId}`}
        iconPos="right"
        variant="panel"
        collapsed={false} /* keep content mounted */
        onToggle={handleToggle}
        label={batchTitle}
        headerStart={headerStart}
        headerExtras={headerExtras}
        showContextMenu={true}
        contextMenuItems={contextMenuItems}
        className={styles.batchPanel}
      >
        <div className={styles.thumbnailStripWrapper}>
          <SortableImageGrid
            images={images.map(img => ({ ...img, isSelected: false }))}
            sortable={false}
            onImageClick={(img) => {
              // Expand and select
              setIsCollapsed(false);
              handleThumbnailClick(img);
            }}
            className={`${styles.thumbnailGrid} recent-thumbnail-grid`}
            disableDefaultGridCols={true}
            onFullscreenClick={(img)=> {
              setIsCollapsed(false);
              handleThumbnailClick(img);
            }}
          />
        </div>
      </ExpandableContainer>
    );
  }
  
  // For expanded view - render selected image + thumbnail strip
  return (
    <ExpandableContainer
      id={`batch-${batchId}`}
      iconPos="right"
      variant="panel"
      collapsed={isCollapsed}
      onToggle={handleToggle}
      label={batchTitle}
      headerStart={headerStart}
      headerExtras={headerExtras}
      showContextMenu={true}
      contextMenuItems={contextMenuItems}
      className={styles.batchPanel}
    >
      <div className={styles.batchContent}>
        {/* Main selected image display */}
        {selectedImage && (
          <div 
            ref={mergedRef}
            {...dragListeners}
            {...dragAttributes}
            className={`${styles.selectedImageContainer} ${isDragging ? styles.dragging : ''} group`}
            style={dragStyle}
          >
            <img 
              src={selectedImage.urlFull || selectedImage.urlThumb} 
              alt={selectedImage.promptKey || 'Selected image'} 
              className={styles.selectedImage}
              onClick={() => onImageClick && onImageClick(selectedImage)}
            />
            <div className={styles.imageCounter}>
              {selectedIndex + 1}/{images.length}
            </div>

            {/* Mobile swipe navigation overlay */}
            {isMobile && (
              <div 
                className="absolute inset-0 z-20"
                onTouchStart={(e) => {
                  const touch = e.touches[0];
                  setTouchStart(touch.clientX);
                }}
                onTouchMove={(e) => {
                  const touch = e.touches[0];
                  setTouchEnd(touch.clientX);
                }}
                onTouchEnd={() => {
                  if (!touchStart || !touchEnd) return;
                  
                  const distance = touchStart - touchEnd;
                  const isLeftSwipe = distance > 50;
                  const isRightSwipe = distance < -50;
                  
                  if (isLeftSwipe && selectedIndex < images.length - 1) {
                    setSelectedIndex(selectedIndex + 1);
                  } else if (isRightSwipe && selectedIndex > 0) {
                    setSelectedIndex(selectedIndex - 1);
                  }
                  
                  setTouchStart(null);
                  setTouchEnd(null);
                }}
              />
            )}

            {/* Navigation arrows - visible on hover for desktop */}
            {!isMobile && (
              <>
                {selectedIndex > 0 && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className={`${styles.navArrow} ${styles.navArrowLeft} h-8 w-8 bg-black/40 hover:bg-black/60 text-white`}
                    onClick={handlePrev}
                  >
                    <ChevronLeft className="h-5 w-5" />
                  </Button>
                )}
                {selectedIndex < images.length - 1 && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className={`${styles.navArrow} ${styles.navArrowRight} h-8 w-8 bg-black/40 hover:bg-black/60 text-white`}
                    onClick={handleNext}
                  >
                    <ChevronRight className="h-5 w-5" />
                  </Button>
                )}
              </>
            )}
            
            {/* Add three-dot menu to selected image - identical to the one in ImageCard */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost" 
                  size="icon" 
                  className="absolute bottom-2 right-2 z-40 h-8 w-8 bg-black/30 hover:bg-black/60 text-white"
                  onClick={(e) => e.stopPropagation()}
                >
                  <MoreVertical className="h-5 w-5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent side="right">
                {/* Use as prompt */}
                {onUseAsPrompt && (
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onUseAsPrompt(selectedImage);
                    }}
                    className="flex items-center"
                  >
                    <Sparkles className="h-4 w-4 mr-2" />
                    Use as prompt
                  </DropdownMenuItem>
                )}
                
                {/* Copy to submenu */}
                {onCopyTo && publishDestinations && publishDestinations.length > 0 && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <DropdownMenuItem
                        onClick={(e) => e.stopPropagation()}
                        className="flex items-center"
                      >
                        <Copy className="h-4 w-4 mr-2" />
                        Copy to...
                      </DropdownMenuItem>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent side="right">
                      {publishDestinations.map(dest => (
                        <DropdownMenuItem
                          key={dest.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            if (onCopyTo) onCopyTo(selectedImage, dest.id);
                          }}
                        >
                          {dest.name}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
                
                {/* Publish to submenu */}
                {onPublish && publishDestinations && publishDestinations.length > 0 && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <DropdownMenuItem
                        onClick={(e) => e.stopPropagation()}
                        className="flex items-center"
                      >
                        <Share className="h-4 w-4 mr-2" />
                        Publish to...
                      </DropdownMenuItem>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent side="right">
                      {publishDestinations
                        .filter(dest => !dest.headless)
                        .map(dest => (
                          <DropdownMenuItem
                            key={dest.id}
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              
                              try {
                                onPublish(selectedImage, dest.id);
                              } catch (error) {
                                console.error("Error publishing image:", error);
                              }
                            }}
                          >
                            {dest.name}
                          </DropdownMenuItem>
                        ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
                
                {/* Full Screen option */}
                {selectedImage.urlFull && (
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation();
                      window.open(selectedImage.urlFull, '_blank');
                    }}
                  >
                    <Maximize2 className="h-4 w-4 mr-2" />
                    Full Screen
                  </DropdownMenuItem>
                )}
                
                {/* Raw URL */}
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
                    window.open(`/output/_recent/${selectedImage.id}`, '_blank');
                  }}
                >
                  <ExternalLink className="h-4 w-4 mr-2" />
                  Raw URL
                </DropdownMenuItem>
                
                {/* Delete option */}
                {onDeleteImage && (
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteImage(selectedImage);
                    }}
                  >
                    <Trash2 className="h-4 w-4 mr-2" />
                    Delete
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
            
            {/* Fullscreen button */}
            {onImageClick && (
              <Button
                variant="ghost" 
                size="icon" 
                className="absolute bottom-2 right-12 z-30 h-8 w-8 bg-black/30 hover:bg-black/60 text-white"
                onClick={(e) => {
                  e.stopPropagation();
                  onImageClick(selectedImage);
                }}
              >
                <Maximize2 className="h-5 w-5" />
              </Button>
            )}
          </div>
        )}
        
        {/* Thumbnail strip */}
        <div 
          className={styles.thumbnailStripWrapper}
          onClick={(e) => {
            // Try to find the closest image element
            const target = e.target as HTMLElement;
            const imgEl = target.closest('img') as HTMLImageElement;
            if (imgEl && imgEl.alt) {
              // Use the alt text which contains the prompt key to find the image
              const imgIndex = images.findIndex(img => img.promptKey === imgEl.alt);
              if (imgIndex !== -1) {
                console.log('Direct click handler activated for:', imgEl.alt);
                setSelectedIndex(imgIndex);
              }
            }
          }}
        >
          <SortableImageGrid
            images={[
              ...images.map(img => ({
                ...img,
                isSelected: img.id === selectedId
              })),
              ...(onGenerateAgain ? [{
                id: `generate-again-${batchId}-${Date.now()}`,
                urlThumb: '',
                urlFull: '',
                promptKey: 'Generate Again',
                seed: 0,
                createdAt: new Date().toISOString(),
                isFavourite: false,
                customComponent: (
                  <button 
                    className={styles.generateAgainCard}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (onGenerateAgain) onGenerateAgain(batchId);
                    }}
                    onContextMenu={(e) => e.preventDefault()}
                  >
                    <div className={styles.generateAgainInner}>
                      <RefreshCw className={styles.generateAgainIcon} />
                      <span className={styles.generateAgainText}>Generate Again</span>
                      <span className={styles.generateAgainTextMobile}>Another</span>
                    </div>
                  </button>
                ),
                metadata: {},
                mediaType: 'image' as const
              }] : [])
            ]}
            sortable={false}
            onImageClick={(img) => {
              // Ensure generate again button is excluded from selection
              if (!img.id.startsWith('generate-again-')) {
                handleThumbnailClick(img);
              }
            }}
            onDelete={handleDelete}
            onCopyTo={onCopyTo}
            onPublish={onPublish}
            onUseAsPrompt={onUseAsPrompt}
            publishDestinations={publishDestinations}
            className={`${styles.thumbnailGrid} recent-thumbnail-grid`}
            disableDefaultGridCols={true}
            onFullscreenClick={(img)=> onImageClick && onImageClick(img)}
          />
        </div>
      </div>
    </ExpandableContainer>
  );
};

export default RecentBatchPanel; 
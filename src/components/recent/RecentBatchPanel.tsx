import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
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
import { createPortal } from 'react-dom';

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
  // COMPLETE ISOLATION: Each panel manages its own state, ignores parent after init
  
  // Refs to maintain component state
  const initializedRef = useRef(false);
  const isMountedRef = useRef(true);
  const isUserSelecting = useRef(false);
  
  // Cleanup on unmount
  useEffect(() => {
    isMountedRef.current = true;
    return () => { 
      isMountedRef.current = false; 
    };
  }, []);
  
  // Local selection state - completely independent
  const [selectedIndex, setSelectedIndex] = useState<number>(
    initialSelectedIndex < images.length ? initialSelectedIndex : 0
  );
  const [selectedId, setSelectedId] = useState<string>(
    images[initialSelectedIndex]?.id || images[0]?.id || ''
  );
  
  // Collapsed state
  const [isCollapsed, setIsCollapsed] = useState<boolean>(initialCollapsed);
  
  // First-time initialization only (happens once)
  useEffect(() => {
    if (initializedRef.current) return;
    
    // Try initial ID from props first
    if (forceSelectId && images.some(img => img.id === forceSelectId)) {
      const idx = images.findIndex(img => img.id === forceSelectId);
      if (idx !== -1) {
        setSelectedIndex(idx);
        setSelectedId(forceSelectId);
      }
    } else {
      // Try localStorage if no valid forceSelectId
      try {
        const cacheKey = `recent_selected_${batchId}`;
        const storedId = localStorage.getItem(cacheKey);
        
        if (storedId && images.some(img => img.id === storedId)) {
          const idx = images.findIndex(img => img.id === storedId);
          setSelectedIndex(idx);
          setSelectedId(storedId);
        } else if (images.length > 0) {
          // Default to first image
          localStorage.setItem(cacheKey, images[0].id);
        }
      } catch (e) {
        // Ignore localStorage errors
      }
    }
    
    // Mark as initialized - never accept external updates again
    initializedRef.current = true;
  }, [forceSelectId, images, batchId]);
  
  // Handle thumbnail selection - PURE LOCAL STATE MANAGEMENT
  const handleThumbnailClick = (img: ImageItem) => {
    if (img.id === selectedId || !isMountedRef.current) return;
    
    isUserSelecting.current = true;
    
    const idx = images.findIndex(i => i.id === img.id);
    if (idx !== -1) {
      // Update local state
      setSelectedIndex(idx);
      setSelectedId(img.id);
      
      // Save to localStorage
      try {
        localStorage.setItem(`recent_selected_${batchId}`, img.id);
      } catch (e) {
        // Ignore localStorage errors
      }
      
      // Notify parent for coordination only
      if (onSelectImage) {
        onSelectImage(batchId, img.id);
      }
      
      // Expand if needed
      if (isCollapsed) {
        setIsCollapsed(false);
        onCollapseChange?.(batchId, false);
      }
    }
    
    // Reset after a timeout
    setTimeout(() => {
      if (isMountedRef.current) {
        isUserSelecting.current = false;
      }
    }, 300);
  };
  
  // Navigation handlers with the same pattern
  const handlePrev = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (selectedIndex > 0 && isMountedRef.current) {
      const newIndex = selectedIndex - 1;
      const newId = images[newIndex].id;
      
      // Use same pattern as click handler
      isUserSelecting.current = true;
      setSelectedIndex(newIndex);
      setSelectedId(newId);
      
      if (onSelectImage) {
        onSelectImage(batchId, newId);
      }
      
      try {
        localStorage.setItem(`recent_selected_${batchId}`, newId);
      } catch (e) {
        // Ignore localStorage errors
      }
      
      setTimeout(() => {
        if (isMountedRef.current) {
          isUserSelecting.current = false;
        }
      }, 300);
    }
  };
  
  const handleNext = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (selectedIndex < images.length - 1 && isMountedRef.current) {
      const newIndex = selectedIndex + 1;
      const newId = images[newIndex].id;
      
      // Use same pattern as click handler
      isUserSelecting.current = true;
      setSelectedIndex(newIndex);
      setSelectedId(newId);
      
      if (onSelectImage) {
        onSelectImage(batchId, newId);
      }
      
      try {
        localStorage.setItem(`recent_selected_${batchId}`, newId);
      } catch (e) {
        // Ignore localStorage errors
      }
      
      setTimeout(() => {
        if (isMountedRef.current) {
          isUserSelecting.current = false;
        }
      }, 300);
    }
  };
  
  // Selected image
  const selectedImage = useMemo(() => {
    const byId = images.find(img => img.id === selectedId);
    return byId || images[selectedIndex] || images[0];
  }, [images, selectedId, selectedIndex]);
  
  // Handle touch events for mobile swipes
  const [touchStart, setTouchStart] = useState<number | null>(null);
  const [touchEnd, setTouchEnd] = useState<number | null>(null);
  const [isMobile, setIsMobile] = useState(false);
  
  // Mobile detection
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth <= 768);
    };
    
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);
  
  // Handle swipe gestures
  const handleTouchEnd = () => {
    if (!touchStart || !touchEnd) return;
    
    const distance = touchStart - touchEnd;
    const isLeftSwipe = distance > 50;
    const isRightSwipe = distance < -50;
    
    if (isLeftSwipe && selectedIndex < images.length - 1) {
      handleNext({ stopPropagation: () => {} } as React.MouseEvent);
    } else if (isRightSwipe && selectedIndex > 0) {
      handlePrev({ stopPropagation: () => {} } as React.MouseEvent);
    }
    
    setTouchStart(null);
    setTouchEnd(null);
  };
  
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
  
  // Header prompt (truncated if long) and separate counter element
  const headerPrompt = useMemo(() => {
    return images[0]?.promptKey || 'No prompt';
  }, [images]);

  const headerCounter = useMemo(() => {
    return `(${selectedIndex + 1}/${images.length})`;
  }, [selectedIndex, images.length]);

  // Build label as prompt (truncated) plus counter – counter should never truncate.
  const headerLabel = (
    <span className="flex items-center min-w-0 max-w-full">
      <span className="truncate min-w-0" style={{ minWidth: 0 }}>{headerPrompt}</span>
      <span className="ml-1 flex-none text-xs text-muted-foreground">{headerCounter}</span>
    </span>
  );

  const headerExtras = null;
  
  // Custom header start (left side)
  const headerStart = (
    <div className="drag-handle cursor-grab mr-2 shrink-0">
      <GripVertical className="h-4 w-4" />
    </div>
  );
  
  // Context menu items for the panel header
  const contextMenuItems = useMemo(() => [
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
  
  // Log selection changes for debugging
  useEffect(() => {
    console.log(`[RecentBatch] ${batchId} selection changed: index=${selectedIndex}, id=${selectedId}`);
  }, [selectedIndex, selectedId, batchId]);

  // Log collapse state changes
  useEffect(() => {
    console.log(`[RecentBatch] ${batchId} collapse state:`, isCollapsed);
  }, [isCollapsed, batchId]);
  
  // Collapsed view – show only thumbnails
  if (isCollapsed) {
    return (
      <ExpandableContainer
        id={`batch-${batchId}`}
        iconPos="right"
        variant="panel"
        collapsed={false}
        onToggle={handleToggle}
        label={headerLabel}
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
      label={headerLabel}
      headerStart={headerStart}
      headerExtras={headerExtras}
      showContextMenu={true}
      contextMenuItems={contextMenuItems}
      className={styles.batchPanel}
    >
      <div className={styles.batchContent}>
        {/* Main selected image display */}
        {selectedImage && (
          <>
            <div 
              ref={mergedRef}
              {...dragListeners}
              {...dragAttributes}
              className={`${styles.selectedImageContainer} ${isDragging ? styles.dragging : ''} group`}
              style={{ opacity: isDragging ? 0.4 : 1, cursor: isDragging ? 'grabbing' : 'grab' }}
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
                  onTouchEnd={handleTouchEnd}
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
          </>
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
              if (imgIndex !== -1 && images[imgIndex]) {
                console.log('Direct click handler activated for:', imgEl.alt);
                handleThumbnailClick(images[imgIndex]);
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

// Update the memo comparison function to be even stricter
const MemoizedRecentBatchPanel = React.memo(RecentBatchPanel, (prevProps, nextProps) => {
  // ONLY re-render for these specific changes:
  
  // 1. If a complete different batch (batch ID changed)
  if (prevProps.batchId !== nextProps.batchId) return false;
  
  // 2. If the image collection has fundamentally changed
  if (prevProps.images.length !== nextProps.images.length) return false;
  
  // 3. If collapsed state changed
  if (prevProps.initialCollapsed !== nextProps.initialCollapsed) return false;
  
  // STABILIZE: Don't re-render for these changes:
  
  // Ignore changes to forceSelectId - panel manages its own selection
  // Ignore changes to various handler functions - they don't affect rendering
  // Ignore publishDestinations changes unless they're actually used
  
  // Check if the actual image data has changed (comparing by id)
  const imagesChanged = !prevProps.images.every((prevImg, idx) => 
    prevImg.id === nextProps.images[idx]?.id
  );
  
  // Now the key part - only allow re-renders when images actually change
  return !imagesChanged;
});

// Export the memoized version as default
export default MemoizedRecentBatchPanel; 
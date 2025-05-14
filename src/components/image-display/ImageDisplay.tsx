import React, { useState, useEffect, useMemo, useRef, useCallback, memo } from 'react';
import FullscreenDialog from './FullscreenDialog';
import ViewModeContent from './ViewModeContent';
import useImageDisplayState from './hooks/useImageDisplayState';
import apiService from '@/utils/api';
import { PublishDestination } from '@/utils/api';
import * as LucideIcons from 'lucide-react';
import { BucketGridView } from './BucketGridView';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import ViewModeSelector from './ViewModeSelector';
import { CirclePause, CirclePlay, CircleStop, Settings, Image, ImagePlus, ChevronRight, ChevronLeft, Copy, Send, Share, ClockIcon } from 'lucide-react';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';
import {
  useDroppable,
  DragEndEvent,
  DragStartEvent,
  useDndMonitor,
} from '@dnd-kit/core';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { DROP_ZONES } from '@/dnd/dropZones';
import useFullscreen from './hooks/useFullscreen';
import RecentView from '@/components/recent/RecentView';

export type ViewMode = 'normal' | 'small' | 'table';
export type SortField = 'index' | 'prompt' | 'batchSize' | 'timestamp';
export type SortDirection = 'asc' | 'desc';

// Define a type for bucket images
export interface BucketImage {
  id: string;
  url: string;
  prompt?: string;
  timestamp?: string;
  metadata?: Record<string, any>;
}

// Our original ImageDisplay props
interface OriginalImageDisplayProps {
  imageUrl: string | null;
  currentPrompt: string | null;
  isLoading: boolean;
  destinations: string[];
  onFullscreen?: () => void;
  isFullscreen?: boolean;
}

// Props being passed from Index.tsx
interface IndexImageDisplayProps {
  imageUrl: string | null;
  prompt?: string | null;
  isLoading: boolean;
  uploadedImages?: string[];
  generatedImages?: any[];
  imageContainerOrder?: string[];
  expandedContainers?: Record<string, boolean>;
  setExpandedContainers?: (containers: Record<string, boolean>) => void;
  workflow?: string;
  generationParams?: Record<string, any>;
  onUseGeneratedAsInput?: (url: string) => void;
  onCreateAgain?: (batchId?: string) => void;
  onReorderContainers?: (sourceIndex: number, destinationIndex: number) => void;
  onDeleteImage?: (batchId: string, index: number) => void;
  onDeleteContainer?: (batchId: string) => void;
  fullscreenRefreshTrigger?: number;
  publishDestinations?: string[];
}

// Combined props type using a discriminated union
type ImageDisplayProps = OriginalImageDisplayProps | IndexImageDisplayProps;

// Define interface for the destination tabs
interface DestinationTab {
  id: string;
  label: string;
  icon: React.ReactNode;
  highlight: boolean;
  file: string | null;
  headless?: boolean;
}

export function ImageDisplay(props: ImageDisplayProps) {
  const { destinationsWithBuckets, loading: destinationsLoading } = usePublishDestinations();
  const [selectedTab, setSelectedTab] = useState<string>('generated');
  const [bucketRefreshFlags, setBucketRefreshFlags] = useState<Record<string, number>>({});
  const [viewMode, setViewMode] = useState<ViewMode>('normal');
  const [sortField, setSortField] = useState<SortField>('timestamp');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');
  const [schedulerStatuses, setSchedulerStatuses] = useState<Record<string, any>>({});
  const [draggedImageData, setDraggedImageData] = useState<any>(null);
  const [dropActionDestId, setDropActionDestId] = useState<string | null>(null);
  const [dropActionImageId, setDropActionImageId] = useState<string | null>(null);
  
  // ---------------------------------------------------------------------------
  // Fullscreen state management ----------------------------------------------
  // ---------------------------------------------------------------------------
  // Build a flat list of all generated images (completed/failed/error/generating)
  const allImagesFlat = useMemo(() => {
    if (!('generatedImages' in props) || !props.generatedImages) return [] as any[];
    return (props.generatedImages as any[])
      .filter(img => ['completed', 'failed', 'error', 'generating'].includes(img.status))
      .map(img => ({
        url: img.url,
        prompt: img.prompt,
        batchId: img.batchId,
        batchIndex: img.batchIndex,
        workflow: img.workflow,
        timestamp: img.timestamp,
        referenceImageUrl: img.referenceImageUrl,
        params: img.params,
        refiner: img.refiner,
        title: img.title
      }));
  }, [props]);

  // Use the common fullscreen hook
  const {
    showFullScreenView,
    setShowFullScreenView,
    fullScreenBatchId,
    fullScreenImageIndex,
    setFullScreenImageIndex,
    currentGlobalIndex,
    openFullScreenView,
    handleNavigateGlobal,
    handleNavigateWithBatchAwareness
  } = useFullscreen(allImagesFlat);

  // Function to trigger a refresh for a specific bucket
  const refreshBucket = async (bucket: string) => {
    // Increment the refresh flag to trigger a re-render of the BucketGridView
    setBucketRefreshFlags(prev => ({
      ...prev,
      [bucket]: (prev[bucket] || 0) + 1
    }));
    
    // Also immediately fetch the latest scheduler status for this bucket
    try {
      const status = await apiService.getSchedulerStatus(bucket);
      // Update just this one scheduler status without affecting others
      setSchedulerStatuses(prev => ({
        ...prev,
        [bucket]: status
      }));
      console.log('[poll] scheduler status updated at', new Date().toLocaleTimeString());
    } catch (error) {
      console.error(`Error fetching scheduler status for ${bucket}:`, error);
    }
  };

  // after definition of refreshBucket function wrap in useCallback for stability
  const stableRefreshBucket = useCallback(refreshBucket, []);

  const handleImageClick = (image: any) => {
    console.log('Image clicked:', image);
    if (image && image.batchId !== undefined && image.batchIndex !== undefined) {
      openFullScreenView(image.batchId, image.batchIndex);
    }
  };

  // Determine if we're receiving props from Index.tsx or our original props
  const isIndexProps = 'publishDestinations' in props || 'prompt' in props || 'generatedImages' in props;
  
  // Extract the appropriate props
  const imageUrl = props.imageUrl;
  const currentPrompt = isIndexProps 
    ? (props as IndexImageDisplayProps).prompt || null 
    : (props as OriginalImageDisplayProps).currentPrompt;
  const isLoading = props.isLoading;
  const destinations = isIndexProps 
    ? (props as IndexImageDisplayProps).publishDestinations || [] 
    : (props as OriginalImageDisplayProps).destinations;
  const onFullscreen = isIndexProps 
    ? undefined 
    : (props as OriginalImageDisplayProps).onFullscreen;
  const isFullscreen = isIndexProps 
    ? false 
    : (props as OriginalImageDisplayProps).isFullscreen;

  // Index.tsx specific props
  const generatedImages = isIndexProps ? (props as IndexImageDisplayProps).generatedImages || [] : [];
  const imageContainerOrder = isIndexProps ? (props as IndexImageDisplayProps).imageContainerOrder || [] : [];
  const expandedContainers = isIndexProps ? (props as IndexImageDisplayProps).expandedContainers || {} : {};
  const setExpandedContainers = isIndexProps ? (props as IndexImageDisplayProps).setExpandedContainers : undefined;
  const onUseGeneratedAsInput = isIndexProps ? (props as IndexImageDisplayProps).onUseGeneratedAsInput : undefined;
  const onCreateAgain = isIndexProps ? (props as IndexImageDisplayProps).onCreateAgain : undefined;
  const onDeleteImage = isIndexProps ? (props as IndexImageDisplayProps).onDeleteImage : undefined;
  const onDeleteContainer = isIndexProps ? (props as IndexImageDisplayProps).onDeleteContainer : undefined;
  const onReorderContainers = isIndexProps ? (props as IndexImageDisplayProps).onReorderContainers : undefined;

  // Use destinationsWithBuckets instead of publishDestinations
  const allDestinations = destinationsWithBuckets;

  // Convert array of destinations to tabs format
  const destinationTabs = useMemo(() => {
    return [
      { 
        id: 'generated', 
        label: 'Generated',
        icon: <ImagePlus className="h-4 w-4 mr-2" />,
        highlight: true,
        file: null
      } as DestinationTab,
      // Always add the Recent tab
      {
        id: 'recent', 
        label: 'Recent',
        icon: <ClockIcon className="h-4 w-4 mr-2" />,
        highlight: false,
        file: '_recent',
        headless: true
      } as DestinationTab,
      ...destinationsWithBuckets.map(dest => ({
        id: dest.id,
        label: dest.name,
        icon: <Image className="h-4 w-4 mr-2" />,
        highlight: false,
        file: dest.file || dest.id,
        headless: dest.headless || false
      }))
    ];
  }, [destinationsWithBuckets]);

  // Find file property for a destination ID
  const getDestinationFile = (destinationId: string) => {
    const destTab = destinationTabs.find(tab => tab.id === destinationId);
    if (!destTab || destinationId === 'generated') return destinationId;
    return destTab.file;
  };

  // Get scheduler status for all destinations
  useEffect(() => {
    let isMounted = true;
    const fetchSchedulerStatuses = async () => {
      if (!isMounted) return;
      console.log('[poll] fetchSchedulerStatuses triggered at', new Date().toLocaleTimeString());
      try {
        const statuses = await Promise.all(
          destinationsWithBuckets.map(async (dest) => {
            const status = await apiService.getSchedulerStatus(dest.id);
            return { [dest.id]: status };
          })
        );
        if (isMounted) {
          setSchedulerStatuses(Object.assign({}, ...statuses));
        }
      } catch (error) {
        console.error('Error fetching scheduler statuses:', error);
      }
    };

    if (destinationsWithBuckets.length > 0) {
      // Initial fetch
      fetchSchedulerStatuses();
      
      // Set up polling interval (every 15 seconds)
      const intervalId = setInterval(fetchSchedulerStatuses, 15000);
      
      // Cleanup interval and mounted flag on unmount or when destinations change
      return () => {
        isMounted = false;
        clearInterval(intervalId);
      };
    }
  }, [destinationsWithBuckets]);

  // Get status for a destination to be passed to BucketGridView
  const getStatusForDestination = (destId: string) => {
    return schedulerStatuses[destId] || { is_running: false, is_paused: false };
  };

  // Scroll logic for tabs
  const tabsRef = useRef<HTMLDivElement>(null);
  const [showLeftScroll, setShowLeftScroll] = useState(false);
  const [showRightScroll, setShowRightScroll] = useState(false);

  // Check if we need scroll indicators
  const checkScroll = () => {
    if (!tabsRef.current) return;
    
    const { scrollLeft, scrollWidth, clientWidth } = tabsRef.current;
    
    // Show left scroll indicator if scrolled at least 10px
    setShowLeftScroll(scrollLeft > 10);
    
    // Show right scroll indicator if there's at least 10px more to scroll
    // This threshold helps with fractional pixels and rounding errors
    setShowRightScroll(scrollLeft + clientWidth < scrollWidth - 10);
    
    // Force a reflow to ensure the UI updates correctly
    // This helps with rendering issues in some browsers
    if (tabsRef.current.offsetHeight) {
      // This access to offsetHeight forces a reflow
    }
  };

  // Handle scrolling tabs
  const scrollTabs = (direction: 'left' | 'right') => {
    if (!tabsRef.current) return;
    
    // Calculate a better scroll amount based on container width
    const containerWidth = tabsRef.current.clientWidth;
    const scrollAmount = Math.max(100, containerWidth * 0.6); // 60% of visible width or at least 100px
    
    const currentScroll = tabsRef.current.scrollLeft;
    tabsRef.current.scrollTo({
      left: direction === 'left' ? currentScroll - scrollAmount : currentScroll + scrollAmount,
      behavior: 'smooth'
    });
    
    // Update scroll indicators after scrolling
    setTimeout(checkScroll, 400); // Check after animation completes
  };

  // Check scroll on window resize and tab changes
  useEffect(() => {
    checkScroll();
    const handleResize = () => checkScroll();
    window.addEventListener('resize', handleResize);
    window.addEventListener('orientationchange', handleResize);
    
    // Force an additional check after the component has fully rendered
    setTimeout(checkScroll, 100);
    
    return () => {
      window.removeEventListener('resize', handleResize);
      window.removeEventListener('orientationchange', handleResize);
    };
  }, [destinationTabs, selectedTab]);
  
  // Scroll selected tab into view when it changes
  useEffect(() => {
    if (tabsRef.current) {
      const selectedTabElement = tabsRef.current.querySelector(`button[data-tab-id="${selectedTab}"]`) as HTMLButtonElement;
      if (selectedTabElement) {
        // Calculate the center position to scroll to
        const containerWidth = tabsRef.current.clientWidth;
        const tabWidth = selectedTabElement.clientWidth;
        const tabLeft = selectedTabElement.offsetLeft;
  
        // Scroll to center the tab
        tabsRef.current.scrollTo({
          left: tabLeft - (containerWidth / 2) + (tabWidth / 2),
          behavior: 'smooth'
        });
      }
    }
  }, [selectedTab]);
  
  // Function to toggle expansion of a batch container
  const handleToggleExpand = (batchId: string) => {
    if (setExpandedContainers) {
      setExpandedContainers({
        ...expandedContainers,
        [batchId]: !expandedContainers[batchId]
      });
    }
  };

  // Get all images for small grid view
  const getAllImages = () => {
    // Flatten all images from all batches
    if (!isIndexProps || !generatedImages) return [];
    return generatedImages;
  };

  // Handle image click in small grid view
  const handleSmallImageClick = (image: any) => {
    console.log('Small image clicked:', image);
  };

  // Sort handling
  const handleSortClick = (field: SortField) => {
    if (field === sortField) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
  };

  // Get sorted container IDs
  const getSortedContainers = () => {
    return [...imageContainerOrder]; // Just use the order provided by parent for now
  };

  // Handle table row click
  const handleTableRowClick = (batchId: string) => {
    if (setExpandedContainers) {
      setExpandedContainers({
        ...expandedContainers,
        [batchId]: !expandedContainers[batchId]
      });
    }
  };
  
  // Organize batches by batch ID
  const batches: Record<string, any[]> = {};
  if (isIndexProps && generatedImages) {
    generatedImages.forEach(image => {
      if (image.batchId) {
        if (!batches[image.batchId]) {
          batches[image.batchId] = [];
        }
        batches[image.batchId].push(image);
      }
    });
  }

  // Set tab and scroll it into view
  const handleTabClick = (tabId: string) => {
    setSelectedTab(tabId);
    
    // Find the selected tab element and scroll it into view
    setTimeout(() => {
      if (tabsRef.current) {
        const selectedTabElement = tabsRef.current.querySelector(`button[data-tab-id="${tabId}"]`) as HTMLButtonElement;
        if (selectedTabElement) {
          // Calculate the center position to scroll to
          const containerWidth = tabsRef.current.clientWidth;
          const tabWidth = selectedTabElement.clientWidth;
          const tabLeft = selectedTabElement.offsetLeft;
          
          // Scroll to center the tab
          tabsRef.current.scrollTo({
            left: tabLeft - (containerWidth / 2) + (tabWidth / 2),
            behavior: 'smooth'
          });
    }
      }
    }, 10);
  };

  const handleDragStart = () => {};

  // Context menu state
  type MenuAction = 'copy' | 'move' | 'publish';
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    destId: string;
    imageId: string;
    isHeadless: boolean;
  } | null>(null);

  // Close context menu on outside click
  useEffect(() => {
    if (!contextMenu) return;
    
    const handleClickOutside = () => setContextMenu(null);
    window.addEventListener('click', handleClickOutside);
    return () => window.removeEventListener('click', handleClickOutside);
  }, [contextMenu]);

  // Ref to track mouse position
  const mousePositionRef = useRef({ x: 0, y: 0 });
  
  // Track mouse and touch position
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      mousePositionRef.current = { x: e.clientX, y: e.clientY };
    };
    const handleTouchMove = (e: TouchEvent) => {
      if (e.touches.length > 0) {
        const t = e.touches[0];
        mousePositionRef.current = { x: t.clientX, y: t.clientY };
      }
    };
    
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('touchmove', handleTouchMove);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('touchmove', handleTouchMove);
    };
  }, []);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;

    // Strip prefix if dragging selected image
    let activeId = active.id as string;
    if (typeof activeId === 'string' && activeId.startsWith('selected:')) {
      activeId = activeId.slice('selected:'.length);
    }

    // Tab drop handling – IDs prefixed with DROP_ZONES.TAB_PREFIX
    if (typeof over.id === 'string' && (over.id as string).startsWith(DROP_ZONES.TAB_PREFIX)) {
      const destId = (over.id as string).slice(DROP_ZONES.TAB_PREFIX.length);
      if (destId !== selectedTab) {
        // Find if the destination is headless
        const destInfo = destinationsWithBuckets.find(d => d.id === destId);
        const isHeadless = destInfo?.headless || false;
        
        // Use the current mouse position that we've been tracking
        const { x, y } = mousePositionRef.current;
        
        // Show context menu (prevent event bubbling that would close it immediately)
        setTimeout(() => {
          setContextMenu({
            x,
            y,
            destId,
            imageId: activeId,
            isHeadless
          });
        }, 0);
      }
    }
  };

  // Handle menu action selection
  const handleMenuAction = (action: MenuAction) => {
    if (!contextMenu) return;
    
    const { destId, imageId, isHeadless } = contextMenu;
    
    if (action === 'copy') {
      console.log(`Copying image ${imageId} from ${selectedTab} to ${destId}`);
      apiService.copyImageToBucket(getDestinationFile(selectedTab), destId, imageId, true)
        .then(() => {
          toast.success(`Copied to ${destId}`);
          stableRefreshBucket(destId);
        })
        .catch((err) => {
          console.error('Copy failed:', err);
          toast.error('Copy failed');
        });
    } else if (action === 'move') {
      console.log(`Moving image ${imageId} from ${selectedTab} to ${destId}`);
      apiService.copyImageToBucket(getDestinationFile(selectedTab), destId, imageId, false)
        .then(() => {
          toast.success(`Moved to ${destId}`);
          stableRefreshBucket(destId);
          // Also refresh the source bucket since the file should be removed
          stableRefreshBucket(getDestinationFile(selectedTab));
        })
        .catch((err) => {
          console.error('Move failed:', err);
          toast.error('Move failed');
        });
    } else if (action === 'publish' && !isHeadless) {
      console.log(`Publishing image ${imageId} from ${selectedTab} to ${destId}`);
      
      // Determine the appropriate publish method based on source tab
      if (selectedTab !== 'generated') {
        // For bucket-to-bucket publishing (non-generated images)
        apiService.publishImageUnified({
          dest_bucket_id: destId,
          src_bucket_id: getDestinationFile(selectedTab),
          filename: imageId
        })
        .then(() => {
          toast.success('Published successfully');
          stableRefreshBucket(destId);
        })
        .catch((err) => {
          console.error('Publish failed:', err);
          toast.error('Publish failed');
        });
      } else {
        // For generated images, use the full URL with the external source method
        const fullSourceUrl = `${window.location.protocol}//${window.location.host}/api/buckets/${getDestinationFile(selectedTab)}/raw/${imageId}`;
        console.log(`Source URL: ${fullSourceUrl}`);
        
        apiService.publishImageUnified({
          dest_bucket_id: destId,
          source_url: fullSourceUrl,
          metadata: {}, // Add metadata if available
          skip_bucket: false
        })
        .then(() => {
          toast.success('Published successfully');
          stableRefreshBucket(destId);
        })
        .catch((err) => {
          console.error('Publish failed:', err);
          toast.error('Publish failed');
        });
      }
    }
    
    setContextMenu(null);
  };

  // Listen for global DnD events from the root context
  useDndMonitor({
    onDragStart: handleDragStart,
    onDragEnd: handleDragEnd,
  });

  // Helper to get current bucket id when BucketGridView is active
  const currentBucketId = selectedTab && selectedTab !== 'generated' ? getDestinationFile(selectedTab) : '';

  // ---------- Droppable Tab Button ---------- //
  const DroppableTabButton: React.FC<{ tab: DestinationTab }> = ({ tab }) => {
    // Only make non-generated and non-recent tabs droppable
    const isDroppable = tab.id !== 'generated' && tab.id !== 'recent';
    
    // Use a dummy ref for non-droppable tabs
    const dummyRef = useRef<HTMLButtonElement>(null);
    const { setNodeRef, isOver } = isDroppable 
      ? useDroppable({ id: `${DROP_ZONES.TAB_PREFIX}${tab.id}` })
      : { setNodeRef: dummyRef, isOver: false };

    return (
      <button
        ref={setNodeRef as React.RefObject<HTMLButtonElement>}
        key={tab.id}
        data-tab-id={tab.id}
        onClick={() => handleTabClick(tab.id)}
        className={`px-2 py-1.5 text-xs sm:text-sm sm:px-3 sm:py-2 inline-flex items-center rounded-md transition-colors
          relative
          ${isDroppable && isOver ? 'ring-2 ring-primary' : ''}
          ${tab.highlight && selectedTab === tab.id 
            ? 'bg-primary text-primary-foreground' 
            : selectedTab === tab.id
              ? 'text-primary border-b-2 border-primary'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
          }`}
      >
        {/* Extended drop area that gives more space for drop detection */}
        {isDroppable && (
          <div className="absolute inset-0 -right-4 -left-4" style={{ zIndex: -1 }} />
        )}
        {tab.icon}
        <span className="truncate max-w-[60px] sm:max-w-[100px]">{tab.label}</span>
      </button>
    );
  };

  const refreshRecent = useCallback(() => {
    stableRefreshBucket('_recent');
  }, [stableRefreshBucket]);

  // Define memoized component after refreshRecent definition but before return:
  const MemoRecentView = useMemo(() => memo(RecentView), []);

  return (
    <div className="bg-background h-full overflow-y-auto">
      {/* Tabs for switching between Generated view and Destinations - frameless design */}
      <div className="border-b w-full sticky top-0 z-50 bg-background">
        <div className="relative grid grid-cols-1">
          {/* Scroll indicators */}
          <div className="absolute inset-y-0 left-0 z-10 flex items-center pointer-events-none">
            {showLeftScroll && (
              <button 
                onClick={() => scrollTabs('left')}
                className="bg-background/80 h-full px-1 flex items-center justify-center hover:bg-background/90 pointer-events-auto"
                aria-label="Scroll tabs left"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
            )}
          </div>
          
          <div className="absolute inset-y-0 right-0 z-10 flex items-center pointer-events-none">
            {showRightScroll && (
              <button 
                onClick={() => scrollTabs('right')}
                className="bg-background/80 h-full px-1 flex items-center justify-center hover:bg-background/90 pointer-events-auto"
                aria-label="Scroll tabs right"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            )}
          </div>
          
          {/* Scrollable tabs */}
          <div 
            ref={tabsRef}
            onScroll={checkScroll}
            className="w-full overflow-x-auto scrollbar-hide px-6"
          >
            <div className="inline-flex items-center space-x-1 p-1">
              {destinationTabs.map(tab => (
                <DroppableTabButton key={tab.id} tab={tab} />
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Content area */}
      <div className="p-4">
        {selectedTab === 'generated' ? (
          isLoading ? (
            <div className="flex items-center justify-center h-full">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
            </div>
          ) : isIndexProps && imageContainerOrder && imageContainerOrder.length > 0 ? (
            <>
              {/* View Mode Selector */}
              <div className="flex justify-end mb-4">
                <ViewModeSelector 
                  viewMode={viewMode}
                  onViewModeChange={(value) => setViewMode(value as ViewMode)}
                />
              </div>
            
              {/* Render appropriate content for Index.tsx props */}
              <ViewModeContent
                viewMode={viewMode}
                imageContainerOrder={imageContainerOrder}
                batches={batches}
                expandedContainers={expandedContainers}
                handleToggleExpand={handleToggleExpand}
                onUseGeneratedAsInput={onUseGeneratedAsInput || (() => {})}
                onCreateAgain={onCreateAgain || (() => {})}
                onDeleteImage={onDeleteImage || (() => {})}
                onDeleteContainer={onDeleteContainer || (() => {})}
                onFullScreenClick={handleImageClick}
                imageUrl={imageUrl}
                getAllImages={getAllImages}
                handleSmallImageClick={handleSmallImageClick}
                sortField={sortField}
                sortDirection={sortDirection}
                handleSortClick={handleSortClick}
                getSortedContainers={getSortedContainers}
                handleTableRowClick={handleTableRowClick}
                isLoading={isLoading}
                onReorderContainers={onReorderContainers || (() => {})}
              />
            </>
          ) : (
            <div className="text-center text-muted-foreground py-10">
              <p>No generated images found. Try generating some!</p>
            </div>
          )
        ) : selectedTab === 'recent' ? (
          /* Render the Recent tab view */
          <MemoRecentView refreshRecent={refreshRecent}/>
        ) : (
          selectedTab && selectedTab !== 'generated' && (
            <BucketGridView
              destination={selectedTab}
              destinationName={destinationTabs.find(tab => tab.id === selectedTab)?.label || selectedTab}
              onImageClick={handleImageClick}
              refreshBucket={stableRefreshBucket}
              isLoading={isLoading}
              schedulerStatus={getStatusForDestination(selectedTab)}
              headless={destinationTabs.find(tab => tab.id === selectedTab)?.headless || false}
              icon={destinationsWithBuckets.find(d => d.id === selectedTab)?.icon || 'image'}
            />
          )
        )}
      </div>
      {/* Context menu */}
      {contextMenu && (
        <div
          className="fixed z-50 min-w-[180px] overflow-hidden rounded-md border bg-background p-1 shadow-md animate-in fade-in-80"
          style={{
            top: `${contextMenu.y}px`,
            left: `${contextMenu.x}px`
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <Button
            variant="ghost"
            className="w-full justify-start px-2 py-1.5 text-sm"
            onClick={(e) => {
              e.stopPropagation();
              handleMenuAction('copy');
            }}
          >
            <Copy className="mr-2 h-4 w-4" />
            Copy to {contextMenu.destId}
          </Button>
          <Button
            variant="ghost"
            className="w-full justify-start px-2 py-1.5 text-sm"
            onClick={(e) => {
              e.stopPropagation();
              handleMenuAction('move');
            }}
          >
            <Send className="mr-2 h-4 w-4" />
            Move to {contextMenu.destId}
          </Button>
          {!contextMenu.isHeadless && (
            <Button
              variant="ghost"
              className="w-full justify-start px-2 py-1.5 text-sm"
              onClick={(e) => {
                e.stopPropagation();
                handleMenuAction('publish');
              }}
            >
              <Share className="mr-2 h-4 w-4" />
              Publish to {contextMenu.destId}
            </Button>
          )}
        </div>
      )}

      {/* Fullscreen dialog */}
      <FullscreenDialog
        showFullScreenView={showFullScreenView}
        setShowFullScreenView={setShowFullScreenView}
        fullScreenBatchId={fullScreenBatchId}
        batches={batches}
        fullScreenImageIndex={fullScreenImageIndex}
        setFullScreenImageIndex={setFullScreenImageIndex}
        onDeleteImage={onDeleteImage || (() => {})}
        onCreateAgain={onCreateAgain || (() => {})}
        onUseGeneratedAsInput={onUseGeneratedAsInput || (() => {})}
        allImagesFlat={allImagesFlat}
        currentGlobalIndex={currentGlobalIndex}
        handleNavigateGlobal={handleNavigateGlobal}
        handleNavigateWithBatchAwareness={handleNavigateWithBatchAwareness}
        fullscreenRefreshTrigger={isIndexProps ? (props as any).fullscreenRefreshTrigger || 0 : 0}
      />
    </div>
  );
}

// For compatibility with import statements expecting a default export
export default ImageDisplay;

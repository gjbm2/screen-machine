import React, { useState, useEffect, useMemo, useRef } from 'react';
import FullscreenDialog from './FullscreenDialog';
import ViewModeContent from './ViewModeContent';
import useImageDisplayState from './hooks/useImageDisplayState';
import { getPublishDestinations } from '@/services/PublishService';
import * as LucideIcons from 'lucide-react';
import { BucketGridView } from './BucketGridView';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import ViewModeSelector from './ViewModeSelector';
import { CirclePause, CirclePlay, CircleStop, Settings, Image, ImagePlus, ChevronRight, ChevronLeft } from 'lucide-react';
import apiService from '@/utils/api';

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
  const [selectedTab, setSelectedTab] = useState<string>('generated');
  const [bucketRefreshFlags, setBucketRefreshFlags] = useState<Record<string, number>>({});
  const [viewMode, setViewMode] = useState<ViewMode>('normal');
  const [sortField, setSortField] = useState<SortField>('timestamp');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');
  
  // Function to trigger a refresh for a specific bucket
  const refreshBucket = (bucket: string) => {
    setBucketRefreshFlags(prev => ({
      ...prev,
      [bucket]: (prev[bucket] || 0) + 1
    }));
  };

  const handleImageClick = (image: any) => {
    // Handle image click, could open a detail view or set it as the main image
    console.log('Image clicked:', image);
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

  // Convert array of destinations to tabs format
  const destinationTabs = useMemo(() => {
    // Get all publish destinations with their display names
    const allDestinations = getPublishDestinations();
    
    return [
      { 
        id: 'generated', 
        label: 'Generated',
        icon: <ImagePlus className="h-4 w-4 mr-2" />,
        highlight: true,
        file: null
      } as DestinationTab,
      ...(destinations ? destinations.map(destId => {
        // Find the destination info to get the display name
        const destInfo = allDestinations.find(d => d.id === destId);
        return { 
          id: destId, 
          label: destInfo ? destInfo.name : destId, // Use display name if found, fall back to ID
          icon: <Image className="h-4 w-4 mr-2" />,
          highlight: false,
          file: destInfo?.file || destId, // Use file property if available, otherwise fall back to ID
          headless: destInfo?.headless || false // Pass through headless property
        } as DestinationTab;
      }) : [])
    ];
  }, [destinations]);

  // Find file property for a destination ID
  const getDestinationFile = (destinationId: string) => {
    const destTab = destinationTabs.find(tab => tab.id === destinationId);
    if (!destTab || destinationId === 'generated') return destinationId;
    return destTab.file;
  };

  // Track scheduler status for each destination
  const [schedulerStatuses, setSchedulerStatuses] = useState<Record<string, {is_running: boolean, is_paused: boolean}>>({});

  // Get scheduler status for all destinations
  useEffect(() => {
    const fetchSchedulerStatuses = async () => {
      if (!destinations || destinations.length === 0) return;
      
      try {
        const statusMap: Record<string, {is_running: boolean, is_paused: boolean}> = {};
        
        await Promise.all(destinations.map(async (destId) => {
          try {
            const status = await apiService.getSchedulerStatus(destId);
            statusMap[destId] = {
              is_running: status.is_running || false,
              is_paused: status.is_paused || false
            };
          } catch (error) {
            console.error(`Failed to get scheduler status for ${destId}:`, error);
            statusMap[destId] = { is_running: false, is_paused: false };
          }
        }));
        
        setSchedulerStatuses(statusMap);
      } catch (error) {
        console.error("Failed to fetch scheduler statuses:", error);
      }
    };
    
    fetchSchedulerStatuses();
    
    // Set up periodic refresh
    const intervalId = setInterval(fetchSchedulerStatuses, 30000); // refresh every 30 seconds
    
    return () => clearInterval(intervalId);
  }, [destinations]);

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
    setShowLeftScroll(scrollLeft > 0);
    setShowRightScroll(scrollLeft + clientWidth < scrollWidth);
  };

  // Handle scrolling tabs
  const scrollTabs = (direction: 'left' | 'right') => {
    if (!tabsRef.current) return;
    const scrollAmount = 200; // px to scroll
    const currentScroll = tabsRef.current.scrollLeft;
    tabsRef.current.scrollTo({
      left: direction === 'left' ? currentScroll - scrollAmount : currentScroll + scrollAmount,
      behavior: 'smooth'
    });
  };

  // Check scroll on window resize and tab changes
  useEffect(() => {
    checkScroll();
    const handleResize = () => checkScroll();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [destinationTabs, selectedTab]);

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
    // Group images by batch ID
    generatedImages.forEach(image => {
      if (image.batchId) {
        if (!batches[image.batchId]) {
          batches[image.batchId] = [];
        }
        batches[image.batchId].push(image);
      }
    });
  }

  return (
    <div className="bg-background overflow-hidden h-full">
      {/* Tabs for switching between Generated view and Destinations - frameless design */}
      <div className="border-b relative flex w-full max-w-full">
        {/* Left scroll indicator */}
        {showLeftScroll && (
          <button 
            onClick={() => scrollTabs('left')}
            className="absolute left-0 z-10 bg-background/80 h-full px-1 flex items-center justify-center hover:bg-background/90"
            aria-label="Scroll tabs left"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        )}
        
        <div 
          className="flex-1 overflow-x-auto no-scrollbar" 
          ref={tabsRef}
          onScroll={checkScroll}
          style={{ 
            msOverflowStyle: 'none', 
            scrollbarWidth: 'none',
            WebkitOverflowScrolling: 'touch' 
          }}
        >
          <div className="flex space-x-1 p-1 whitespace-nowrap min-w-fit">
            {destinationTabs.map(tab => (
              <button
                key={tab.id}
                onClick={() => setSelectedTab(tab.id)}
                className={`px-4 py-2 text-sm flex items-center rounded-md transition-colors flex-shrink-0
                  ${tab.highlight && selectedTab === tab.id 
                    ? 'bg-primary text-primary-foreground' 
                    : selectedTab === tab.id
                      ? 'text-primary border-b-2 border-primary'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                  }`}
              >
                {tab.icon}
                <span>{tab.label}</span>
              </button>
            ))}
          </div>
        </div>
        
        {/* Right scroll indicator */}
        {showRightScroll && (
          <button 
            onClick={() => scrollTabs('right')}
            className="absolute right-0 z-10 bg-background/80 h-full px-1 flex items-center justify-center hover:bg-background/90"
            aria-label="Scroll tabs right"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Content based on selected tab */}
      <div className="p-4 h-[calc(100%-48px)] overflow-auto">
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
          ) : imageUrl ? (
            <div className="relative h-full flex flex-col">
              <div className="flex-1 relative min-h-0">
                <img
                  src={imageUrl}
                  alt={currentPrompt || 'Generated image'}
                  className="h-full w-full object-contain mx-auto"
                />
              </div>
              {currentPrompt && (
                <div className="mt-4 text-sm text-center text-muted-foreground">
                  <p className="italic">"{currentPrompt}"</p>
                </div>
              )}
              {onFullscreen && (
                <button
                  onClick={onFullscreen}
                  className="absolute top-2 right-2 p-1 bg-background/80 rounded-md hover:bg-background"
                >
                  {isFullscreen ? (
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="4 14 10 14 10 20"></polyline>
                      <polyline points="20 10 14 10 14 4"></polyline>
                      <line x1="14" y1="10" x2="21" y2="3"></line>
                      <line x1="3" y1="21" x2="10" y2="14"></line>
                    </svg>
                  ) : (
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="15 3 21 3 21 9"></polyline>
                      <polyline points="9 21 3 21 3 15"></polyline>
                      <line x1="21" y1="3" x2="14" y2="10"></line>
                      <line x1="3" y1="21" x2="10" y2="14"></line>
                    </svg>
                  )}
                </button>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="48"
                height="48"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="mb-4"
              >
                <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
                <circle cx="9" cy="9" r="2" />
                <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
              </svg>
              <p>No image generated yet</p>
            </div>
          )
        ) : (
          // Display bucket content for the selected destination
          <BucketGridView
            key={`${selectedTab}-${bucketRefreshFlags[selectedTab] || 0}`}
            destination={getDestinationFile(selectedTab)}
            destinationName={destinationTabs.find(tab => tab.id === selectedTab)?.label || selectedTab}
            onImageClick={handleImageClick}
            refreshBucket={refreshBucket}
            isLoading={false}
            schedulerStatus={getStatusForDestination(selectedTab)}
            headless={destinationTabs.find(tab => tab.id === selectedTab)?.headless || false}
            icon={getPublishDestinations().find(d => d.id === selectedTab)?.icon || 'image'}
          />
        )}
      </div>
    </div>
  );
}

// For compatibility with import statements expecting a default export
export default ImageDisplay;

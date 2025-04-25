
import React, { useState, useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { 
  Bucket, BucketItem, fetchBucketDetails, toggleFavorite, 
  deleteImage, moveImage, copyImageToBucket, publishImage,
  fetchAllBuckets, performBucketMaintenance
} from '@/api/buckets-api';
import { PublishDestinations } from './PublishDestinations';
import { BucketImage } from './BucketImage';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Separator } from '@/components/ui/separator';
import { Settings, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors } from '@dnd-kit/core';
import { SortableContext, arrayMove, sortableKeyboardCoordinates, rectSortingStrategy } from '@dnd-kit/sortable';
import { SortableBucketImage } from './SortableBucketImage';

interface BucketGridViewProps {
  onFullScreenView?: (item: BucketItem) => void;
}

export function BucketGridView({ onFullScreenView }: BucketGridViewProps) {
  // State for selected destination/bucket
  const [selectedDestination, setSelectedDestination] = useState<any>(null);
  const [selectedItem, setSelectedItem] = useState<BucketItem | null>(null);
  
  // Query client for cache invalidation
  const queryClient = useQueryClient();
  
  // Fetch all available buckets
  const { data: buckets = [], isLoading: isBucketsLoading, error: bucketsError } = useQuery({
    queryKey: ['buckets'],
    queryFn: fetchAllBuckets,
    refetchOnWindowFocus: false
  });
  
  useEffect(() => {
    console.log('Loaded buckets:', buckets);
  }, [buckets]);
  
  // Fetch details for currently selected bucket
  const { 
    data: bucketDetails,
    isLoading: isBucketLoading, 
    isError: isBucketError,
    refetch: refetchBucketDetails
  } = useQuery({
    queryKey: ['bucket', selectedDestination?.file],
    queryFn: () => {
      if (!selectedDestination?.file) return Promise.resolve({ name: '', items: [] });
      console.log('Fetching details for bucket:', selectedDestination.file);
      return fetchBucketDetails(selectedDestination.file);
    },
    refetchOnWindowFocus: false,
    enabled: !!selectedDestination?.file
  });
  
  useEffect(() => {
    if (bucketDetails) {
      console.log('Bucket details loaded:', bucketDetails);
    }
  }, [bucketDetails]);
  
  // Refresh buckets after certain operations
  const refreshBucket = () => {
    if (selectedDestination?.file) {
      console.log('Refreshing bucket:', selectedDestination.file);
      queryClient.invalidateQueries({ queryKey: ['bucket', selectedDestination.file] });
    }
  };
  
  // Handle operations on images
  const handleToggleFavorite = async (bucket: string, filename: string, currentState: boolean) => {
    console.log('Toggle favorite:', bucket, filename, currentState);
    const success = await toggleFavorite(bucket, filename, currentState);
    if (success) {
      refreshBucket();
    }
  };
  
  const handleDelete = async (bucket: string, filename: string) => {
    if (confirm('Are you sure you want to delete this image?')) {
      console.log('Deleting image:', bucket, filename);
      const success = await deleteImage(bucket, filename);
      if (success) {
        refreshBucket();
      }
    }
  };
  
  const handleMoveUp = async (bucket: string, filename: string) => {
    console.log('Moving image up:', bucket, filename);
    const success = await moveImage(bucket, filename, 'up');
    if (success) {
      refreshBucket();
    }
  };
  
  const handleMoveDown = async (bucket: string, filename: string) => {
    console.log('Moving image down:', bucket, filename);
    const success = await moveImage(bucket, filename, 'down');
    if (success) {
      refreshBucket();
    }
  };
  
  const handleCopyTo = async (sourceBucket: string, targetBucket: string, filename: string) => {
    console.log('Copying image:', sourceBucket, targetBucket, filename);
    const success = await copyImageToBucket(sourceBucket, targetBucket, filename);
    if (success) {
      // Invalidate both source and target bucket queries
      queryClient.invalidateQueries({ queryKey: ['bucket', sourceBucket] });
      queryClient.invalidateQueries({ queryKey: ['bucket', targetBucket] });
    }
  };
  
  const handlePublish = async (bucket: string, filename: string) => {
    console.log('Publishing image:', bucket, filename);
    const success = await publishImage(bucket, filename);
    if (success) {
      refreshBucket();
      toast.success('Image published successfully');
    }
  };
  
  const handleBucketMaintenance = async (action: 'purge' | 'reindex' | 'extract') => {
    if (!selectedDestination?.file) return;
    
    const actionLabels = {
      purge: 'purge non-favorites',
      reindex: 're-index',
      extract: 'extract metadata'
    };
    
    if (confirm(`Are you sure you want to ${actionLabels[action]} this bucket?`)) {
      console.log('Performing bucket maintenance:', action, selectedDestination.file);
      const success = await performBucketMaintenance(selectedDestination.file, action);
      if (success) {
        refreshBucket();
      }
    }
  };
  
  const handleOpenImage = (item: BucketItem) => {
    const bucketItem = {
      ...item,
      bucket: selectedDestination?.file || ''
    };
    
    setSelectedItem(bucketItem);
    if (onFullScreenView) {
      console.log('Opening image in fullscreen:', bucketItem);
      onFullScreenView(bucketItem);
    }
  };
  
  const handleSelectDestination = (destination: any) => {
    console.log('Selected destination:', destination);
    setSelectedDestination(destination);
  };
  
  // DnD sensors for drag and drop functionality
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );
  
  // Handle DnD end event
  const handleDragEnd = (event: any) => {
    const { active, over } = event;
    
    if (active.id !== over.id) {
      // Find indices of items being dragged
      const oldIndex = bucketDetails?.items.findIndex(item => item.filename === active.id);
      const newIndex = bucketDetails?.items.findIndex(item => item.filename === over.id);
      
      if (oldIndex !== undefined && newIndex !== undefined && oldIndex >= 0 && newIndex >= 0) {
        // If moving up, call moveUp multiple times
        if (oldIndex > newIndex) {
          toast.info(`Moving image ${oldIndex - newIndex} positions up`);
          for (let i = 0; i < oldIndex - newIndex; i++) {
            handleMoveUp(selectedDestination.file, active.id);
          }
        } 
        // If moving down, call moveDown multiple times
        else if (oldIndex < newIndex) {
          toast.info(`Moving image ${newIndex - oldIndex} positions down`);
          for (let i = 0; i < newIndex - oldIndex; i++) {
            handleMoveDown(selectedDestination.file, active.id);
          }
        }
      }
    }
  };
  
  const isLoading = isBucketsLoading || isBucketLoading;
  const hasError = bucketsError || isBucketError;

  return (
    <div className="w-full">
      {/* Publish destinations selector */}
      <PublishDestinations 
        selectedDestination={selectedDestination?.id || null}
        onSelectDestination={handleSelectDestination}
      />
      
      {/* Current bucket info */}
      {selectedDestination && bucketDetails && (
        <div className="mb-4 flex justify-between items-center">
          <div>
            <h2 className="text-lg font-semibold">{bucketDetails.name}</h2>
            {bucketDetails.published && (
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Published: {bucketDetails.published}
                {bucketDetails.publishedAt && ` at ${bucketDetails.publishedAt}`}
              </p>
            )}
          </div>
          
          {/* Bucket maintenance dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm">
                <Settings className="h-4 w-4 mr-1" />
                <span className="hidden sm:inline">Tools</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => handleBucketMaintenance('purge')}>
                Purge non-favorites
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleBucketMaintenance('reindex')}>
                Re-index bucket
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleBucketMaintenance('extract')}>
                Extract JSON metadata
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      )}
      
      {/* Loading state */}
      {isLoading && (
        <div className="flex justify-center items-center p-8">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <span className="ml-2">Loading bucket...</span>
        </div>
      )}
      
      {/* Error state */}
      {hasError && (
        <div className="bg-red-50 p-4 rounded-md text-red-800">
          Failed to load bucket data. Please try again.
          <Button variant="outline" size="sm" className="ml-2" onClick={() => refetchBucketDetails()}>
            Retry
          </Button>
        </div>
      )}
      
      {/* Empty state */}
      {bucketDetails && bucketDetails.items.length === 0 && !isLoading && !hasError && (
        <div className="bg-gray-50 p-8 rounded-md text-center">
          <p className="text-gray-500">This bucket is empty.</p>
        </div>
      )}
      
      {/* Grid of bucket items */}
      {bucketDetails && bucketDetails.items.length > 0 && (
        <DndContext 
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext 
            items={bucketDetails.items.map(item => item.filename)}
            strategy={rectSortingStrategy}
          >
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2">
              {bucketDetails.items.map((item) => {
                // Add bucket info to each item
                const itemWithBucket = {
                  ...item,
                  bucket: bucketDetails.name
                };
                
                return (
                  <SortableBucketImage
                    key={item.filename}
                    id={item.filename}
                    bucket={bucketDetails.name}
                    item={itemWithBucket}
                    buckets={buckets}
                    onToggleFavorite={handleToggleFavorite}
                    onDelete={handleDelete}
                    onCopyTo={handleCopyTo}
                    onMoveUp={handleMoveUp}
                    onMoveDown={handleMoveDown}
                    onOpen={handleOpenImage}
                    onPublish={handlePublish}
                  />
                );
              })}
            </div>
          </SortableContext>
        </DndContext>
      )}
    </div>
  );
}

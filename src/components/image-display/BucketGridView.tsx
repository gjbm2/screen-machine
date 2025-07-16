import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { format, formatDistance } from 'date-fns';
import apiService from '@/utils/api';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { BucketItem as ApiBucketItem, Bucket as ApiBucket } from '@/utils/api';
import { Image as ImageIcon, RefreshCw, AlertCircle, Star, StarOff, Upload, MoreVertical, Trash, Share, Plus, ChevronsUpDown, Settings, ExternalLink, Send, Trash2, Copy, Info, Filter, Film, Camera, Link, CirclePause, CirclePlay, CircleStop, Maximize2, ArrowUp, ArrowDown, Pause, Square, ChevronUp, ChevronDown, Play, X, Mic, Moon } from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert';
import {
  DragStartEvent,
  DragEndEvent,
  DragOverEvent,
  useDroppable,
  DragOverlay,
  useDndMonitor,
} from '@dnd-kit/core';
import {
  SortableContext,
  useSortable,
  arrayMove,
  rectSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';

// New unified-dnd building blocks
import { ExpandableContainer } from '@/components/common';
import SortableImageGrid from '@/components/common/SortableImageGrid';
import { ImageItem } from '@/types/image-types';

// Import the new SchedulerControl component
import { SchedulerControl } from './SchedulerPanel';

// Import the new DROP_ZONES
import { DROP_ZONES } from '@/dnd/dropZones';

// Import the new getReferenceUrl utility
import { getReferenceUrl } from '@/utils/image-utils';

// Import the global isVideoFile utility
import { isVideoFile } from '@/utils/image-utils';

// Import the new useLoopeView context
import { useLoopeView } from '@/contexts/LoopeViewContext';
import { Switch } from "@/components/ui/switch";
import { Api } from "@/utils/api";
import { Download } from 'lucide-react';
import { getTranscriptionStatus } from '../../api';
import { ReferenceImageService } from '@/services/reference-image-service';

// Define the expected types based on the API response
interface BucketItem extends ApiBucketItem {
  raw_url?: string;
}

interface Bucket extends ApiBucket {}

interface BucketImage {
  id: string;
  url: string;
  thumbnail_url?: string;
  thumbnail_embedded?: string;
  prompt?: string;
  metadata?: Record<string, any>;
  created_at?: number;
  raw_url?: string;
  reference_images?: any[]; // Add reference images support
  bucketId?: string; // Add bucket ID support
}

// Define BucketDetails interface for this component (extended from the API type)
interface LocalBucketDetails {
  name: string;
  count: number;
  favorites_count: number;
  size_mb: number;
  last_modified: string;
  published: string | null;
  published_at: string | null;
  raw_url: string | null;
  thumbnail_url: string | null;
}

// Add NextAction interface
interface NextAction {
  has_next_action: boolean;
  next_time: string | null;
  description: string | null;
  minutes_until_next: number | null;
  timestamp: string;
  time_until_display?: string;
}

interface BucketGridViewProps {
  destination: string;
  destinationName?: string;
  onImageClick?: (image: BucketImage) => void;
  refreshBucket: (bucket: string) => void;
  isLoading: boolean;
  schedulerStatus?: { 
    is_running: boolean; 
    is_paused: boolean; 
    next_action?: NextAction | null;
  };
  headless?: boolean;
  icon?: string;
  headerPinned?: boolean; // indicates if tab bar is pinned so we should offset header
}

// Add a new type to distinguish drop targets
const enum DropTargetType {
  REORDER = 'reorder',
  PUBLISH = 'publish',
  TAB = 'tab'
}

// Simple confirmation dialog component for actions
interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  title: string;
  description: string;
  confirmLabel?: string;
}

const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  onOpenChange,
  onConfirm,
  title,
  description,
  confirmLabel = "Confirm"
}) => {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button 
            variant="destructive" 
            onClick={() => {
              onConfirm();
              onOpenChange(false);
            }}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

// Add simple modal components
const UploadModal = ({ isOpen, onClose, destination, onUploadComplete }: { 
  isOpen: boolean;
  onClose: () => void;
  destination: string;
  onUploadComplete: () => void;
}) => {
  // Simplified upload modal
  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Upload to {destination}</DialogTitle>
        </DialogHeader>
        <p>Upload functionality</p>
        <DialogFooter className="mt-4">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" onClick={onUploadComplete}>
            Upload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const MaintenanceModal = ({ isOpen, onClose, destination, onActionComplete }: { 
  isOpen: boolean;
  onClose: () => void;
  destination: string;
  onActionComplete: () => void;
}) => {
  const [days, setDays] = useState<number | undefined>();
  const [isLoading, setIsLoading] = useState(false);

  const handlePurge = async () => {
    setIsLoading(true);
    try {
      const result = await apiService.purgeBucket(destination, days);
      if (result.status === 'purged') {
        const message = result.removed.length > 0 
          ? `Purged ${result.removed.length} files successfully`
          : 'No files were purged';
        toast.success(message);
        onActionComplete();
        onClose();
      } else {
        toast.error(result.error || 'Failed to purge files');
      }
    } catch (error) {
      console.error('Error purging files:', error);
      toast.error(error instanceof Error ? error.message : 'Failed to purge files');
    } finally {
      setIsLoading(false);
    }
  };

  const handleReindex = async () => {
    setIsLoading(true);
    try {
      const success = await apiService.performBucketMaintenance(destination, 'reindex');
      if (success) {
        toast.success('Bucket reindexed successfully');
        onActionComplete();
        onClose();
      }
    } catch (error) {
      console.error('Error reindexing bucket:', error);
      toast.error('Failed to reindex bucket');
    } finally {
      setIsLoading(false);
    }
  };

  const handleExtractJson = async () => {
    setIsLoading(true);
    try {
      const success = await apiService.performBucketMaintenance(destination, 'extract');
      if (success) {
        toast.success('JSON extracted successfully');
        onActionComplete();
        onClose();
      }
    } catch (error) {
      console.error('Error extracting JSON:', error);
      toast.error('Failed to extract JSON');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Bucket Maintenance</DialogTitle>
          <DialogDescription>
            Perform maintenance operations on this bucket
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Purge Non-Favorites</Label>
            <div className="flex items-center space-x-2">
              <Input
                type="number"
                placeholder="Days (optional)"
                value={days || ''}
                onChange={(e) => setDays(e.target.value ? parseInt(e.target.value) : undefined)}
                min={1}
              />
              <Button 
                onClick={handlePurge} 
                disabled={isLoading}
                variant="destructive"
              >
                Purge
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">
              {days ? `Will remove non-favorite files older than ${days} days` : 'Will remove all non-favorite files'}
            </p>
          </div>
          <div className="space-y-2">
            <Label>Reindex Bucket</Label>
            <Button 
              onClick={handleReindex} 
              disabled={isLoading}
              variant="outline"
            >
              Reindex
            </Button>
          </div>
          <div className="space-y-2">
            <Label>Extract JSON</Label>
            <Button 
              onClick={handleExtractJson} 
              disabled={isLoading}
              variant="outline"
            >
              Extract
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export const BucketGridView = ({
  destination,
  destinationName,
  onImageClick,
  refreshBucket,
  isLoading: externalLoading,
  schedulerStatus,
  headless = false,
  icon,
  headerPinned = false,
}: BucketGridViewProps) => {
  const [bucketImages, setBucketImages] = useState<BucketImage[]>([]);
  const [bucketDetails, setBucketDetails] = useState<LocalBucketDetails | null>(null);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [showMaintenanceModal, setShowMaintenanceModal] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadUrl, setUploadUrl] = useState('');
  const [currentPublishedImage, setCurrentPublishedImage] = useState<BucketImage | null>(null);
  const [destinations, setDestinations] = useState<{id: string, name: string, headless?: boolean, has_bucket: boolean}[]>([]);
  const [showFavoritesFirst, setShowFavoritesFirst] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadTab, setUploadTab] = useState<'file' | 'url'>('file');
  const [selectedImage, setSelectedImage] = useState<BucketImage | null>(null);
  const [showImageDetail, setShowImageDetail] = useState(false);
  const [refreshTimeout, setRefreshTimeout] = useState<NodeJS.Timeout | null>(null);
  const [isDragging, setIsDragging] = useState<string | null>(null);
  const [dragOverTarget, setDragOverTarget] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [activeDraggedImage, setActiveDraggedImage] = useState<BucketImage | null>(null);
  const [activeDropTarget, setActiveDropTarget] = useState<string | null>(null);
  const [dropTargetType, setDropTargetType] = useState<DropTargetType | null>(null);
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const [sectionSortMap, setSectionSortMap] = useState<Record<string, 'desc' | 'asc'>>({});
  const [hasBucket, setHasBucket] = useState<boolean>(true);
  
  // Add state for the delete confirmation dialog
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [sectionToDelete, setSectionToDelete] = useState<string | null>(null);
  const [deleteImagesCount, setDeleteImagesCount] = useState(0);
  const [maskEnabled, setMaskEnabled] = useState(true);
  const api = new Api();
  
  const hasCamera = 'mediaDevices' in navigator && 'getUserMedia' in navigator.mediaDevices;
  const [showAppDownloadModal, setShowAppDownloadModal] = useState(false);
  const [isIOS, setIsIOS] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [isNewVersion, setIsNewVersion] = useState(false);

  const debouncedFetchBucketDetails = useCallback(() => {
    if (refreshTimeout) {
      clearTimeout(refreshTimeout);
    }
    const timeout = setTimeout(() => {
      fetchBucketDetails();
    }, 500);
    setRefreshTimeout(timeout);
  }, [refreshTimeout]);

  useEffect(() => {
    return () => {
      if (refreshTimeout) {
        clearTimeout(refreshTimeout);
      }
    };
  }, [refreshTimeout]);

  useEffect(() => {
    fetchBucketDetails();
    fetchDestinations();
  }, [destination]);

  // Fetch mask state on mount
  useEffect(() => {
    const fetchMaskState = async () => {
      try {
        const result = await api.getMaskState(destination);
        setMaskEnabled(result.enabled);
      } catch (error) {
        console.error('Error fetching mask state:', error);
        // Default to true if there's an error
        setMaskEnabled(true);
      }
    };
    
    fetchMaskState();
  }, [destination]);

  useEffect(() => {
    // Detect iOS
    const userAgent = window.navigator.userAgent.toLowerCase();
    setIsIOS(/iphone|ipad|ipod/.test(userAgent));
  }, []);

  const fetchDestinations = async () => {
    try {
      const buckets = await apiService.getPublishDestinations();
      
      // Set destinations list
      setDestinations(buckets.map(bucket => ({
        id: bucket.id,
        name: bucket.name || bucket.id,
        headless: bucket.headless || false,
        has_bucket: bucket.has_bucket || false
      })));
      
      // Set hasBucket state for current destination
      const currentDest = buckets.find(d => d.id === destination);
      setHasBucket(currentDest?.has_bucket !== false);
    } catch (error) {
      console.error('Error fetching destinations:', error);
      toast.error('Failed to fetch destinations');
    }
  };

  const fetchBucketDetails = async () => {
    setLoading(true);
    setError(null);
    try {
      // Check if the current destination has a bucket
      const currentDest = await apiService.getPublishDestinations();
      const destInfo = currentDest.find(d => d.id === destination);
      const hasDestBucket = destInfo?.has_bucket !== false;
      
      // Update hasBucket state
      setHasBucket(hasDestBucket);
      
      // If this destination has no bucket, ONLY use getPublishedContent API
      if (!hasDestBucket) {
        console.log(`Destination ${destination} does not have a bucket, using getPublishedContent API`);
        
        // NEVER call getBucketDetails for bucketless destinations - use dedicated published content API
        const publishedContent = await apiService.getPublishedContent(destination);
        
        // Set empty bucket data with only published content
        setBucketDetails({
          name: destInfo?.name || destination,
          count: 0,
          favorites_count: 0,
          size_mb: 0,
          last_modified: '',
          published: publishedContent.published,
          published_at: publishedContent.publishedAt,
          raw_url: publishedContent.raw_url,
          thumbnail_url: publishedContent.thumbnail_url
        });
        
        setBucketImages([]);
        
        // Find the published image if it exists
        if (publishedContent.published && (publishedContent.raw_url || publishedContent.thumbnail_url)) {
          // Create a placeholder published image
          const publishedImage = {
            id: publishedContent.published,
            url: publishedContent.raw_url || '',
            thumbnail_url: publishedContent.thumbnail_url || '',
            metadata: {},
            created_at: 0
          };
          
          console.log('Using published image from API:', publishedImage);
          setCurrentPublishedImage(publishedImage);
        } else {
          setCurrentPublishedImage(null);
        }
        
        setLoading(false);
        return;
      }
      
      // Only for destinations WITH buckets, proceed with normal getBucketDetails logic
      const details: any = await apiService.getBucketDetails(destination);
      if (details.error) {
        throw new Error(details.error);
      }

      const images = details.items.map((item: BucketItem) => ({
        id: item.filename,
        url: item.url || '',
        thumbnail_url: item.thumbnail_url,
        thumbnail_embedded: item.thumbnail_embedded,
        prompt: item.metadata?.prompt,
        metadata: {
          ...item.metadata,
          favorite: item.favorite
        },
        created_at: item.created_at || item.metadata?.timestamp || item.metadata?.modified,
        raw_url: item.raw_url
      }));

      // Sort images by favorite status and timestamp
      const sortedImages = images.sort((a: BucketImage, b: BucketImage) => {
        if (showFavoritesFirst) {
          if (a.metadata?.favorite && !b.metadata?.favorite) return -1;
          if (!a.metadata?.favorite && b.metadata?.favorite) return 1;
        }
        return 0;
      });

      setBucketImages(sortedImages);
      setBucketDetails({
        name: details.name,
        count: details.items.length,
        favorites_count: details.items.filter((item: BucketItem) => item.favorite).length,
        size_mb: details.size_mb || 0,
        last_modified: details.last_modified || '',
        published: details.published,
        published_at: details.publishedAt,
        raw_url: details.raw_url,
        thumbnail_url: details.thumbnail_url
      });

      // Find the published image
      if (details.published) {
        const publishedImage = sortedImages.find(img => img.id === details.published);
        if (publishedImage) {
          setCurrentPublishedImage(publishedImage);
        } else {
          // If we didn't find it in the regular items, it may be a published image not in the bucket
          // Simply use the thumbnail_url and raw_url directly from the API response
          const fallbackPublishedImage = {
            id: details.published,
            url: details.raw_url || '',
            thumbnail_url: details.thumbnail_url || '',
            metadata: {},
            created_at: 0
          };
          
          console.log('Using published image from API:', fallbackPublishedImage);
          setCurrentPublishedImage(fallbackPublishedImage);
        }
      }
    } catch (error) {
      console.error('Error fetching bucket details:', error);
      toast.error('Failed to fetch bucket details');
      setError('Failed to fetch bucket details');
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = () => {
    fetchBucketDetails();
    refreshBucket(destination);
  };

  // Access Loope view opener once
  let openLoope;
  try {
    const loopeContext = useLoopeView();
    openLoope = loopeContext.open;
  } catch (error) {
    console.warn("LoopeView context not available:", error);
    // Provide a fallback function that won't crash
    openLoope = () => {
      console.warn("Loope view can't be opened - context not available");
    };
  }

  const handleImageClick = (image: BucketImage) => {
    // Build list of ImageItems for viewer
    const imageItems: ImageItem[] = bucketImages.map(img => ({
      id: img.id,
      urlFull: img.url,
      urlThumb: img.thumbnail_url || img.thumbnail_embedded || img.url,
      promptKey: img.prompt || '',
      seed: 0,
      createdAt: new Date(img.created_at || Date.now()).toISOString(),
      isFavourite: !!img.metadata?.favorite,
      mediaType: img.url.toLowerCase().match(/\.mp4|\.webm/) ? 'video' : 'image',
      raw_url: img.raw_url || img.url,
      metadata: img.metadata,
      bucketId: destination,
    }));

    const clickedIdx = bucketImages.findIndex(i => i.id === image.id);
    if (clickedIdx !== -1 && openLoope) {
      const contextTitle = `${destinationName || destination}`;
      openLoope(imageItems, clickedIdx, contextTitle);
    }

    if (onImageClick) {
      onImageClick(image);
    } else {
      // fallback to original detail view until fully replaced
      setSelectedImage(image);
      setShowImageDetail(true);
    }
  };

  const handleToggleFavorite = async (image: BucketImage) => {
    try {
      const newFavoriteState = !image.metadata?.favorite;
      const success = await apiService.toggleFavorite(destination, image.id, newFavoriteState);
      if (success) {
        // Update local state immediately
        setBucketImages(prevImages => 
          prevImages.map(img => {
            const baseId = img.id.endsWith('#dup') ? img.id.replace('#dup', '') : img.id;
            if (baseId === image.id) {
              return { ...img, metadata: { ...img.metadata, favorite: newFavoriteState } };
            }
            return img;
          })
        );
        // Update bucket details
        setBucketDetails(prev => prev ? {
          ...prev,
          favorites_count: prev.favorites_count + (newFavoriteState ? 1 : -1)
        } : null);
      }
    } catch (error) {
      console.error('Error toggling favorite:', error);
      toast.error('Failed to toggle favorite');
    }
  };

  const handleDeleteImage = async (image: BucketImage) => {
    // Only show confirmation dialog for favorite images
    if (image.metadata?.favorite) {
      if (!confirm('Are you sure you want to delete this favorite image?')) {
        return; // Exit if user cancels deletion of a favorite
      }
    }
    
    try {
      const success = await apiService.deleteImage(destination, image.id);
      if (success) {
        // Update local state immediately
        setBucketImages(prevImages => prevImages.filter(img => img.id !== image.id));
        // Update bucket details
        setBucketDetails(prev => prev ? {
          ...prev,
          count: prev.count - 1,
          favorites_count: image.metadata?.favorite ? prev.favorites_count - 1 : prev.favorites_count
        } : null);
      }
    } catch (error) {
      console.error('Error deleting image:', error);
      toast.error('Failed to delete image');
    }
  };

  const handlePublish = async (bucket: string, filename: string) => {
    try {
      // Use the new unified publish API for bucket-to-bucket publishing
      const success = await apiService.publishImageUnified({
        dest_bucket_id: bucket,
        src_bucket_id: destination,
        filename: filename
      });
      
      if (success) {
        // Update local state immediately
        setBucketDetails(prev => prev ? {
          ...prev,
          published: filename,
          published_at: new Date().toISOString()
        } : null);
        
        // Refresh bucket details to ensure everything is in sync
        await fetchBucketDetails();
        
        toast.success('Image published successfully');
      } else {
        // Handle successful response but unsuccessful operation
        console.warn('Publish operation returned false');
        toast.error('Failed to publish image');
      }
    } catch (error) {
      // Log detailed error info for debugging
      console.error('Error publishing image:', error);
      
      // Ensure UI updates despite error
      toast.error(`Failed to publish image: ${error instanceof Error ? error.message : 'Unknown error'}`);
      
      // Make sure bucket details are refreshed even on error
      try {
        await fetchBucketDetails();
      } catch (refreshError) {
        console.error('Failed to refresh after publish error:', refreshError);
      }
    }
  };

  const handleCopyToDestination = async (image: BucketImage, targetBucket: string) => {
    try {
      // Extract the original ID if this is a duplicate image
      const originalId = getOriginalId(image.id);
      const result = await apiService.copyImageToBucket(destination, targetBucket, originalId, true);
      if (result && (result.status === 'copied' || result.status === 'moved')) {
        toast.success(`Image ${result.status} to ${targetBucket} successfully`);
        // Refresh the target bucket to show the new image
        debouncedFetchBucketDetails();
      } else {
        toast.error(`Failed to copy image to ${targetBucket}`);
      }
    } catch (error) {
      console.error('Error copying image:', error);
      toast.error('Failed to copy image');
    }
  };

  // Handle maintenance actions
  const handlePurgeNonFavorites = () => {
    setShowMaintenanceModal(true);
  };

  const handleReindex = async () => {
    try {
      const success = await apiService.performBucketMaintenance(destination, 'reindex');
      if (success) {
        toast.success('Bucket reindexed successfully');
        debouncedFetchBucketDetails();
      }
    } catch (error) {
      console.error('Error reindexing bucket:', error);
      toast.error('Failed to reindex bucket');
    }
  };

  const handleExtractJson = async () => {
    try {
      const success = await apiService.performBucketMaintenance(destination, 'extract');
      if (success) {
        toast.success('JSON extracted successfully');
        refreshBucket(destination);
      } else {
        toast.error('Failed to extract JSON');
      }
    } catch (error) {
      console.error('Error extracting JSON:', error);
      toast.error('Failed to extract JSON');
    }
  };

  // Handle "Generate Again" from an image with reference images
  const handleGenerateAgain = (image: BucketImage) => {
    // Extract the prompt and settings from the image
    const imageMetadata = image.metadata as {
      prompt?: string;
      workflow?: string;
      params?: Record<string, any>;
      reference_images?: any[];
    } | undefined;

    // Extract reference images from the API response (top-level field) first,
    // as it contains properly formatted URLs. Fall back to metadata if needed.
    const referenceImages = image.reference_images || imageMetadata?.reference_images || [];
    
    // Convert to accessible URLs - use actual bucket ID, fallback to destination for backward compatibility
    const bucketId = image.bucketId || destination;
    const referenceUrls = ReferenceImageService.getReferenceImageUrls(bucketId, referenceImages);
    
    // Debug logging to understand what reference URLs are being generated
    console.log('[handleGenerateAgain] Reference images:', referenceImages);
    console.log('[handleGenerateAgain] Bucket ID:', bucketId);
    console.log('[handleGenerateAgain] Generated reference URLs:', referenceUrls);

    // Reuse the original batch ID for "Generate Again" to maintain consistency
    const batchId = image.batchId || `batch-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

    // Create parameters for generation
    // For "Generate Again", we want to use the exact same refined prompt and settings
    // without re-running the refiner
    const params: any = {
      prompt: image.prompt || imageMetadata?.prompt || '',
      batch_id: batchId,
      workflow: imageMetadata?.workflow || '',
      params: {
        ...imageMetadata?.params || {},
        publish_destination: destination, // Add publish_destination for reference image resolution
      },
      global_params: {
        batch_size: 1, // Default batch size for Generate Again
        ...imageMetadata?.global_params || {},
      },
      placeholders: [], // Required by the API type
      referenceUrls,
      refiner: "none", // Explicitly disable refiner - we want to use the already-refined prompt
    };
    params.__skipPlaceholder = true;
    
    toast.loading('Generating image...', { id: 'generate-toast' });
    apiService.generateImage(params)
      .then(() => {
        toast.dismiss('generate-toast');
        toast.success('Image generated');
        // Refresh the bucket to show the new image
        refreshBucket(destination);
      })
      .catch((error: any) => {
        toast.dismiss('generate-toast');
        toast.error('Failed to generate image');
        console.error('Error generating image:', error);
      });
  };

  // Keep this function as it's used by the dropdown menu
  const handleOpenSchedulerPage = () => {
    // Navigate to scheduler page for this destination
    window.location.href = `/scheduler?destination=${destination}`;
  };

  // Format a friendly date string
  const formatPublishDate = (dateString?: string) => {
    if (!dateString) return '';
    try {
      const date = new Date(dateString);
      return `${formatDistance(date, new Date(), { addSuffix: true })}`;
    } catch (error) {
      return dateString;
    }
  };

  // Sort images with favorites first if enabled
  const getSortedImages = () => {
    if (!showFavoritesFirst) return bucketImages;
    
    return [...bucketImages].sort((a, b) => {
      if ((a.metadata?.favorite && b.metadata?.favorite) || (!a.metadata?.favorite && !b.metadata?.favorite)) {
        return 0; // Keep original order
      }
      return a.metadata?.favorite ? -1 : 1;
    });
  };

  const handleUploadTabChange = (value: 'file' | 'url') => {
    setUploadTab(value);
  };

  const handleUpload = async () => {
    if (!uploadFile && !uploadUrl) {
      toast.error('Please select a file or enter a URL');
      return;
    }

    try {
      if (uploadFile) {
        const formData = new FormData();
        formData.append('file', uploadFile);
        const response = await fetch(`${apiService.getApiUrl()}/buckets/${destination}/upload`, {
          method: 'POST',
          body: formData
        });
        if (!response.ok) throw new Error('Upload failed');
      } else if (uploadUrl) {
        const response = await fetch(`${apiService.getApiUrl()}/buckets/${destination}/upload`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: uploadUrl })
        });
        if (!response.ok) throw new Error('Upload failed');
      }
      
      setShowUploadModal(false);
      setUploadFile(null);
      setUploadUrl('');
      fetchBucketDetails();
      toast.success('Image uploaded successfully');
    } catch (error) {
      console.error('Error uploading image:', error);
      toast.error('Failed to upload image');
    }
  };

  // Utility to check video files – shares logic with getReferenceUrl helper
  const isVideo = (filename: string) => {
    // Re-use global helper that also strips the "#dup" suffix
    return isVideoFile(filename);
  };

  const sortedImages = getSortedImages();
  const favoritesCount = bucketImages.filter(img => img.metadata?.favorite).length;
  
  // ----------  Build section groups (Favourites + date buckets) ---------- //

  const favourites = bucketImages.filter(img => img.metadata?.favorite);

  /*
    Build date-based groups.  
    We want favourite images to appear BOTH in the dedicated favourites section **and** inside their
    chronological date bucket.  However, the DnD-Kit library requires every draggable item id to be
    unique across the whole page.  To avoid duplicate ids we create a lightweight *clone* of each
    favourite image with a modified id (we append `#dup`).  All user actions (toggle favourite, delete,
    etc.) will translate this duplicate id back to the original id before performing API calls.
  */

  // Helper to derive friendly date bucket label – defined here (before first use)
  const getDateBucketLabel = (timestamp?: number): string => {
    if (!timestamp) return 'Older';
    
    const date = new Date((timestamp ?? 0) * 1000);
    const now = new Date();

    // Set both dates to midnight for day comparison
    const dateAtMidnight = new Date(date);
    dateAtMidnight.setHours(0, 0, 0, 0);
    const nowAtMidnight = new Date(now);
    nowAtMidnight.setHours(0, 0, 0, 0);
    
    // Calculate difference in days
    const diffMs = nowAtMidnight.getTime() - dateAtMidnight.getTime();
    const diffDays = Math.floor(diffMs / (24 * 60 * 60 * 1000));

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';

    // Start of current week (Monday)
    const startOfWeek = new Date(nowAtMidnight);
    const day = startOfWeek.getDay();
    const diff = startOfWeek.getDate() - day + (day === 0 ? -6 : 1); // Adjust for Sunday
    startOfWeek.setDate(diff);
    if (date >= startOfWeek) return 'Earlier this week';

    // Previous week
    const startOfLastWeek = new Date(startOfWeek);
    startOfLastWeek.setDate(startOfLastWeek.getDate() - 7);
    if (date >= startOfLastWeek) return 'Last week';

    return 'Older';
  };

  const dateGroupsMap: Record<string, BucketImage[]> = {};

  bucketImages.forEach((img) => {
    const label = getDateBucketLabel(img.created_at);

    // If the image is a favourite we clone it with a unique id so it can coexist with the
    // original one that lives in the favourites section.
    const imgForGroup: BucketImage = img.metadata?.favorite
      ? { ...img, id: `${img.id}#dup` }
      : img;

    if (!dateGroupsMap[label]) dateGroupsMap[label] = [];
    dateGroupsMap[label].push(imgForGroup);
  });
  
  // Sort each date group by timestamp (newest first)
  Object.keys(dateGroupsMap).forEach(key => {
    dateGroupsMap[key].sort((a, b) => {
      const aTime = a.created_at || 0;
      const bTime = b.created_at || 0;
      return bTime - aTime; // Descending order (newest first)
    });
  });

  // Preserve deterministic order of date group labels
  const orderedDateLabels = ['Today', 'Yesterday', 'Earlier this week', 'Last week', 'Older'];

  interface Section {
    id: string;
    label: string;
    variant: 'favourites' | 'dated';
    images: BucketImage[];
  }

  const sections: Section[] = [];
  if (favourites.length > 0) {
    sections.push({ id: 'favourites', label: `Favourites (${favourites.length})`, variant: 'favourites', images: favourites });
  }

  orderedDateLabels.forEach((lbl) => {
    if (dateGroupsMap[lbl] && dateGroupsMap[lbl].length > 0) {
      sections.push({ id: lbl.toLowerCase().replace(/\s+/g, '-'), label: `${lbl} (${dateGroupsMap[lbl].length})`, variant: 'dated', images: dateGroupsMap[lbl] });
    }
  });

  // Helper to map a duplicate id back to its original image id
  const getOriginalId = (id: string) => id.includes('#dup') ? id.split('#dup')[0] : id;

  // NEW: helper to find the BucketImage regardless of duplicate id
  const findImageByAnyId = (id: string) => {
    const originalId = getOriginalId(id);
    return bucketImages.find(img => img.id === originalId);
  };

  // Convert BucketImage -> ImageItem for display components
  const toImageItem = (img: BucketImage): ImageItem => ({
    id: img.id,
    urlThumb: img.thumbnail_url,
    urlFull: img.url,
    promptKey: img.prompt || '',
    seed: 0,
    createdAt: img.created_at ? new Date(img.created_at * 1000).toISOString() : '',
    isFavourite: !!img.metadata?.favorite,
    mediaType: isVideo(img.id) ? 'video' : 'image',
    raw_url: img.raw_url,
    metadata: img.metadata,
    bucketId: destination,
  });

  const handleFavouriteOrderChange = (newOrder: string[]) => {
    if (newOrder.length !== favourites.length) return;

    // Optimistically update UI so order sticks immediately
    setBucketImages(prevImages => {
      const favMap: Record<string, BucketImage> = {};
      prevImages.forEach(img => {
        if (img.metadata?.favorite) {
          favMap[img.id] = img;
        }
      });

      // Re-assemble favourites according to newOrder
      const reorderedFavourites: BucketImage[] = newOrder.map(id => favMap[id]).filter(Boolean);
      const nonFavourites = prevImages.filter(img => !img.metadata?.favorite);
      return [...reorderedFavourites, ...nonFavourites];
    });

    // Identify first item whose index changed
    const prev = favourites.map((f) => f.id);
    let movedId: string | null = null;
    let newIndex = -1;
    prev.forEach((id, idx) => {
      if (newOrder[idx] !== id && !movedId) {
        movedId = id; // original item at this position moved somewhere else
      }
    });
    if (!movedId) return;

    newIndex = newOrder.indexOf(movedId);

    const targetFilename = newIndex === 0 ? null : newOrder[newIndex - 1];
    moveToPosition(movedId, targetFilename);
  };

  // Add moveToPosition method for reordering
  const moveToPosition = async (filename: string, targetFilename: string | null) => {
    try {
      // Strip any #dup suffix from filenames
      const originalFilename = getOriginalId(filename);
      const originalTargetFilename = targetFilename ? getOriginalId(targetFilename) : null;
      await apiService.moveToPosition(destination, originalFilename, originalTargetFilename);
      toast.success('Image position updated');
      debouncedFetchBucketDetails();
    } catch (error) {
      console.error('Error moving image:', error);
      toast.error('Failed to move image');
    }
  };

  // Update handleDragStart to properly resolve duplicate image ids
  const handleDragStart = (event: DragStartEvent) => {
    const { active } = event;
    setActiveId(active.id as string);
    
    // Resolve duplicate ("#dup") ids so activeDraggedImage always points to the real BucketImage
    const draggedImage = findImageByAnyId(active.id as string);
    if (draggedImage) {
      setActiveDraggedImage(draggedImage);
    }
    
    // Prevent scrolling during drag on touch devices
    document.body.style.touchAction = 'none';
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;
    
    console.log("Drag ended:", { 
      active: active?.id, 
      over: over?.id,
      data: over?.data?.current
    });
    
    if (!over) {
      setActiveId(null);
      setActiveDraggedImage(null);
      setActiveDropTarget(null);
      setDropTargetType(null);
      document.body.style.touchAction = 'auto';
      return;
    }

    // Check if we're over the prompt dropzone (skip further processing)
    if (over.id === DROP_ZONES.PROMPT) {
      console.log("Skipping BucketGridView drag end handling for prompt area drops");
      setActiveId(null);
      setActiveDraggedImage(null);
      setActiveDropTarget(null);
      setDropTargetType(null);
      document.body.style.touchAction = 'auto';
      return;
    }
    
    // Check if we're over the publish dropzone
    if (over.id === DROP_ZONES.PUBLISHED) {
      console.log("Publishing image:", active.id);
      const draggedImage = findImageByAnyId(active.id as string);
      
      if (draggedImage) {
        try {
          // Show a toast notification that we're publishing
          toast.loading(`Publishing ${draggedImage.id}...`, { id: 'publish-toast' });
          
          // Call the publish API
          await handlePublish(destination, draggedImage.id);
          
          // Success toast will be shown by handlePublish
          toast.dismiss('publish-toast');
        } catch (error) {
          console.error('Error publishing image:', error);
          toast.error('Failed to publish image');
        }
      }
      // Reset drag state
      setActiveId(null);
      setActiveDraggedImage(null);
      setActiveDropTarget(null);
      setDropTargetType(null);
      document.body.style.touchAction = 'auto';
      return;
    } else if (typeof over.id === 'string' && (over.id as string).startsWith('empty-favorites-')) {
      // Handle drops into the empty favorites container
      console.log("Dropping into empty favorites container:", active.id);
      
      if (activeDraggedImage) {
        const isFav = !!activeDraggedImage.metadata?.favorite;
        const originalId = getOriginalId(activeDraggedImage.id);
        const originalImage = bucketImages.find(img => img.id === originalId);
        
        if (originalImage && !isFav) {
          // Add the image to favorites
          console.log('Adding to favorites (dropped in empty container):', originalImage.id);
          try {
            await handleToggleFavorite(originalImage);
            toast.success('Added to favorites');
            // Refresh after making changes
            debouncedFetchBucketDetails();
          } catch (error) {
            console.error('Error adding to favorites:', error);
            toast.error('Failed to add to favorites');
          }
        }
      }
      
      // Reset drag state
      setActiveId(null);
      setActiveDraggedImage(null);
      setActiveDropTarget(null);
      setDropTargetType(null);
      document.body.style.touchAction = 'auto';
      return;
    } else if (typeof over.id === 'string' && (over.id as string).startsWith('empty-dated-')) {
      // Handle drops into the empty dated container (unfavorite)
      console.log("Dropping into empty dated container:", active.id);
      
      if (activeDraggedImage) {
        const isFav = !!activeDraggedImage.metadata?.favorite;
        const originalId = getOriginalId(activeDraggedImage.id);
        const originalImage = bucketImages.find(img => img.id === originalId);
        
        if (originalImage && isFav) {
          // Remove the image from favorites
          console.log('Removing from favorites (dropped in empty dated container):', originalImage.id);
          try {
            await handleToggleFavorite(originalImage);
            toast.success('Removed from favorites');
            // Refresh after making changes
            debouncedFetchBucketDetails();
          } catch (error) {
            console.error('Error removing from favorites:', error);
            toast.error('Failed to remove from favorites');
          }
        }
      }
      
      // Reset drag state
      setActiveId(null);
      setActiveDraggedImage(null);
      setActiveDropTarget(null);
      setDropTargetType(null);
      document.body.style.touchAction = 'auto';
      return;
    } else if (typeof over.id === 'string' && (over.id as string).startsWith('group-')) {
      // Check if we dropped on a content area - only act on content area drops, ignore header drops
      const overData = over.data?.current as any;
      const isContentArea = overData?.type === 'content-area';
      
      if (!isContentArea) {
        // Not a content area, don't do anything with this drop
        setActiveId(null);
        setActiveDraggedImage(null);
        setActiveDropTarget(null);
        setDropTargetType(null);
        document.body.style.touchAction = 'auto';
        return;
      }
      
      const groupKey = (over.id as string).slice(6);
      const targetSection = sections.find(s => s.id === groupKey);
      
      if (targetSection && activeDraggedImage) {
        const isFav = !!activeDraggedImage.metadata?.favorite;
        const originalId = getOriginalId(activeDraggedImage.id);
        const originalImage = bucketImages.find(img => img.id === originalId);
        
        if (originalImage) {
          // If we're dropping a non-favorite into the favorites section, mark it as favorite
          if (targetSection.variant === 'favourites' && !isFav) {
            console.log('Adding to favorites (dropped in section):', originalImage.id);
            
            // Check if this is an empty favorites container
            const isEmptyFavorites = targetSection.images.length === 0;
            if (isEmptyFavorites) {
              console.log('Favorites section is empty, special handling');
            }
            
            // Toggle favorite status
            try {
              await handleToggleFavorite(originalImage);
              toast.success('Added to favorites');
              
              // Always refresh after modifying favorites
              debouncedFetchBucketDetails();
            } catch (error) {
              console.error('Error adding to favorites:', error);
              toast.error('Failed to add to favorites');
            }
          } 
          // If we're dropping a favorite into a non-favorites section, unfavorite it
          else if (targetSection.variant !== 'favourites' && isFav) {
            console.log('Removing from favorites:', originalImage.id);
            await handleToggleFavorite(originalImage);
            toast.success('Removed from favorites');
          }
        }
      }
      
      // Reset drag state
      setActiveId(null);
      setActiveDraggedImage(null);
      setActiveDropTarget(null);
      setDropTargetType(null);
      document.body.style.touchAction = 'auto';
      return;
    } else if (over.id !== active.id) {
      // Determine dragged and target images using original ids so #dup clones behave like originals
      const draggedImage = findImageByAnyId(active.id as string);
      const targetImage = findImageByAnyId(over.id as string);

      if (!draggedImage) return;

      const draggedIsFav = !!draggedImage.metadata?.favorite;
      const targetIsFav = !!targetImage?.metadata?.favorite;

      // Toggle favourite status when dropping onto an image of a different favourite state
      if (draggedIsFav && !targetIsFav) {
        await handleToggleFavorite(draggedImage);
        // Reset drag state and exit early
        setActiveId(null);
        setActiveDraggedImage(null);
        setActiveDropTarget(null);
        setDropTargetType(null);
        document.body.style.touchAction = 'auto';
        return; // No re-order required
      }
      if (!draggedIsFav && targetIsFav) {
        // Add to favourites first
        await handleToggleFavorite(draggedImage);

        // Move the newly favourited image just AFTER the target favourite image
        try {
          // Strip any #dup suffix from both ids
          const originalDraggedId = getOriginalId(draggedImage.id);
          const originalTargetId = getOriginalId(targetImage.id);
          await apiService.moveToPosition(
            destination,
            originalDraggedId,
            originalTargetId ? getOriginalId(originalTargetId) : null
          );
        } catch (error) {
          console.error('Error positioning newly favourited image:', error);
          // Swallow; order will at least be refreshed on next fetch
        }

        // Optimistically update local order so UI reflects new position immediately
        setBucketImages(items => {
          const newItems = [...items];
          const dragIdx = newItems.findIndex(i => i.id === draggedImage.id);
          const targetIdx = newItems.findIndex(i => i.id === targetImage.id);
          if (dragIdx === -1 || targetIdx === -1) return items;
          const [dragItem] = newItems.splice(dragIdx, 1);
          // Insert after the target image
          newItems.splice(targetIdx + 1, 0, dragItem);
          return newItems;
        });

        // Reset drag state and exit early
        setActiveId(null);
        setActiveDraggedImage(null);
        setActiveDropTarget(null);
        setDropTargetType(null);
        document.body.style.touchAction = 'auto';
        return;
      }

      // Only allow re-ordering when BOTH dragged and target images are favourites
      if (!draggedIsFav || !targetIsFav || !targetImage) {
        // Simply reset drag state – no action required
        setActiveId(null);
        setActiveDraggedImage(null);
        setActiveDropTarget(null);
        setDropTargetType(null);
        document.body.style.touchAction = 'auto';
        return;
      }

      // Calculate positions in the UI sorted array (favourites only)
      const oldIndex = sortedImages.findIndex(item => item.id === active.id);
      const newIndex = sortedImages.findIndex(item => item.id === over.id);

      // Update local state immediately for responsive UI
      setBucketImages(items => {
        const newItems = [...items];
        const draggedItemIndex = newItems.findIndex(item => item.id === active.id);
        if (draggedItemIndex === -1) return items;

        // Remove the dragged item
        const [draggedItem] = newItems.splice(draggedItemIndex, 1);

        // Find the target position
        const targetIndex = newItems.findIndex(item => item.id === over.id);
        if (targetIndex === -1) return items;

        // Insert after the target if dragged from before, otherwise insert before
        const insertIndex = oldIndex < newIndex ? targetIndex + 1 : targetIndex;
        newItems.splice(insertIndex, 0, draggedItem);

        return newItems;
      });

      try {
        let insertAfterId: string | null;

        if (newIndex > oldIndex) {
          // Moving forward - insert after the target
          insertAfterId = targetImage.id;
        } else {
          // Moving backward - insert after the previous image or at the beginning
          const targetIndex = sortedImages.findIndex(img => img.id === targetImage.id);
          insertAfterId = targetIndex <= 0 ? null : sortedImages[targetIndex - 1].id;
        }

        await apiService.moveToPosition(
          destination,
          getOriginalId(draggedImage.id),
          insertAfterId ? getOriginalId(insertAfterId) : null
        );
      } catch (error) {
        console.error('Error reordering favourite image:', error);
        toast.error('Failed to reorder favourite');
        fetchBucketDetails(); // Only refresh on error
      }
    }
    
    // Reset state
    setActiveId(null);
    setActiveDraggedImage(null);
    setActiveDropTarget(null);
    setDropTargetType(null);
    document.body.style.touchAction = 'auto';
  };

  const handleDragOver = (event: DragOverEvent) => {
    const { active, over } = event;
    
    if (!over) {
      if (activeDropTarget !== null) setActiveDropTarget(null);
      if (dropTargetType !== null) setDropTargetType(null);
      return;
    }
    
    // Check if we're over the prompt dropzone (skip further processing)
    if (over.id === DROP_ZONES.PROMPT) {
      // Don't set any drop target state for prompt area - this prevents any styling from being applied
      setActiveDropTarget(null);
      setDropTargetType(null);
      return;
    }
    
    // Check if we're over the publish dropzone
    if (over.id === DROP_ZONES.PUBLISHED) {
      if (dropTargetType !== DropTargetType.PUBLISH) {
        setDropTargetType(DropTargetType.PUBLISH);
      }
      return;
    }
    
    // Check if we're over a destination tab
    const overId = String(over.id);
    if (overId.startsWith(DROP_ZONES.TAB_PREFIX)) {
      console.log("BucketGridView handleDragOver: Dragging over tab", over.id);
      if (dropTargetType !== DropTargetType.TAB) {
        setDropTargetType(DropTargetType.TAB);
      }
      return;
    }
    
    // Check if we're over an empty favorites container
    if (typeof over.id === 'string' && (over.id as string).startsWith('empty-favorites-')) {
      // We're over an empty favorites container
      const overData = over.data?.current as any;
      
      if (active.id !== over.id) {
        if (activeDropTarget !== over.id) setActiveDropTarget(over.id as string);
        if (dropTargetType !== DropTargetType.REORDER) setDropTargetType(DropTargetType.REORDER);
        console.log('Dragging over empty favorites container');
      } else {
        if (activeDropTarget !== null) setActiveDropTarget(null);
        if (dropTargetType !== null) setDropTargetType(null);
      }
      return;
    }
    
    // Check if we're over an empty dated container
    if (typeof over.id === 'string' && (over.id as string).startsWith('empty-dated-')) {
      // We're over an empty dated container
      const overData = over.data?.current as any;
      
      if (active.id !== over.id) {
        if (activeDropTarget !== over.id) setActiveDropTarget(over.id as string);
        if (dropTargetType !== DropTargetType.REORDER) setDropTargetType(DropTargetType.REORDER);
        console.log('Dragging over empty dated container');
      } else {
        if (activeDropTarget !== null) setActiveDropTarget(null);
        if (dropTargetType !== null) setDropTargetType(null);
      }
      return;
    }
    
    // Check if we're over a section content area
    const overData = over.data?.current as any;
    const isContentArea = overData?.type === 'content-area';
    const isGroupTarget = typeof over.id === 'string' && (over.id as string).startsWith('group-');
    
    if (isGroupTarget && isContentArea) {
      // We're over a section content area
      // Get the section id from the over.id (remove 'group-' prefix)
      const sectionId = (over.id as string).slice(6);
      const sectionVariant = overData?.variant || sections.find(s => s.id === sectionId)?.variant;
      
      // Special handling for empty favorites section
      const isFavoritesSection = sectionVariant === 'favourites';
      const currentSection = sections.find(s => s.id === sectionId);
      const isEmptySection = currentSection && currentSection.images.length === 0;
      
      if (active.id !== over.id) {
        // Always set the active drop target to the section we're over
        if (activeDropTarget !== over.id) setActiveDropTarget(over.id as string);

        // Always set to REORDER for consistency, we'll handle special cases in handleDragEnd
        if (dropTargetType !== DropTargetType.REORDER) setDropTargetType(DropTargetType.REORDER);
        
        // Log more details about the drop target for debugging
        if (isFavoritesSection && isEmptySection) {
          console.log('Dragging over empty favorites section');
        }
      } else {
        if (activeDropTarget !== null) setActiveDropTarget(null);
        if (dropTargetType !== null) setDropTargetType(null);
      }
    } else if (active.id !== over.id) {
      // We're over an individual item that's not a section
      if (activeDropTarget !== over.id) setActiveDropTarget(over.id as string);
      if (dropTargetType !== DropTargetType.REORDER) setDropTargetType(DropTargetType.REORDER);
    } else {
      // Not a valid drop target
      if (activeDropTarget !== null) setActiveDropTarget(null);
      if (dropTargetType !== null) setDropTargetType(null);
    }
  };

  const toggleSection = (id: string) =>
    setCollapsedSections((prev) => ({ ...prev, [id]: !prev[id] }));

  const toggleSectionSort = (id: string) => {
    setSectionSortMap(prev => ({ ...prev, [id]: prev[id] === 'asc' ? 'desc' : 'asc' }));
  };

  // New function to handle deleting all images in a section
  const handleDeleteAllInSection = async (sectionId: string) => {
    const section = sections.find(s => s.id === sectionId);
    if (!section) return;
    
    // For dated sections, only count non-favorites
    const imagesToDelete = section.variant === 'dated' 
      ? section.images.filter(img => !img.metadata?.favorite)
      : section.images;
    
    if (imagesToDelete.length === 0) {
      toast.info('No images to delete in this section');
      return;
    }
    
    // Set the state for confirmation dialog
    setSectionToDelete(sectionId);
    setDeleteImagesCount(imagesToDelete.length);
    setDeleteDialogOpen(true);
  };
  
  // Function that will actually perform the delete operation after confirmation
  const confirmDeleteAllInSection = async () => {
    if (!sectionToDelete) return;
    
    const section = sections.find(s => s.id === sectionToDelete);
    if (!section) return;
    
    // Get images to delete - for dated sections, we skip favorites
    const imagesToDelete = section.variant === 'dated' 
      ? section.images.filter(img => !img.metadata?.favorite)
      : section.images;
    
    let deleteCount = 0;
    let failCount = 0;
    
    // Show a loading toast
    const loadingToast = toast.loading(`Deleting ${imagesToDelete.length} images...`);
    
    // Process deletions sequentially to avoid overwhelming the server
    for (const image of imagesToDelete) {
      try {
        await apiService.deleteImage(destination, image.id);
        deleteCount++;
      } catch (error) {
        console.error(`Error deleting image ${image.id}:`, error);
        failCount++;
      }
    }
    
    // Dismiss the loading toast
    toast.dismiss(loadingToast);
    
    // Show result toast
    if (failCount === 0) {
      toast.success(`Successfully deleted ${deleteCount} images`);
    } else {
      toast.error(`Deleted ${deleteCount} images, but failed to delete ${failCount} images`);
    }
    
    // Refresh the bucket to update the UI
    fetchBucketDetails();
  };

  /** Single section with droppable */
  const SectionDroppable: React.FC<{ section: Section }> = ({ section }) => {
    const { open: openLoope } = useLoopeView();
    // Create a droppable area for the content area
    const { setNodeRef: setContentNodeRef, isOver: isContentOver } = useDroppable({ 
      id: `${DROP_ZONES.SECTION_PREFIX}${section.id}`,
      data: {
        type: 'content-area',
        sectionId: section.id,
        variant: section.variant
      }
    });
    
    // Create a dedicated droppable for the empty favorites container
    const isEmptyFavorites = section.variant === 'favourites' && section.images.length === 0;
    const { setNodeRef: setEmptyFavoritesRef, isOver: isEmptyFavoritesOver } = useDroppable({
      id: `empty-favorites-${section.id}`,
      data: {
        type: 'empty-favorites',
        sectionId: section.id,
        variant: 'favourites'
      },
      disabled: !isEmptyFavorites // Only enable this droppable when the favorites section is empty
    });
    
    // Create a dedicated droppable for empty dated sections
    const isEmptyDated = section.variant === 'dated' && section.images.length === 0;
    const { setNodeRef: setEmptyDatedRef, isOver: isEmptyDatedOver } = useDroppable({
      id: `empty-dated-${section.id}`,
      data: {
        type: 'empty-dated',
        sectionId: section.id,
        variant: 'dated'
      },
      disabled: !isEmptyDated // Only enable this droppable when the dated section is empty
    });

    // Determine current images list based on sort direction for dated sections
    let sectionImages: ImageItem[] = section.images.map(toImageItem);
    if (section.variant === 'dated' && sectionImages.length > 1) {
      const dir = sectionSortMap[section.id] || 'desc';
      if (dir === 'asc') {
        sectionImages = [...sectionImages].reverse();
      }
    }

    // Build header extra toggle for dated sections
    const headerExtras = section.variant === 'dated' ? (
      <div
        role="button"
        className="flex items-center text-xs text-muted-foreground hover:text-foreground cursor-pointer"
        onClick={(e) => {
          e.stopPropagation();
          toggleSectionSort(section.id);
        }}
      >
        {sectionSortMap[section.id] === 'asc' ? (
          <>
            Oldest <ArrowDown className="h-3 w-3 ml-0.5" />
          </>
        ) : (
          <>
            Newest <ArrowUp className="h-3 w-3 ml-0.5" />
          </>
        )}
      </div>
    ) : null;

    // Create context menu items for the section
    const contextMenuItems = section.variant === 'dated' ? [
      {
        label: `Delete all [except favorites]`,
        onClick: () => handleDeleteAllInSection(section.id),
        icon: <Trash className="h-4 w-4" />,
        variant: 'destructive' as const
      }
    ] : [];

    return (
      <ExpandableContainer
        id={section.id}
        label={section.label}
        variant="section"
        iconPos="left"
        collapsed={collapsedSections[section.id] ?? false}
        onToggle={toggleSection}
        className=""
        headerExtras={headerExtras}
        showContextMenu={section.variant === 'dated' && section.images.length > 0}
        contextMenuItems={contextMenuItems}
      >
        {isEmptyFavorites ? (
          // Special handling for empty favorites container
          <div
            ref={setEmptyFavoritesRef}
            className={`min-h-[100px] ${isEmptyFavoritesOver ? 'ring-2 ring-primary' : ''}`}
            data-section-id={section.id}
            data-section-variant="favourites"
            data-empty-favorites="true"
          >
            <div className="flex items-center justify-center h-[100px] border-2 border-dashed border-muted-foreground/20 rounded-md">
              <div className="text-center text-muted-foreground">
                <Star className="h-6 w-6 mx-auto mb-2 opacity-30" />
                <p className="text-sm">Drop images here to favorite them</p>
              </div>
            </div>
          </div>
        ) : isEmptyDated ? (
          // Special handling for empty dated sections
          <div
            ref={setEmptyDatedRef}
            className={`min-h-[100px] ${isEmptyDatedOver ? 'ring-2 ring-primary' : ''}`}
            data-section-id={section.id}
            data-section-variant="dated"
            data-empty-dated="true"
          >
            <div className="flex items-center justify-center h-[100px] border-2 border-dashed border-muted-foreground/20 rounded-md">
              <div className="text-center text-muted-foreground">
                <StarOff className="h-6 w-6 mx-auto mb-2 opacity-30" />
                <p className="text-sm">Drop favorites here to unfavorite them</p>
              </div>
            </div>
          </div>
        ) : (
          // Normal content area for non-empty sections
          <div 
            ref={setContentNodeRef}
            className={isContentOver ? 'ring-2 ring-primary/60' : ''}
            data-section-id={section.id}
            data-section-variant={section.variant}
          >
            <SortableImageGrid
              images={sectionImages}
              sortable={section.variant === 'favourites'}
              onToggleFavorite={(img) => {
                const originalId = getOriginalId(img.id);
                const originalImage = bucketImages.find(i => i.id === originalId);
                if (originalImage) {
                  handleToggleFavorite(originalImage);
                }
              }}
              onImageClick={(img) => {
                const originalId = getOriginalId(img.id);
                const originalImage = bucketImages.find(i => i.id === originalId);
                if (originalImage) {
                  const items = section.images.map(toImageItem);
                  const idx = items.findIndex(it => it.id === img.id || getOriginalId(it.id)===originalId);
                  const sectionTitle = `${destinationName || destination} – ${section.label}`;
                  openLoope(items, idx === -1 ? 0 : idx, sectionTitle);
                }
              }}
              onDelete={(img) => {
                const originalId = getOriginalId(img.id);
                const originalImage = bucketImages.find(i => i.id === originalId);
                if (originalImage) {
                  handleDeleteImage(originalImage);
                }
              }}
              onCopyTo={(img, destId) => {
                const originalId = getOriginalId(img.id);
                const originalImage = bucketImages.find(i => i.id === originalId);
                if (originalImage && destId) {
                  handleCopyToDestination(originalImage, destId);
                }
              }}
              onPublish={(img, destId) => {
                const originalId = getOriginalId(img.id);
                const originalImage = bucketImages.find(i => i.id === originalId);
                if (originalImage && destId) {
                  handlePublish(destId, originalImage.id);
                }
              }}
              onUseAsPrompt={(img) => {
                const originalId = getOriginalId(img.id);
                const originalImage = bucketImages.find(i => i.id === originalId);
                if (originalImage) {
                  // Use the shared utility function to get the appropriate URL
                  const urlToUse = getReferenceUrl(originalImage);
                  
                  // Dispatch a custom event that the prompt form can listen for
                  // Add append=true parameter to add to existing images rather than replacing them
                  const event = new CustomEvent('useImageAsPrompt', { 
                    detail: { url: urlToUse, append: true }
                  });
                  window.dispatchEvent(event);
                }
              }}
              onGenerateAgain={(img) => {
                const originalId = getOriginalId(img.id);
                const originalImage = bucketImages.find(i => i.id === originalId);
                if (originalImage) {
                  handleGenerateAgain(originalImage);
                }
              }}
              publishDestinations={destinations
                .filter(d => !d.headless) // Filter out headless destinations
                .map(d => ({
                  id: d.id,
                  name: d.name,
                  headless: false // Already filtered out headless ones
                }))}
              bucketId={destination}
              sectionVariant={section.variant}
              onFullscreenClick={(img) => {
                const originalId = getOriginalId(img.id);
                const originalImage = bucketImages.find(i => i.id === originalId);
                if (originalImage) {
                  const items = section.images.map(toImageItem);
                  const idx = items.findIndex(it => it.id === img.id || getOriginalId(it.id)===originalId);
                  const sectionTitle = `${destinationName || destination} – ${section.label}`;
                  openLoope(items, idx === -1 ? 0 : idx, sectionTitle);
                }
              }}
            />
          </div>
        )}
      </ExpandableContainer>
    );
  };

  /* ------------------------------------------------------------------
     Global clean-up so mobile never gets stuck after a cancelled drag  
  ------------------------------------------------------------------ */
  useEffect(() => {
    const handleCancel = () => {
      setActiveId(null);
      setActiveDraggedImage(null);
    };

    window.addEventListener('pointercancel', handleCancel);
    window.addEventListener('pointerup', handleCancel);
    window.addEventListener('visibilitychange', () => {
      if (document.hidden) handleCancel();
    });

    return () => {
      window.removeEventListener('pointercancel', handleCancel);
      window.removeEventListener('pointerup', handleCancel);
      window.removeEventListener('visibilitychange', handleCancel);
    };
  }, []);

  // Set up initial collapsed state when sections change
  useEffect(() => {
    if (sections.length > 0) {
      // Create a map of all sections being collapsed by default
      const initialCollapsedState: Record<string, boolean> = {};
      
      // First, set all sections to collapsed
      sections.forEach(section => {
        initialCollapsedState[section.id] = true;
      });
      
      // Then, ensure Favorites is expanded if it exists
      if (sections.find(s => s.id === 'favourites')) {
        initialCollapsedState['favourites'] = false;
      }
      
      // Find the first non-favorites section and expand it
      const firstNonFavSection = sections.find(s => s.id !== 'favourites');
      if (firstNonFavSection) {
        initialCollapsedState[firstNonFavSection.id] = false;
      }
      
      // Only set the state if it's the first load or when sections actually change
      setCollapsedSections(prevState => {
        // Only update if we haven't set sections before
        if (Object.keys(prevState).length === 0) {
          return initialCollapsedState;
        }
        return prevState;
      });
    }
  }, [sections.length]);

  // Register global DnD callbacks within the outer DndContext
  useDndMonitor({
    onDragStart: handleDragStart,
    onDragEnd: (event) => {
      const { active, over } = event;
      
      // Check if we have a valid active and over
      if (!active || !over) {
        console.log("BucketGridView useDndMonitor.onDragEnd: No valid over target");
        setActiveId(null);
        setActiveDraggedImage(null);
        setActiveDropTarget(null);
        setDropTargetType(null);
        document.body.style.touchAction = 'auto';
        return;
      }
      
      console.log("BucketGridView useDndMonitor.onDragEnd:", { 
        activeId: active.id, 
        overId: over.id, 
        activeData: active.data?.current,
        overData: over.data?.current
      });
      
      // Early return for prompt area drops - let parent context handle this
      if (over.id === DROP_ZONES.PROMPT) {
        console.log("Skipping BucketGridView drag end handling for prompt area drops");
        setActiveId(null);
        setActiveDraggedImage(null);
        setActiveDropTarget(null);
        setDropTargetType(null);
        document.body.style.touchAction = 'auto';
        return;
      }
      
      // Early return for tabs - let parent context handle this
      const overId = String(over.id);
      if (overId.startsWith(DROP_ZONES.TAB_PREFIX)) {
        console.log("Skipping BucketGridView drag end handling for tab drops - over id:", over.id);
        setActiveId(null);
        setActiveDraggedImage(null);
        setActiveDropTarget(null);
        setDropTargetType(null);
        document.body.style.touchAction = 'auto';
        // We MUST return here to let the parent handle the tab drop
        return;
      }
      
      // Handle all other drops
      handleDragEnd(event);
    },
    onDragOver: handleDragOver,
  });

  // Create a PublishedImageDroppable component
  const PublishedImageDroppable = ({ currentImage }: { currentImage: BucketImage | null }) => {
    const { open: openLoope } = useLoopeView();
    const [isLoadingPublished, setIsLoadingPublished] = useState(false);
    
    // Create a droppable area for publishing
    const { setNodeRef, isOver } = useDroppable({
      id: DROP_ZONES.PUBLISHED,
      data: {
        type: DropTargetType.PUBLISH
      }
    });

    // Handle click to directly open Loope view with all published images
    const handleOpenLoopeView = useCallback(async () => {
      if (isLoadingPublished) return; // Prevent multiple clicks
      
      setIsLoadingPublished(true);
      try {
        // Get all destinations first
        const allDestinations = await apiService.getPublishDestinations();
        
        // Filter out headless destinations - they should not be part of published view
        const visibleDestinations = allDestinations.filter(dest => !dest.headless);
        
        const publishedImages: ImageItem[] = [];
        
        // Fetch published content for each destination sequentially
        for (const dest of visibleDestinations) {
          try {
            // Use direct API call to get full metadata
            const response = await apiService.getPublishedContentForDestination(dest.id);
            if (!response.published) continue;
            
            publishedImages.push({
              id: `${dest.id}:${response.published}`,
              urlFull: response.raw_url || '',
              urlThumb: response.thumbnail_url || response.raw_url || '',
              promptKey: response.meta?.prompt || '',
              seed: response.meta?.seed || 0,
              createdAt: response.publishedAt || new Date().toISOString(),
              isFavourite: false,
              mediaType: (response.raw_url || '').toLowerCase().match(/\.mp4|\.webm/) ? 'video' : 'image',
              bucketId: dest.id,
              destinationName: dest.name || dest.id,
              metadata: response.meta || {},
              isPublished: true,
              disableFavorite: true
            });
          } catch (error) {
            console.error(`Error fetching published content for ${dest.id}:`, error);
          }
        }
        
        // If we found any published images, open the Loope view
        if (publishedImages.length > 0) {
          // Find the index of the current destination
          const currentIndex = publishedImages.findIndex(img => img.bucketId === destination);
          const validIndex = currentIndex >= 0 ? currentIndex : 0;
          
          // Create title generator function for dynamic titles when swiping
          const getTitleForPublishedImage = (img: ImageItem, idx: number, total: number) => {
            return `Currently published - ${img.destinationName} (${idx + 1}/${total})`;
          };
          
          // Open Loope view with the initial title
          const initialTitle = getTitleForPublishedImage(
            publishedImages[validIndex], 
            validIndex, 
            publishedImages.length
          );
          
          // Now we can keep looping since we fixed the core issue
          const options = { loop: true };
          
          // Open the Loope view with the published images
          openLoope(publishedImages, validIndex, initialTitle, options);
        } else {
          toast.error('No published images found');
        }
      } catch (error) {
        console.error('Error fetching published images:', error);
        toast.error('Failed to load published images');
      } finally {
        setIsLoadingPublished(false);
      }
    }, [destination, openLoope, isLoadingPublished]);

    // If no current image, don't render a drop target
    if (!currentImage || headless) return null;

    // Just use the thumbnail_url directly
    const imageUrl = currentImage.thumbnail_url || '';

    return (
      <div 
        ref={setNodeRef}
        className={`relative w-24 h-24 rounded-md overflow-hidden bg-black/10 flex-shrink-0 transition-all
          ${isOver ? 'ring-2 ring-primary ring-offset-2 shadow-lg' : ''}
          ${activeId ? 'cursor-copy' : 'cursor-pointer'}
        `}
        onClick={handleOpenLoopeView}
        title="Click to view all published images"
      >
        {isLoadingPublished && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/30 z-10">
            <RefreshCw className="h-5 w-5 text-white animate-spin" />
          </div>
        )}
        
        {imageUrl ? (
          <img 
            src={imageUrl} 
            alt="Published" 
            className={`w-full h-full object-cover ${isOver ? 'opacity-70' : ''}`}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center bg-muted">
            <ImageIcon className="h-10 w-10 text-muted-foreground" />
          </div>
        )}
        
        {/* Overlay that appears when dragging over */}
        {isOver && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/50 text-white">
            <Send className="h-6 w-6 mb-1" />
            <span className="text-xs font-medium">Publish</span>
          </div>
        )}
        
        {/* Helper label that shows when dragging any item */}
        {activeId && !isOver && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/30 text-white text-opacity-70">
            <span className="text-xs">Drop to publish</span>
          </div>
        )}
        
        {/* Small indicator to show it's clickable when not dragging */}
        {!activeId && (
          <div className="absolute bottom-1 right-1 bg-black/50 rounded-full p-0.5">
            <Maximize2 className="h-3 w-3 text-white" />
          </div>
        )}
      </div>
    );
  };

  // Properly handle different view states in the main content area based on bucket properties
  const renderContent = () => {
    // Show loading spinner if loading
    if (loading || externalLoading) {
      return (
        <div className="flex justify-center items-center h-full">
          <RefreshCw className="h-8 w-8 animate-spin opacity-50" />
        </div>
      );
    }
    
    // For destinations without buckets, return empty content since the published image is already shown in the top panel
    if (!hasBucket) {
      return null;
    }
    
    // For destinations with buckets but no images
    if (sections.length === 0) {
      return (
        <div className="flex flex-col justify-center items-center h-full p-6">
          <ImageIcon className="h-12 w-12 mb-4 opacity-20" />
          <p className="text-lg font-medium mb-2">No images found</p>
          <p className="text-sm text-muted-foreground mb-4">
            Upload some images to get started.
          </p>
          <Button variant="secondary" onClick={() => setShowUploadModal(true)}>
            <Upload className="h-4 w-4 mr-2" />
            Upload Images
          </Button>
        </div>
      );
    }
    
    // For destinations with buckets and images
    return (
      <div className="flex flex-col gap-2 overflow-y-auto items-start">
        {sections.map((section) => (
          <SectionDroppable key={section.id} section={section} />
        ))}
      </div>
    );
  };

  // Add mask toggle handler
  const handleMaskToggle = async (enabled: boolean) => {
    try {
      if (enabled) {
        await api.enableMask(destination);
      } else {
        await api.disableMask(destination);
      }
      setMaskEnabled(enabled);
    } catch (error) {
      console.error('Error toggling mask:', error);
      toast.error('Failed to toggle mask');
    }
  };

  const checkAppVersion = async () => {
    const APK_PATH = '/build/sdk/app-release.apk';
    const STORAGE_KEY = 'screenmachine_app_version';
    
    try {
      // Get the current version info from localStorage
      const storedVersion = localStorage.getItem(STORAGE_KEY);
      const currentVersion = storedVersion ? JSON.parse(storedVersion) : null;
      
      // If no stored version exists, we should show download modal
      if (!currentVersion) {
        setIsNewVersion(false);
        return true;
      }
      
      // Get the server's file info
      const response = await fetch(APK_PATH, { method: 'HEAD' });
      const lastModified = response.headers.get('last-modified');
      
      if (!lastModified) {
        console.error('Could not get last modified date for APK');
        setIsNewVersion(false);
        return true; // Default to showing download if we can't check
      }
      
      const serverTimestamp = new Date(lastModified).getTime();
      
      // If we have a stored version and it's older than the server version
      if (currentVersion.timestamp < serverTimestamp) {
        setIsNewVersion(true);
        return true; // New version available
      }
      
      setIsNewVersion(false);
      return false; // No new version needed
    } catch (error) {
      console.error('Error checking app version:', error);
      setIsNewVersion(false);
      return true; // Default to showing download on error
    }
  };

  const handleRecordClick = async () => {
    if (isIOS) {
      toast.error('iOS support coming soon');
      return;
    }

    // First check if we need to update
    const needsUpdate = await checkAppVersion();
    if (needsUpdate) {
      setShowAppDownloadModal(true);
      return;
    }

    // If no update needed, try to deeplink
    const appUrl = `${import.meta.env.VITE_URL}/app?target=${destination}&recording=${isTranscribing ? 'stop' : 'start'}`;
    
    // Create a fallback URL that will trigger the download modal
    const fallbackUrl = `${window.location.origin}${window.location.pathname}?show_download=true`;
    
    // Try to open the app using Android Intent URL scheme with fallback
    const intentUrl = `intent://${appUrl.replace('http://', '')}#Intent;scheme=http;package=com.screenmachine.audio;S.browser_fallback_url=${encodeURIComponent(fallbackUrl)};end`;
    
    // Try to open the app
    window.location.href = intentUrl;
  };

  // Check URL parameters on mount
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('show_download') === 'true') {
      setShowAppDownloadModal(true);
      // Clean up the URL
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  // Poll transcription status instead of using WebSocket
  useEffect(() => {
    let pollInterval: NodeJS.Timeout | null = null;

    const pollTranscriptionStatus = async () => {
      try {
        const status = await getTranscriptionStatus();
        console.log('Transcription status:', status); // Debug log
        // Update recording state based on API response
        const isRecording = status.is_recording[destination] || false;
        console.log('Is recording:', isRecording, 'destination:', destination); // Debug log
        setIsTranscribing(isRecording);
      } catch (error) {
        console.error('Error polling transcription status:', error);
      }
    };

    // Initial poll
    pollTranscriptionStatus();

    // Set up polling interval
    pollInterval = setInterval(pollTranscriptionStatus, 10000); // Poll every 10 seconds
    
    return () => {
      if (pollInterval) {
        clearInterval(pollInterval);
      }
    };
  }, [destination]);

  // Get the base URL from VITE_API_URL, strip trailing /api if present
  const apiUrl = import.meta.env.VITE_API_URL;
  const baseUrl = apiUrl.endsWith('/api') ? apiUrl.slice(0, -4) : apiUrl;
  const sdkUrl = `${baseUrl}/sdk/app-release.apk`;

  const storeVersionInfo = async () => {
    const APK_PATH = '/build/sdk/app-release.apk';
    const STORAGE_KEY = 'screenmachine_app_version';
    
    try {
      // Get the server's file info
      const response = await fetch(APK_PATH, { method: 'HEAD' });
      const lastModified = response.headers.get('last-modified');
      
      if (lastModified) {
        const timestamp = new Date(lastModified).getTime();
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
          timestamp,
          path: APK_PATH
        }));
      }
    } catch (error) {
      console.error('Error storing version info:', error);
    }
  };

  // Listen for new images event to automatically refresh when images are generated
  useEffect(() => {
    const handleRecentAdd = (e: any) => {
      try {
        const { batchId, files, imagesWithMetadata } = e.detail || {};
        if (!files || files.length === 0) return;
        
        // Only refresh if this bucket is the destination for the generated images
        // Check if any of the generated images have this bucket as their destination
        if (imagesWithMetadata && imagesWithMetadata.length > 0) {
          const hasImagesForThisBucket = imagesWithMetadata.some((img: any) => {
            // Check if the image metadata indicates it was published to this bucket
            return img.metadata?.publish_destination === destination || 
                   img.metadata?.bucket_id === destination;
          });
          
          if (hasImagesForThisBucket) {
            console.log(`[BucketGridView] Detected new images for bucket ${destination}, refreshing...`);
            // Refresh the bucket to show the new images with reference image info
            refreshBucket(destination);
          }
        }
      } catch (err) {
        console.warn('BucketGridView:recent:add handler error', err);
      }
    };
    
    window.addEventListener('recent:add', handleRecentAdd as EventListener);
    console.log(`[BucketGridView] Added listener for recent:add events for bucket ${destination}`);
    
    return () => window.removeEventListener('recent:add', handleRecentAdd as EventListener);
  }, [destination, refreshBucket]);

  return (
    <div className="h-full flex flex-col">
      {/* DnD interactions handled by global context; monitor via hooks */}
      {bucketDetails && (
        <>
          <MaintenanceModal
            isOpen={showMaintenanceModal}
            onClose={() => setShowMaintenanceModal(false)}
            destination={destination}
            onActionComplete={debouncedFetchBucketDetails}
          />
          <div className={`flex flex-col mb-0 p-2 sm:p-4 bg-muted rounded-md w-full ${headerPinned ? 'sticky top-12 z-40' : ''}`}>
            <div className="flex items-start gap-3">
              {/* Left side with droppable published image area */}
              <PublishedImageDroppable currentImage={currentPublishedImage} />
              
              {/* Controls area */}
              <div className="flex-1 flex flex-col min-w-0 h-full">
                {/* All publish destinations use same layout */}
                <div className="flex justify-between items-center">
                  <div className="flex flex-wrap gap-1 items-center">
                    <Button 
                      size="sm" 
                      variant="ghost" 
                      onClick={handleRefresh} 
                      disabled={loading || externalLoading}
                      className="flex-nowrap h-8"
                    >
                      <RefreshCw 
                        className={`h-4 w-4 ${(loading || externalLoading) ? 'animate-spin' : ''}`} 
                      />
                      <span className="ml-1 hidden sm:inline">Refresh</span>
                    </Button>
                    {/* Only show upload/maintenance for destinations with buckets */}
                    {hasBucket && (
                      <>
                        <Button 
                          size="sm" 
                          variant="secondary"
                          onClick={() => setShowUploadModal(true)}
                          className="flex-nowrap h-8"
                        >
                          <Upload className="h-4 w-4" />
                          <span className="ml-1 hidden sm:inline">Upload</span>
                        </Button>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button size="sm" variant="outline" className="flex-nowrap h-8">
                              <Settings className="h-4 w-4" />
                              <span className="ml-1 hidden sm:inline">Maintenance</span>
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent>
                            <DropdownMenuItem onClick={handlePurgeNonFavorites}>
                              <Trash2 className="h-4 w-4 mr-2" />
                              Purge Non-Favorites
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={handleReindex}>
                              <RefreshCw className="h-4 w-4 mr-2" />
                              Re-Index
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={handleExtractJson}>
                              <Copy className="h-4 w-4 mr-2" />
                              Extract JSON
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </>
                    )}
                  </div>

                  {/* Add mask toggle and record button */}
                  <div className="flex items-center gap-2">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          size="sm"
                          variant={maskEnabled ? "default" : "outline"}
                          onClick={() => handleMaskToggle(!maskEnabled)}
                          className="h-8 px-2 sm:px-3"
                        >
                          <Moon className="h-4 w-4" />
                          <span className="ml-1 hidden sm:inline">Mask</span>
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">
                        {maskEnabled ? "Disable mask" : "Enable mask"}
                      </TooltipContent>
                    </Tooltip>

                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          size="sm"
                          variant={isTranscribing ? "default" : "outline"}
                          onClick={handleRecordClick}
                          className="h-8 px-2 sm:px-3"
                        >
                          <Mic className={`h-4 w-4 ${isTranscribing ? 'animate-pulse' : ''}`} />
                          <span className="ml-1 hidden sm:inline">
                            {isTranscribing ? "Stop Recording" : "Start Recording"}
                          </span>
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">
                        {isTranscribing ? "Stop recording" : "Start recording"}
                      </TooltipContent>
                    </Tooltip>
                  </div>
                </div>
                
                {/* Create a flex spacer that pushes the controls to the bottom */}
                <div className="flex-grow"></div>
                
                {/* Replace the inline scheduler controls with the SchedulerControl component */}
                <SchedulerControl 
                  destination={destination}
                  isRunning={schedulerStatus?.is_running}
                  isPaused={schedulerStatus?.is_paused}
                  nextAction={schedulerStatus?.next_action}
                  refreshScheduler={refreshBucket}
                />
              </div>
            </div>
          </div>
        </>
      )}
      
      {/* Error message */}
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      
      {/* Content area with proper separation of concerns */}
      {renderContent()}
      
      {/* Tabbed upload modal */}
      <Dialog open={showUploadModal} onOpenChange={setShowUploadModal}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Upload to {destinationName || destination}</DialogTitle>
          </DialogHeader>
          
          <Tabs defaultValue="file" value={uploadTab} onValueChange={handleUploadTabChange}>
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="file" className="flex items-center justify-center">
                <Upload className="h-4 w-4 mr-2" />
                <span>File</span>
              </TabsTrigger>
              <TabsTrigger value="url" className="flex items-center justify-center">
                <Link className="h-4 w-4 mr-2" />
                <span>URL</span>
              </TabsTrigger>
              <TabsTrigger value="camera" disabled={!hasCamera} className="flex items-center justify-center">
                <Camera className="h-4 w-4 mr-2" />
                <span>Camera</span>
              </TabsTrigger>
            </TabsList>
            
            <TabsContent value="file" className="pt-4">
              <div className="grid gap-4">
                <Input
                  id="file-upload"
                  type="file"
                  className="w-full"
                  onChange={(e) => {
                    if (e.target.files && e.target.files[0]) {
                      setUploadFile(e.target.files[0]);
                    }
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  Select a file from your device to upload.
                </p>
              </div>
            </TabsContent>
            
            <TabsContent value="url" className="pt-4">
              <div className="grid gap-4">
                <Input
                  id="url-upload"
                  type="url"
                  placeholder="https://example.com/image.jpg"
                  value={uploadUrl}
                  onChange={(e) => setUploadUrl(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Enter the URL of an image to upload from the web.
                </p>
              </div>
            </TabsContent>
            
            <TabsContent value="camera" className="pt-4">
              <div className="grid gap-4">
                <div className="bg-black aspect-video rounded-md flex items-center justify-center">
                  <p className="text-white">Camera access coming soon</p>
                </div>
                <p className="text-xs text-muted-foreground">
                  Take a photo using your device's camera.
                </p>
              </div>
            </TabsContent>
          </Tabs>
          
          <DialogFooter className="mt-4">
            <Button variant="outline" onClick={() => setShowUploadModal(false)}>
              Cancel
            </Button>
            <Button type="submit" onClick={handleUpload}>
              Upload
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      
      {/* Image detail dialog */}
      <Dialog open={showImageDetail} onOpenChange={setShowImageDetail}>
        <DialogContent className="max-w-4xl">
          <DialogHeader>
            <DialogTitle>Image Details</DialogTitle>
          </DialogHeader>
          
          {selectedImage && (
            <div className="flex flex-col gap-4">
              <div className="bg-muted rounded-md overflow-hidden">
                <img
                  src={selectedImage.url}
                  alt={selectedImage.prompt || 'Selected image'}
                  className="w-full h-auto object-contain max-h-[70vh]"
                />
              </div>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  {selectedImage.prompt && (
                    <>
                      <h3 className="text-sm font-medium mb-1">Prompt</h3>
                      <p className="text-sm whitespace-pre-wrap mb-3">{selectedImage.prompt}</p>
                    </>
                  )}
                  
                  {selectedImage.created_at && (
                    <>
                      <h3 className="text-sm font-medium mb-1">Created</h3>
                      <p className="text-sm mb-3">
                        {new Date(selectedImage.created_at * 1000).toLocaleString()}
                      </p>
                    </>
                  )}
                </div>
                
                <div className="flex flex-col gap-2">
                  <Button 
                    variant="secondary" 
                    className="w-full justify-start"
                    onClick={() => handleToggleFavorite(selectedImage)}
                  >
                    {selectedImage.metadata?.favorite ? (
                      <>
                        <StarOff className="h-4 w-4 mr-2" />
                        Remove from Favorites
                      </>
                    ) : (
                      <>
                        <Star className="h-4 w-4 mr-2" />
                        Add to Favorites
                      </>
                    )}
                  </Button>
                  
                  <Button 
                    variant="secondary" 
                    className="w-full justify-start"
                    onClick={() => handlePublish(destination, selectedImage.id)}
                  >
                    <Send className="h-4 w-4 mr-2" />
                    Publish Image
                  </Button>
                  
                  <Button 
                    variant="secondary" 
                    className="w-full justify-start"
                    onClick={() => window.open(selectedImage.url, '_blank')}
                  >
                    <ExternalLink className="h-4 w-4 mr-2" />
                    Open Full Size
                  </Button>
                  
                  <Button 
                    variant="destructive" 
                    className="w-full justify-start"
                    onClick={() => {
                      handleDeleteImage(selectedImage);
                      setShowImageDetail(false);
                    }}
                  >
                    <Trash className="h-4 w-4 mr-2" />
                    Delete Image
                  </Button>
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Drag overlay with scale and shadow */}
      <DragOverlay adjustScale={false}>
        {activeDraggedImage ? (
          <div
            className="rounded-md overflow-hidden shadow-xl transform-gpu scale-105"
            style={{ opacity: dropTargetType === DropTargetType.REORDER ? 1 : 0.6 }}
          >
            <img
              src={activeDraggedImage.thumbnail_url || activeDraggedImage.url}
              alt="drag preview"
              className="w-full h-full object-cover" />
          </div>
        ) : null}
      </DragOverlay>

      {/* Confirmation dialogs */}
      <ConfirmDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={confirmDeleteAllInSection}
        title="Delete Images"
        description={`Are you sure you want to delete ${deleteImagesCount} images? This cannot be undone.`}
        confirmLabel="Delete"
      />

      {/* App Download Modal */}
      <Dialog open={showAppDownloadModal} onOpenChange={setShowAppDownloadModal}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{isNewVersion ? "New Version Available" : "Install Screen Machine"}</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col items-center gap-4 py-4">
            <p className="text-center text-muted-foreground">
              {isNewVersion 
                ? "A new version of Screen Machine is available. Please download and install the latest version to continue."
                : "To use the recording feature, please install the Screen Machine app."
              }
            </p>
            <div className="flex flex-col items-center gap-4">
              <Button 
                onClick={() => {
                  window.location.href = sdkUrl;
                  // Store version info after successful download
                  storeVersionInfo();
                }}
                className="flex items-center gap-2"
              >
                <Download className="h-4 w-4" />
                Download SDK
              </Button>
              {isNewVersion && (
                <Button 
                  variant="outline"
                  onClick={() => {
                    setShowAppDownloadModal(false);
                    // Try to open the app with current version
                    const appUrl = `${import.meta.env.VITE_URL}/app?target=${destination}&recording=${isTranscribing ? 'stop' : 'start'}`;
                    const intentUrl = `intent://${appUrl.replace('http://', '')}#Intent;scheme=http;package=com.screenmachine.audio;S.browser_fallback_url=${encodeURIComponent(appUrl)};end`;
                    window.location.href = intentUrl;
                  }}
                >
                  Continue with current version
                </Button>
              )}
              <p className="text-sm text-muted-foreground">
                Play Store availability coming soon
              </p>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}; 
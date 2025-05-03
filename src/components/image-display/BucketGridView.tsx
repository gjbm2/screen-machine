import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { format, formatDistance } from 'date-fns';
import apiService from '@/utils/api';
import { Input } from '@/components/ui/input';
import { BucketItem as ApiBucketItem, Bucket as ApiBucket } from '@/utils/api';
import { Image as ImageIcon, RefreshCw, AlertCircle, Star, StarOff, Upload, MoreVertical, Trash, Share, Plus, ChevronsUpDown, Settings, ExternalLink, Send, Trash2, Copy, Info, Filter, Film, Camera, Link, CirclePause, CirclePlay, CircleStop } from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert';
import { BucketImage } from './BucketImage';
import { BucketDetails } from './BucketDetails';
import { UploadModal } from './UploadModal';
import { MaintenanceModal } from './MaintenanceModal';

// Define the expected types based on the API response
interface BucketItem extends ApiBucketItem {}

interface Bucket extends ApiBucket {}

interface BucketImage {
  id: string;
  url: string;
  thumbnail_url?: string;
  thumbnail_embedded?: string;
  prompt?: string;
  metadata?: Record<string, any>;
}

interface BucketDetails {
  name: string;
  count: number;
  favorites_count: number;
  size_mb: number;
  last_modified: string;
  published?: string;
  published_at?: string;
}

interface BucketGridViewProps {
  destination: string;
  destinationName?: string;
  onImageClick?: (image: BucketImage) => void;
  refreshBucket: (bucket: string) => void;
  isLoading: boolean;
  schedulerStatus?: { is_running: boolean; is_paused: boolean };
  headless?: boolean;
  icon?: string;
}

export const BucketGridView = ({
  destination,
  destinationName,
  onImageClick,
  refreshBucket,
  isLoading: externalLoading,
  schedulerStatus,
  headless = false,
  icon
}: BucketGridViewProps) => {
  const [bucketImages, setBucketImages] = useState<BucketImage[]>([]);
  const [bucketDetails, setBucketDetails] = useState<BucketDetails | null>(null);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [showMaintenanceModal, setShowMaintenanceModal] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadUrl, setUploadUrl] = useState('');
  const [currentPublishedImage, setCurrentPublishedImage] = useState<BucketImage | null>(null);
  const [destinations, setDestinations] = useState<{id: string, name: string}[]>([]);
  const [showFavoritesFirst, setShowFavoritesFirst] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadTab, setUploadTab] = useState<'file' | 'url'>('file');
  const [selectedImage, setSelectedImage] = useState<BucketImage | null>(null);
  const [showImageDetail, setShowImageDetail] = useState(false);
  const [refreshTimeout, setRefreshTimeout] = useState<NodeJS.Timeout | null>(null);

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

  const fetchDestinations = async () => {
    try {
      const buckets = await apiService.getPublishDestinations();
      setDestinations(buckets.map(bucket => ({
        id: bucket.id,
        name: bucket.name || bucket.id
      })));
    } catch (error) {
      console.error('Error fetching destinations:', error);
      toast.error('Failed to fetch destinations');
    }
  };

  const fetchBucketDetails = async () => {
    setLoading(true);
    setError(null);
    try {
      const details = await apiService.getBucketDetails(destination);
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
        }
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
        published_at: details.published_at
      });

      if (details.published) {
        const publishedImage = sortedImages.find(img => img.id === details.published);
        if (publishedImage) {
          setCurrentPublishedImage(publishedImage);
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

  const handleImageClick = (image: BucketImage) => {
    if (onImageClick) {
      onImageClick(image);
    } else {
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
          prevImages.map(img => 
            img.id === image.id 
              ? { ...img, metadata: { ...img.metadata, favorite: newFavoriteState } }
              : img
          )
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
    if (confirm('Are you sure you want to delete this image?')) {
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
    }
  };

  const handlePublish = async (bucket: string, filename: string) => {
    try {
      const success = await apiService.publishImage({
        publish_destination_id: bucket,
        source: `/api/buckets/${bucket}/raw/${filename}`
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
      }
    } catch (error) {
      console.error('Error publishing image:', error);
      toast.error('Failed to publish image');
    }
  };

  const handleCopyToDestination = async (image: BucketImage, targetBucket: string) => {
    try {
      const success = await apiService.copyImageToBucket(destination, targetBucket, image.id);
      if (success) {
        toast.success('Image copied successfully');
      }
    } catch (error) {
      console.error('Error copying image:', error);
      toast.error('Failed to copy image');
    }
  };

  // Handle scheduler actions
  const handleSchedulerAction = async (action: 'start' | 'stop' | 'pause' | 'unpause') => {
    try {
      switch (action) {
        case 'start':
          await apiService.startScheduler(destination, {});
          break;
        case 'stop':
          await apiService.stopScheduler(destination);
          break;
        case 'pause':
          await apiService.pauseScheduler(destination);
          break;
        case 'unpause':
          await apiService.unpauseScheduler(destination);
          break;
      }
      toast.success(`Scheduler ${action}ed successfully`);
      debouncedFetchBucketDetails();
      refreshBucket(destination);
    } catch (error) {
      console.error(`Error ${action}ing scheduler:`, error);
      toast.error(`Failed to ${action} scheduler`);
    }
  };

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

  // Check if an image is a video (could be based on file extension)
  const isVideo = (filename: string) => {
    const videoExtensions = ['.mp4', '.avi', '.mov', '.webm', '.mkv'];
    return videoExtensions.some(ext => filename.toLowerCase().endsWith(ext));
  };

  const sortedImages = getSortedImages();
  const favoritesCount = bucketImages.filter(img => img.metadata?.favorite).length;
  
  // Get scheduler status icon and text
  const getSchedulerStatusDisplay = () => {
    if (schedulerStatus.is_running && !schedulerStatus.is_paused) {
      return {
        icon: <CirclePlay className="h-5 w-5 text-green-500 animate-pulse" />,
        text: "Running",
        colorClass: "text-green-500"
      };
    } else if (schedulerStatus.is_running && schedulerStatus.is_paused) {
      return {
        icon: <CirclePause className="h-5 w-5 text-amber-500" />,
        text: "Paused",
        colorClass: "text-amber-500"
      };
    } else {
      return {
        icon: <CircleStop className="h-5 w-5 text-gray-400" />,
        text: "Stopped",
        colorClass: "text-gray-400"
      };
    }
  };

  const statusDisplay = getSchedulerStatusDisplay();
  const hasCamera = 'mediaDevices' in navigator && 'getUserMedia' in navigator.mediaDevices;

  // Handle maintenance actions
  const handlePurgeNonFavorites = async () => {
    if (confirm('Are you sure you want to purge non-favorite images?')) {
      try {
        const success = await apiService.performBucketMaintenance(destination, 'purge');
        if (success) {
          debouncedFetchBucketDetails();
        }
      } catch (error) {
        console.error('Error purging non-favorites:', error);
        toast.error('Failed to purge non-favorites');
      }
    }
  };

  const handleReindex = async () => {
    try {
      const success = await apiService.performBucketMaintenance(destination, 'reindex');
      if (success) {
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
        debouncedFetchBucketDetails();
      }
    } catch (error) {
      console.error('Error extracting JSON:', error);
      toast.error('Failed to extract JSON');
    }
  };

  // Add moveToPosition method for reordering
  const moveToPosition = async (filename: string, position: number) => {
    try {
      const success = await apiService.moveImage(destination, filename, position > 0 ? 'up' : 'down');
      if (success) {
        debouncedFetchBucketDetails();
      }
    } catch (error) {
      console.error('Error moving image:', error);
      toast.error('Failed to move image');
    }
  };

  // Drag and drop handlers
  const handleDragStart = (event: React.DragEvent, image: BucketImage, currentIndex: number) => {
    event.dataTransfer.setData('application/json', JSON.stringify({
      id: image.id,
      currentIndex
    }));
    event.dataTransfer.effectAllowed = 'move';
  };

  const handleDragOver = (event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  };

  const handleDrop = (event: React.DragEvent, targetIndex: number) => {
    event.preventDefault();
    try {
      const data = JSON.parse(event.dataTransfer.getData('application/json'));
      const sourceIndex = data.currentIndex;
      const imageId = data.id;
      
      if (sourceIndex !== targetIndex) {
        moveToPosition(imageId, targetIndex);
      }
    } catch (err) {
      console.error('Error processing drop:', err);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Enhanced bucket header panel with triple height */}
      {bucketDetails && (
        <div className="flex flex-col mb-4 p-2 sm:p-4 bg-muted rounded-md w-full">
          {/* Single row with image, controls and scheduler status */}
          <div className="flex items-start gap-3">
            {/* Left side with image */}
            {currentPublishedImage && !headless && (
              <div className="w-24 h-24 rounded-md overflow-hidden bg-black/10 flex-shrink-0">
                <img 
                  src={currentPublishedImage.thumbnail_url || currentPublishedImage.url} 
                  alt="Published" 
                  className="w-full h-full object-cover"
                />
              </div>
            )}
            
            {/* Middle and right content */}
            <div className="flex-1 flex flex-col min-w-0">
              {/* Button row with all controls on same line */}
              <div className="flex justify-between items-center mt-1">
                {/* Left side buttons */}
                <div className="flex flex-wrap gap-1 items-center">
                  {/* Star button with counts */}
                  <Button
                    variant={showFavoritesFirst ? "secondary" : "outline"}
                    size="sm"
                    className="flex items-center h-8"
                    onClick={() => setShowFavoritesFirst(!showFavoritesFirst)}
                  >
                    <Star className={`h-4 w-4 ${showFavoritesFirst ? "fill-current" : ""}`} />
                    <span className="ml-1 text-xs">{bucketDetails.favorites_count} / {bucketDetails.count}</span>
                  </Button>
                  
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
                      {!headless && (
                        <DropdownMenuItem onClick={handleOpenSchedulerPage}>
                          <Settings className="h-4 w-4 mr-2" />
                          Scheduler
                        </DropdownMenuItem>
                      )}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
                
                {/* Scheduler controls on same line */}
                {!headless && (
                  <div className="flex items-center gap-1 ml-2">
                    <div className={`flex items-center ${statusDisplay.colorClass}`}>
                      {statusDisplay.icon}
                      <span className="text-sm ml-1">{statusDisplay.text}</span>
                    </div>
                    
                    <div className="flex items-center ml-1 space-x-1">
                      {!schedulerStatus.is_running && (
                        <Button 
                          variant="outline" 
                          size="xs"
                          onClick={() => handleSchedulerAction('start')}
                          className="h-6 px-2 text-xs"
                        >
                          Start
                        </Button>
                      )}
                      
                      {schedulerStatus.is_running && !schedulerStatus.is_paused && (
                        <Button 
                          variant="outline" 
                          size="xs"
                          onClick={() => handleSchedulerAction('pause')}
                          className="h-6 px-2 text-xs"
                        >
                          Pause
                        </Button>
                      )}
                      
                      {schedulerStatus.is_running && schedulerStatus.is_paused && (
                        <Button 
                          variant="outline" 
                          size="xs"
                          onClick={() => handleSchedulerAction('unpause')}
                          className="h-6 px-2 text-xs"
                        >
                          Resume
                        </Button>
                      )}
                      
                      {schedulerStatus.is_running && (
                        <Button 
                          variant="outline" 
                          size="xs"
                          onClick={() => handleSchedulerAction('stop')}
                          className="h-6 px-2 text-xs"
                        >
                          Stop
                        </Button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
      
      {/* Error message */}
      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      
      {/* Image grid */}
      {loading || externalLoading ? (
        <div className="flex justify-center items-center h-full">
          <RefreshCw className="h-8 w-8 animate-spin opacity-50" />
        </div>
      ) : bucketImages.length === 0 ? (
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
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4 overflow-y-auto">
          {sortedImages.map((image, index) => (
            <Tooltip key={image.id}>
              <TooltipTrigger asChild>
                <Card 
                  className="group relative overflow-hidden cursor-pointer border-transparent hover:border-primary transition-all"
                  onClick={() => handleImageClick(image)}
                  draggable
                  onDragStart={(e) => handleDragStart(e, image, index)}
                  onDragOver={handleDragOver}
                  onDrop={(e) => handleDrop(e, index)}
                >
                  {/* Always visible favorite star button */}
                  <Button
                    variant="ghost" 
                    size="icon" 
                    className="absolute top-2 right-2 z-10 h-6 w-6 bg-black/20 hover:bg-black/40 text-white"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleToggleFavorite(image);
                    }}
                  >
                    {image.metadata?.favorite ? 
                      <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" /> : 
                      <Star className="h-4 w-4" />
                    }
                  </Button>
                  
                  {/* Video indicator for video files */}
                  {isVideo(image.id) && (
                    <Badge className="absolute top-2 left-2 z-10 bg-blue-500">
                      <Film className="h-3 w-3" />
                    </Badge>
                  )}
                  
                  {/* Image thumbnail */}
                  <div className="aspect-square overflow-hidden bg-muted">
                    {image.thumbnail_embedded ? (
                      <img
                        src={`data:image/jpeg;base64,${image.thumbnail_embedded}`}
                        alt={image.prompt || 'Image in bucket'}
                        className="w-full h-full object-cover transition-all group-hover:scale-105"
                      />
                    ) : (
                      <img
                        src={image.thumbnail_url || 'https://placehold.co/400x400?text=Loading...'}
                        alt={image.prompt || 'Image in bucket'}
                        className="w-full h-full object-cover transition-all group-hover:scale-105"
                        onError={(e) => {
                          const target = e.target as HTMLImageElement;
                          if (image.url) {
                            target.src = image.url;
                          } else {
                            target.src = 'https://placehold.co/400x400?text=Error+loading+image';
                          }
                        }}
                      />
                    )}
                  </div>
                  
                  {/* Overlay with actions */}
                  <div className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col justify-between p-3">
                    <div className="text-white text-xs break-words whitespace-pre-wrap line-clamp-3">
                      {image.prompt || 'No prompt available'}
                    </div>
                    
                    <div className="flex justify-between items-center mt-2">
                      <div className="text-white text-xs">
                        {image.metadata?.timestamp ? new Date(image.metadata.timestamp).toLocaleDateString() : 'No date'}
                      </div>
                      
                      <div className="flex gap-1">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost" 
                              size="icon" 
                              className="h-6 w-6 bg-black/20 hover:bg-black/40 text-white"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <MoreVertical className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent>
                            <DropdownMenuItem
                              onClick={(e) => {
                                e.stopPropagation();
                                handlePublish(destination, image.id);
                              }}
                            >
                              <Send className="h-4 w-4 mr-2" />
                              Publish
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={(e) => {
                                e.stopPropagation();
                                window.open(image.url, '_blank');
                              }}
                            >
                              <ExternalLink className="h-4 w-4 mr-2" />
                              Open Full Size
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={(e) => {
                                e.stopPropagation();
                                handleDeleteImage(image);
                              }}
                            >
                              <Trash className="h-4 w-4 mr-2" />
                              Delete
                            </DropdownMenuItem>
                            
                            {/* Add to other destinations submenu */}
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <div className="flex items-center px-2 py-1.5 text-sm cursor-pointer hover:bg-accent">
                                  <Copy className="h-4 w-4 mr-2" />
                                  <span>Add to...</span>
                                </div>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent side="right">
                                {destinations.map(dest => (
                                  <DropdownMenuItem
                                    key={dest.id}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      handleCopyToDestination(image, dest.id);
                                    }}
                                  >
                                    {dest.name}
                                  </DropdownMenuItem>
                                ))}
                              </DropdownMenuContent>
                            </DropdownMenu>
                            
                            {/* Move To position dialog */}
                            <DropdownMenuItem
                              onClick={(e) => {
                                e.stopPropagation();
                                const position = prompt(`Enter position (0-${bucketImages.length - 1}):`, index.toString());
                                if (position !== null) {
                                  const posNum = parseInt(position, 10);
                                  if (!isNaN(posNum) && posNum >= 0 && posNum < bucketImages.length) {
                                    moveToPosition(image.id, posNum);
                                  } else {
                                    toast.error(`Invalid position. Must be between 0 and ${bucketImages.length - 1}`);
                                  }
                                }
                              }}
                            >
                              <ChevronsUpDown className="h-4 w-4 mr-2" />
                              Move To Position
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </div>
                  </div>
                </Card>
              </TooltipTrigger>
              <TooltipContent>
                <div className="max-w-md">
                  {/* Don't show full filename */}
                  {image.prompt && (
                    <div className="text-sm mb-1">
                      {image.prompt}
                    </div>
                  )}
                  {image.metadata?.timestamp && (
                    <div className="text-xs text-muted-foreground">
                      Created {formatPublishDate(image.metadata.timestamp)}
                    </div>
                  )}
                </div>
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
      )}
      
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
                  
                  {selectedImage.metadata?.timestamp && (
                    <>
                      <h3 className="text-sm font-medium mb-1">Created</h3>
                      <p className="text-sm mb-3">
                        {new Date(selectedImage.metadata.timestamp).toLocaleString()}
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
    </div>
  );
} 
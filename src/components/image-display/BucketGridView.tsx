import React, { useState, useEffect } from 'react';
import { Image as ImageIcon, RefreshCw, AlertCircle, Star, StarOff, Upload, MoreVertical, Trash, Share, Plus, ChevronsUpDown, Settings, ExternalLink, Send, Trash2, Copy, Info, Filter, Film, Camera, Link, CirclePause, CirclePlay, CircleStop } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { getPublishDestinations } from '@/services/PublishService';
import { format, formatDistance } from 'date-fns';
import * as LucideIcons from 'lucide-react';

// Define the expected types based on the API response
interface BucketItem {
  filename: string;
  url?: string;
  thumbnail?: string;
  prompt?: string;
  isFavorite?: boolean;
  timestamp?: string;
}

interface Bucket {
  name: string;
  items?: BucketItem[];
  error?: string;
  size_mb?: number;
  last_modified?: string;
  published?: string;
  publishedAt?: string;
}

interface BucketImage {
  id: string;
  url: string;
  thumbnail?: string;
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
  publishedAt?: string;
}

interface BucketGridViewProps {
  destination: string;  // This is the file/directory name used for API calls
  destinationName?: string; // This is the display name shown in the UI
  onImageClick?: (image: BucketImage) => void;
  refreshBucket: (bucket: string) => void;
  isLoading: boolean;
  schedulerStatus?: { is_running: boolean; is_paused: boolean };
  headless?: boolean; // Add headless property
  icon?: string; // Add icon property
}

export function BucketGridView({ 
  destination, 
  destinationName,
  onImageClick, 
  refreshBucket,
  isLoading,
  schedulerStatus = { is_running: false, is_paused: false },
  headless = false, // Default to false
  icon = 'image' // Default icon
}: BucketGridViewProps) {
  const [bucketDetails, setBucketDetails] = useState<BucketDetails | null>(null);
  const [bucketImages, setBucketImages] = useState<BucketImage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedImage, setSelectedImage] = useState<BucketImage | null>(null);
  const [showImageDetail, setShowImageDetail] = useState(false);
  const [showFavoritesFirst, setShowFavoritesFirst] = useState(true);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [uploadUrl, setUploadUrl] = useState('');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadTab, setUploadTab] = useState('file'); // file, url, camera
  const [currentPublishedImage, setCurrentPublishedImage] = useState<BucketImage | null>(null);

  // Use the destinationName in the UI if provided, otherwise fall back to the destination
  const displayName = destinationName || destination;

  useEffect(() => {
    fetchBucketDetails();
  }, [destination]);

  const fetchBucketDetails = async () => {
    if (!destination) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const details = await apiService.fetchBucketDetails(destination);
      
      if (details && 'error' in details) {
        setError(details.error as string);
        setBucketImages([]);
        setBucketDetails(null);
        return;
      }
      
      // Try to get the current published image directly from API
      let publishedImage = await fetchPublishedImage();
      
      // Transform the data to match our expected format
      const bucketImages = details.items?.map((item: BucketItem) => ({
        id: item.filename,
        url: item.url || `${apiService.getApiUrl()}/bucket/${destination}/${item.filename}`,
        thumbnail: item.thumbnail || `${apiService.getApiUrl()}/bucket/${destination}/${item.filename}/thumbnail`,
        prompt: item.prompt || '',
        metadata: {
          favorite: item.isFavorite || false,
          timestamp: item.timestamp || '',
        }
      })) || [];
      
      // Sort by favorite status first, then by timestamp (newest first)
      bucketImages.sort((a, b) => {
        if ((a.metadata?.favorite && b.metadata?.favorite) || (!a.metadata?.favorite && !b.metadata?.favorite)) {
          return (b.metadata?.timestamp || '') > (a.metadata?.timestamp || '') ? 1 : -1;
        }
        return a.metadata?.favorite ? -1 : 1;
      });
      
      setBucketImages(bucketImages);
      
      // If we found a published image from the API, try to enrich it with any additional metadata from bucketImages
      if (publishedImage) {
        const matchingImage = bucketImages.find(img => img.id === publishedImage.id);
        if (matchingImage) {
          publishedImage = { ...publishedImage, ...matchingImage };
        }
      }
      
      // Set bucket details
      setBucketDetails({
        name: destination,
        count: bucketImages.length,
        favorites_count: bucketImages.filter(img => img.metadata?.favorite).length,
        size_mb: 'size_mb' in details ? Number(details.size_mb) : 0,
        last_modified: 'last_modified' in details ? details.last_modified as string : '',
        published: details.published,
        publishedAt: details.publishedAt,
      });

      // Manually set currentPublishedImage if we have it
      if (publishedImage) {
        setCurrentPublishedImage(publishedImage);
      } else if (details.published) {
        // Fall back to finding the image in bucketImages
        setCurrentPublishedImage(bucketImages.find(img => img.id === details.published) || null);
      } else {
        setCurrentPublishedImage(null);
      }
    } catch (err) {
      console.error('Error fetching bucket details:', err);
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
      const currentState = image.metadata?.favorite || false;
      await apiService.toggleFavorite(destination, image.id, currentState);
      
      // Update local state
      setBucketImages(prevImages => 
        prevImages.map(img => 
          img.id === image.id 
            ? { ...img, metadata: { ...img.metadata, favorite: !currentState } } 
            : img
        )
      );

      if (bucketDetails) {
        setBucketDetails({
          ...bucketDetails,
          favorites_count: currentState 
            ? bucketDetails.favorites_count - 1 
            : bucketDetails.favorites_count + 1
        });
      }
      
      toast.success(`Image ${currentState ? 'removed from' : 'added to'} favorites`);
    } catch (err) {
      console.error('Error toggling favorite:', err);
      toast.error('Failed to update favorite status');
    }
  };

  const handleDeleteImage = async (image: BucketImage) => {
    // Only show confirmation for favorited images
    const shouldProceed = !image.metadata?.favorite || 
      window.confirm('Are you sure you want to delete this favorited image?');

    if (shouldProceed) {
      try {
        const success = await apiService.deleteImage(destination, image.id);
        
        if (success) {
          // Update local state
          setBucketImages(prevImages => prevImages.filter(img => img.id !== image.id));
          
          if (bucketDetails) {
            setBucketDetails({
              ...bucketDetails,
              count: bucketDetails.count - 1,
              favorites_count: image.metadata?.favorite 
                ? bucketDetails.favorites_count - 1 
                : bucketDetails.favorites_count
            });
          }
          
          toast.success('Image deleted');
          
          // If we deleted the current published image, update that state
          if (currentPublishedImage && currentPublishedImage.id === image.id) {
            setCurrentPublishedImage(null);
          }
        }
      } catch (err) {
        console.error('Error deleting image:', err);
        toast.error('Failed to delete image');
      }
    }
  };

  const handlePublishImage = async (image: BucketImage) => {
    try {
      await apiService.publishBucketImage(destination, image.id);
      
      // Update bucket details to reflect the new published image
      if (bucketDetails) {
        setBucketDetails({
          ...bucketDetails,
          published: image.id,
          publishedAt: new Date().toISOString()
        });
      }
      
      toast.success('Image published successfully');
    } catch (err) {
      console.error('Error publishing image:', err);
      toast.error('Failed to publish image');
    }
  };

  const handleCopyToDestination = async (image: BucketImage, targetBucket: string) => {
    try {
      await apiService.copyImageToBucket(destination, targetBucket, image.id);
      toast.success(`Image copied to ${targetBucket}`);
    } catch (err) {
      console.error('Error copying image:', err);
      toast.error('Failed to copy image');
    }
  };

  // Get the published image data
  const fetchPublishedImage = async () => {
    try {
      console.log(`Fetching published image for ${destination} (display: ${displayName})`);
      
      // First try the /info endpoint which has more fields
      try {
        const publishedInfo = await apiService.getPublishedInfo(destination);
        console.log("Got published info:", publishedInfo);
        
        if (publishedInfo && publishedInfo.filename) {
          console.log("Found published image for", destination, publishedInfo);
          return {
            id: publishedInfo.filename,
            thumbnail: publishedInfo.thumbnail_url || `${apiService.getApiUrl()}/buckets/${destination}/thumbnail/${publishedInfo.filename}`,
            url: publishedInfo.raw_url || `${apiService.getApiUrl()}/buckets/${destination}/raw/${publishedInfo.filename}`,
            prompt: publishedInfo.meta?.prompt || '',
            // Add any other properties you need
          };
        }
      } catch (infoError) {
        console.log("Info endpoint failed, trying published endpoint:", infoError);
      }
      
      // Fall back to the /published endpoint
      const publishedData = await fetch(`${apiService.getApiUrl()}/buckets/${destination}/published`);
      
      if (!publishedData.ok) {
        throw new Error(`Failed to get published data for ${destination}`);
      }
      
      const data = await publishedData.json();
      
      if (data && data.published) {
        console.log("Found published data via /published endpoint:", data);
        return {
          id: data.published,
          thumbnail: data.thumbnail ? `data:image/jpeg;base64,${data.thumbnail}` : null,
          url: data.raw_url,
          prompt: data.meta?.prompt || '',
        };
      }
    } catch (error) {
      console.error(`Error fetching published image for ${destination}:`, error);
    }
    return null;
  };

  // Handle scheduler actions
  const handleSchedulerAction = async (action: 'start' | 'stop' | 'pause' | 'unpause') => {
    try {
      let result;
      switch (action) {
        case 'start':
          result = await apiService.startScheduler(destination, {});
          toast.success(`Started scheduler for ${destination}`);
          break;
        case 'stop':
          result = await apiService.stopScheduler(destination);
          toast.success(`Stopped scheduler for ${destination}`);
          break;
        case 'pause':
          result = await apiService.pauseScheduler(destination);
          toast.success(`Paused scheduler for ${destination}`);
          break;
        case 'unpause':
          result = await apiService.unpauseScheduler(destination);
          toast.success(`Resumed scheduler for ${destination}`);
          break;
      }
      refreshBucket(destination); // Refresh to update UI
    } catch (error) {
      console.error(`Error with scheduler action ${action}:`, error);
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

  // Get all possible destination buckets for copy feature
  const getAllDestinations = () => {
    return getPublishDestinations().filter(dest => dest.id !== destination);
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

  const handleUpload = async () => {
    if (!uploadFile && !uploadUrl) {
      toast.error('Please select a file or enter a URL');
      return;
    }

    try {
      if (uploadFile) {
        await apiService.uploadToBucket(destination, uploadFile);
        toast.success('File uploaded successfully');
      } else if (uploadUrl) {
        // This would require backend endpoint support
        // For now, we'll show a toast indicating this isn't implemented
        toast.error('URL upload not yet implemented on backend');
      }
      
      // Reset state and refresh bucket
      setUploadFile(null);
      setUploadUrl('');
      setShowUploadModal(false);
      fetchBucketDetails();
      refreshBucket(destination);
    } catch (err) {
      console.error('Error uploading:', err);
      toast.error('Failed to upload file');
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
    try {
      if (window.confirm('Are you sure you want to delete all non-favorite images? This cannot be undone.')) {
        await apiService.purgeNonFavorites(destination);
        toast.success('Purged non-favorite images');
        fetchBucketDetails();
        refreshBucket(destination);
      }
    } catch (err) {
      console.error('Error purging non-favorites:', err);
      toast.error('Failed to purge non-favorite images');
    }
  };

  const handleReindex = async () => {
    try {
      await apiService.reindexBucket(destination);
      toast.success('Bucket re-indexed');
      fetchBucketDetails();
      refreshBucket(destination);
    } catch (err) {
      console.error('Error re-indexing bucket:', err);
      toast.error('Failed to re-index bucket');
    }
  };

  const handleExtractJson = async () => {
    try {
      await apiService.extractJson(destination);
      toast.success('JSON extracted');
      fetchBucketDetails();
      refreshBucket(destination);
    } catch (err) {
      console.error('Error extracting JSON:', err);
      toast.error('Failed to extract JSON');
    }
  };

  // Add moveToPosition method for reordering
  const moveToPosition = async (filename: string, position: number) => {
    try {
      if (position < 0 || position >= bucketImages.length) {
        throw new Error('Invalid position');
      }
      
      await apiService.moveImageToPosition(destination, filename, position);
      toast.success('Image moved successfully');
      fetchBucketDetails();
      refreshBucket(destination);
    } catch (err) {
      console.error('Error moving image:', err);
      toast.error('Failed to move image');
    }
  };

  // Get icon component based on icon name
  const renderIcon = () => {
    // Just use the Image icon for now as a fallback
    return <ImageIcon className="h-5 w-5" />;
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
        <div className="flex flex-col mb-4 p-4 bg-muted rounded-md space-y-3">
          <div className="flex justify-between items-start">
            {/* Left side: Bucket info and current image thumbnail */}
            <div className="flex items-start gap-4">
              {/* Current published image thumbnail - hide if headless */}
              {currentPublishedImage && !headless && (
                <div className="w-24 h-24 rounded-md overflow-hidden bg-black/10 flex-shrink-0">
                  <img 
                    src={currentPublishedImage.thumbnail || currentPublishedImage.url} 
                    alt="Published" 
                    className="w-full h-full object-cover"
                  />
                </div>
              )}
              
              <div className="flex flex-col space-y-1">
                {/* Published info with friendly date - hide if headless */}
                {bucketDetails.published && !headless && (
                  <div className="text-sm text-muted-foreground">
                    <span className="font-medium">Published:</span> {bucketDetails.published}
                    {bucketDetails.publishedAt && (
                      <span className="text-xs ml-2 text-muted-foreground">
                        ({formatPublishDate(bucketDetails.publishedAt)})
                      </span>
                    )}
                  </div>
                )}
                
                {/* Show metadata about current published image - hide if headless */}
                {currentPublishedImage && currentPublishedImage.prompt && !headless && (
                  <div className="text-sm text-muted-foreground max-w-md truncate">
                    <span className="font-medium">Prompt:</span> {currentPublishedImage.prompt}
                  </div>
                )}
                
                {/* Scheduler status and mini controls - hide if headless */}
                {!headless && (
                  <div className="flex items-center gap-2 mt-1">
                    <div className={`flex items-center ${statusDisplay.colorClass}`}>
                      {statusDisplay.icon}
                      <span className="text-sm ml-1">{statusDisplay.text}</span>
                    </div>
                    
                    <div className="flex items-center ml-4 space-x-1">
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
            
            {/* Right side: Actions */}
            <div className="flex items-center gap-2">
              {/* Star button with counts */}
              <Button
                variant={showFavoritesFirst ? "secondary" : "outline"}
                size="sm"
                className="flex items-center"
                onClick={() => setShowFavoritesFirst(!showFavoritesFirst)}
              >
                <Star className={`h-4 w-4 ${showFavoritesFirst ? "fill-current" : ""}`} />
                <span className="ml-1 text-xs">{bucketDetails.favorites_count} / {bucketDetails.count}</span>
              </Button>
              
              <Button 
                size="sm" 
                variant="ghost" 
                onClick={handleRefresh} 
                disabled={loading || isLoading}
                className="flex-nowrap"
              >
                <RefreshCw 
                  className={`h-4 w-4 ${(loading || isLoading) ? 'animate-spin' : ''}`} 
                />
                <span className="ml-1 hidden sm:inline">Refresh</span>
              </Button>
              
              <Button 
                size="sm" 
                variant="secondary"
                onClick={() => setShowUploadModal(true)}
                className="flex-nowrap"
              >
                <Upload className="h-4 w-4" />
                <span className="ml-1 hidden sm:inline">Upload</span>
              </Button>
              
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="sm" variant="outline" className="flex-nowrap">
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
                  <DropdownMenuItem onClick={handleOpenSchedulerPage}>
                    <Settings className="h-4 w-4 mr-2" />
                    Scheduler
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
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
      {loading || isLoading ? (
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
                    <img
                      src={image.thumbnail || image.url}
                      alt={image.prompt || 'Image in bucket'}
                      className="w-full h-full object-cover transition-all group-hover:scale-105"
                      onError={(e) => {
                        const target = e.target as HTMLImageElement;
                        target.src = 'https://placehold.co/400x400?text=Error+loading+image';
                      }}
                    />
                  </div>
                  
                  {/* Overlay with actions */}
                  <div className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col justify-between p-3">
                    <div className="text-white text-xs truncate">
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
                                handlePublishImage(image);
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
                                {getAllDestinations().map(dest => (
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
            <DialogTitle>Upload to {displayName}</DialogTitle>
          </DialogHeader>
          
          <Tabs defaultValue="file" value={uploadTab} onValueChange={setUploadTab}>
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
                    onClick={() => handlePublishImage(selectedImage)}
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
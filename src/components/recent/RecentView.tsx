import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { SortableContext, arrayMove, rectSortingStrategy, useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { DragStartEvent, DragEndEvent, useDndMonitor, DragOverlay } from '@dnd-kit/core';
import { ImageItem } from '@/types/image-types';
import { useLoopeView } from '@/contexts/LoopeViewContext';
import styles from './recent.module.css';
import React from 'react';
import RecentBatchPanel from './RecentBatchPanel';
import { ReferenceImageService } from '@/services/reference-image-service';

// Add type declarations for window functions
declare global {
  interface Window {
    generateImage: (params: {
      prompt: string;
      batch_id: string;
      workflow?: string;
      [key: string]: any;
    }) => Promise<void>;
    addImageReferenceToPrompt: (sourceUrl: string, bucketId: string, imageId: string) => void;
  }
}

// Interface for the cleaned-up bucket image
interface BucketImage {
  id: string;
  url: string;
  thumbnail_url?: string;
  thumbnail_embedded?: string;
  prompt?: string;
  metadata?: Record<string, any>;
  created_at?: number;
  raw_url?: string;
  batchId?: string; // Added for grouping
  reference_images?: any[]; // Added for reference images
  bucketId?: string; // Bucket where this image is stored
}

interface RecentViewProps {
  refreshRecent?: () => void;
}

// Define interface for placeholder tracking
interface PlaceholderInfo {
  id: string;
  timestamp: number;
}

export const RecentView: React.FC<RecentViewProps> = ({
  refreshRecent,
}) => {
  const [bucketImages, setBucketImages] = useState<BucketImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Batch grouping and ordering
  const [batchGroups, setBatchGroups] = useState<{ batchId: string, images: ImageItem[] }[]>([]);
  const [batchOrder, setBatchOrder] = useState<string[]>([]);
  
  // Panel collapse state
  const [collapsedPanels, setCollapsedPanels] = useState<Record<string, boolean>>({});
  
  // For Loope integration
  let openLoope;
  try {
    const loopeContext = useLoopeView();
    openLoope = loopeContext.open;
  } catch (error) {
    console.warn("Loope view context not available:", error);
    openLoope = () => {
      console.warn("Loope view can't be opened - context not available");
    };
  }
  
  // Destinations for copy/move operations
  const [destinations, setDestinations] = useState<{id: string, name: string, headless: boolean, has_bucket?: boolean}[]>([]);
  
  // Ref for interval/timeout cleanup
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  
  // Placeholders state
  const [placeholders, setPlaceholders] = useState<PlaceholderInfo[]>([]);
  
  // Selection Architecture:
  // 1. Each batch panel manages its own selection state independently
  // 2. Parent component only receives notifications, not controls selections
  // 3. No two-way binding between parent and child components

  // Map of batchId -> selected image ID (for persistence only)
  const [selectedImageByBatch, setSelectedImageByBatch] = useState<Record<string,string>>({});
  
  // Set to track the last few placeholder batch events to detect duplicates
  const recentPlaceholderEvents = useRef<{batchId: string, timestamp: number}[]>([]).current;
  
  // At the beginning of the component, add a debounce function and ref
  const selectionUpdateTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  
  // Create a minimal selection handler that just stores the value without affecting other components
  const handleSelectImage = useCallback((batchId: string, imageId: string) => {
    // Persist selection to localStorage only – avoid triggering parent re-renders
    try {
      // Read existing selections from localStorage (fallback to empty object)
      const stored = localStorage.getItem('recentTabSelections');
      const current: Record<string, string> = stored ? JSON.parse(stored) : {};
      
      // Short-circuit if nothing changed
      if (current[batchId] === imageId) return;
      
      // Update & save
      current[batchId] = imageId;
      localStorage.setItem('recentTabSelections', JSON.stringify(current));
    } catch (error) {
      console.error('Error saving selection:', error);
    }
  }, []);
  
  // Add a useRef to track initialization
  const initializedRef = useRef(false);

  // Add this effect near the top to ensure clean one-time initialization
  useEffect(() => {
    // Only run initialization code once
    if (initializedRef.current) return;
    
    // Load saved state
    try {
      // Load selection state
      const savedSelections = localStorage.getItem('recentTabSelections');
      if (savedSelections) {
        try {
          setSelectedImageByBatch(JSON.parse(savedSelections));
        } catch (e) {
          console.error('Error parsing saved selections', e);
        }
      }
      
      // Load panel state
      const savedState = localStorage.getItem('recentTabState');
      if (savedState) {
        try {
          const state = JSON.parse(savedState);
          if (state.batchOrder) setBatchOrder(state.batchOrder);
          if (state.collapsedPanels) setCollapsedPanels(state.collapsedPanels);
        } catch (e) {
          console.error('Error parsing saved state', e);
        }
      }
    } catch (e) {
      // Ignore any localStorage errors
    }
    
    // Mark as initialized
    initializedRef.current = true;
  }, []);
  
  // Load images initially and start polling for updates
  useEffect(() => {
    // Initial load
    fetchRecentImages();
    fetchDestinations();
    
    // Polling has been disabled as it was causing issues
    // Clean up function is no longer needed since we're not setting up an interval
  }, []);
  
  // Save state to localStorage on any state change
  useEffect(() => {
    saveStateToStorage();
  }, [batchOrder, collapsedPanels]);
  
  // Group images by batchId whenever the bucket images change
  useEffect(() => {
    updateBatchGroups();
  }, [bucketImages]);
  
  // Fetch bucket destinations for copy/move operations
  const fetchDestinations = async () => {
    try {
      const buckets = await apiService.getPublishDestinations();
      
      // Filter out hidden destinations like _recent
      const visibleDestinations = buckets.filter(dest => !dest.headless && dest.has_bucket);
      
      setDestinations(visibleDestinations.map(bucket => ({
        id: bucket.id,
        name: bucket.name || bucket.id,
        headless: bucket.headless || false,
        has_bucket: bucket.has_bucket || false
      })));
    } catch (error) {
      console.error('Error fetching destinations:', error);
      toast.error('Failed to fetch destinations');
    }
  };
  
  // Add this function to load active placeholders from localStorage at the top of the component
  const loadActivePlaceholdersFromStorage = (): PlaceholderInfo[] => {
    try {
      const storedPlaceholders = localStorage.getItem('activePlaceholders');
      if (storedPlaceholders) {
        const parsed = JSON.parse(storedPlaceholders);
        // Removed verbose logging to reduce console noise
        return parsed;
      }
    } catch (error) {
      console.error('[recent] Error loading active placeholders from localStorage:', error);
    }
    return [];
  };

  // Add this function to clean up expired placeholders in localStorage
  const cleanupExpiredPlaceholders = () => {
    try {
      const storedPlaceholders = localStorage.getItem('activePlaceholders');
      if (storedPlaceholders) {
        const parsed = JSON.parse(storedPlaceholders);
        const now = Date.now();
        const expireTime = 10 * 60 * 1000; // 10 minutes
        
        const activePlaceholders = parsed.filter((p: any) => (now - p.timestamp) < expireTime);
        
        if (activePlaceholders.length !== parsed.length) {
          localStorage.setItem('activePlaceholders', JSON.stringify(activePlaceholders));
        }
      }
    } catch (error) {
      console.error('[recent] Error cleaning up expired placeholders:', error);
    }
  };

  // Update the fetchRecentImages function to preserve selection state
  const fetchRecentImages = async (showLoading = true) => {
    if (showLoading) {
      setLoading(true);
    }
    setError(null);
    
    // Get current selection state to preserve it
    const currentSelections = {...selectedImageByBatch};
    
    try {
      const details = await apiService.getBucketDetails('_recent');
      if (details.error) {
        throw new Error(details.error);
      }
      
      // Map bucket items to our internal format
      const images = details.items.map((item: any) => {
        return {
          id: item.filename,
          url: item.url || '',
          thumbnail_url: item.thumbnail_url,
          thumbnail_embedded: item.thumbnail_embedded,
          prompt: item.metadata?.prompt,
          metadata: {
            ...item.metadata,
            favorite: item.favorite
          },
          created_at: item.created_at || item.metadata?.timestamp || 0,
          raw_url: item.raw_url,
          // Extract batchId from filename (e.g., "2024-05-14T12-30-00_batch-42_0.png")
          batchId: extractBatchId(item.filename),
          // Set bucketId to _recent since this function specifically fetches from _recent
          bucketId: '_recent',
          // Copy reference images from API response
          reference_images: item.reference_images || []
        };
      });
      
      // Get active placeholders from localStorage to ensure they survive polling
      const activePlaceholders = loadActivePlaceholdersFromStorage();
      
      setBucketImages(prev => {
        // Get all current placeholders that should be preserved
        const currentPlaceholders = prev.filter(img => 
          img.id.startsWith('placeholder-') && img.metadata?.placeholder
        );
        
        // Filter to keep only placeholders that are still active or not in the localStorage list
        const relevantPlaceholders = currentPlaceholders.filter(placeholder => {
          // Check if this placeholder is in our active list from localStorage
          return activePlaceholders.some((active: any) => active.id === placeholder.id);
        });
        

        
        // Create a Map to deduplicate images by ID
        const uniqueImages = new Map<string, BucketImage>();
        
        // First add placeholders to ensure they take precedence
        relevantPlaceholders.forEach(img => uniqueImages.set(img.id, img));
        
        // Then add API images, which will override placeholders with the same ID
        images.forEach(img => uniqueImages.set(img.id, img));
        
        return Array.from(uniqueImages.values());
      });
      
      // Handle selection state preservation
      // We want to keep selections for batches that still exist
      const newBatchIds = new Set(images.map(img => img.batchId));
      const updatedSelections: Record<string, string> = {};
      
      // Keep only selections for batches that still exist
      Object.entries(currentSelections).forEach(([batchId, imageId]) => {
        if (newBatchIds.has(batchId)) {
          updatedSelections[batchId] = imageId;
        }
      });
      
      // Only update if there are changes
      if (Object.keys(updatedSelections).length !== Object.keys(selectedImageByBatch).length) {
        setSelectedImageByBatch(updatedSelections);
      }
    } catch (error) {
      console.error('Error fetching recent images:', error);
      if (showLoading) {
        toast.error('Failed to fetch recent images');
        setError('Failed to fetch recent images');
      }
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  };
  
  // Helper to extract batchId from filename
  const extractBatchId = (filename: string): string => {
    // Extract "batch-XYZ" pattern from the filename - capture everything after "batch-" until the next file extension
    const batchMatch = filename.match(/batch-([^\.]+)/);
    if (batchMatch && batchMatch[1]) {
      return batchMatch[1]; // Return the full batch ID value including underscores
    }
    
    // Fallback if pattern not found: use filename hash
    return filename.split('.')[0];
  };
  
  // Group images by batchId
  const updateBatchGroups = () => {
    const groupMap = new Map<string, BucketImage[]>();
    
    // Group images by batchId
    bucketImages.forEach(img => {
      const batchId = img.batchId || 'unknown';
      if (!groupMap.has(batchId)) {
        groupMap.set(batchId, []);
      }
      groupMap.get(batchId)!.push(img);
    });
    
    // Sort images within each batch by index/timestamp
    groupMap.forEach((images, batchId) => {
      images.sort((a, b) => {
        // Extract sequence number from filename if possible
        const aSeq = extractSequenceNumber(a.id) || 0;
        const bSeq = extractSequenceNumber(b.id) || 0;
        
        // Primary sort by sequence number
        if (aSeq !== bSeq) return aSeq - bSeq;
        
        // Secondary sort by timestamp
        return (a.created_at || 0) - (b.created_at || 0);
      });
    });
    
    // Convert to ImageItems for the components
    const groups = Array.from(groupMap.entries()).map(([batchId, images]) => ({
      batchId,
      images: images.map(toImageItem)
    }));
    
    // Sort groups by newest first (based on first image timestamp)
    groups.sort((a, b) => {
      const aTime = a.images[0]?.createdAt ? new Date(a.images[0].createdAt).getTime() : 0;
      const bTime = b.images[0]?.createdAt ? new Date(b.images[0].createdAt).getTime() : 0;
      return bTime - aTime;
    });
    
    // If we have a saved order, use that instead
    const newBatchIds = groups.map(g => g.batchId);
    if (batchOrder.length > 0) {
      // Determine the correct order: first from saved order if they still exist, 
      // then any new batches at the top
      const existingBatches = batchOrder.filter(id => newBatchIds.includes(id));
      const newBatches = newBatchIds.filter(id => !batchOrder.includes(id));
      const updatedOrder = [...newBatches, ...existingBatches];
      
      // Only update if there's a difference
      if (JSON.stringify(updatedOrder) !== JSON.stringify(batchOrder)) {
        setBatchOrder(updatedOrder);
      }
      
      // Sort groups according to our order
      groups.sort((a, b) => {
        const aIndex = updatedOrder.indexOf(a.batchId);
        const bIndex = updatedOrder.indexOf(b.batchId);
        return aIndex - bIndex;
      });
    } else {
      // Initialize order if empty
      setBatchOrder(newBatchIds);
    }
    
    setBatchGroups(groups);

    // If first load (no batchOrder yet), set initial collapsed panels: newest open
    if (batchOrder.length === 0 && groups.length > 0) {
      const firstId = groups[0].batchId;
      const initialCollapse: Record<string, boolean> = {};
      // Force ALL panels to start collapsed
      groups.forEach(g => { initialCollapse[g.batchId] = true; });
      setCollapsedPanels(initialCollapse);
    }
  };
  
  // Helper to extract sequence number from filename (e.g., "2024-05-14T12-30-00_batch-42_0.png" → 0)
  const extractSequenceNumber = (filename: string): number | null => {
    const parts = filename.split('_');
    if (parts.length >= 3) {
      const lastPart = parts[parts.length - 1];
      // Extract number before file extension
      const numMatch = lastPart.match(/^(\d+)/);
      if (numMatch) {
        return parseInt(numMatch[1], 10);
      }
    }
    return null;
  };
  
  // Convert BucketImage to ImageItem for components
  const toImageItem = (img: BucketImage): ImageItem => {
    const mediaType: 'image' | 'video' = isVideoFile(img.id) ? 'video' : 'image';
    
    const imageItem: ImageItem = {
      id: img.id,
      // Ensure the key for this item is truly unique by using both id and batchId
      uniqueKey: `${img.id}_${img.batchId || 'unknown'}`,
      urlThumb: img.thumbnail_url || img.thumbnail_embedded || '',
      urlFull: img.url || '',
      promptKey: img.prompt || '',
      seed: 0,
      createdAt: img.created_at ? new Date(img.created_at * 1000).toISOString() : new Date().toISOString(),
      isFavourite: false, // Always false in Recent
      mediaType,
      raw_url: img.raw_url || img.url || '',
      metadata: img.metadata || {},
      bucketId: img.bucketId || '_recent', // Use actual bucket ID, fallback to _recent for backward compatibility
      // Copy reference images to ImageItem
      reference_images: img.reference_images || []
    };
    
    return imageItem;
  };
  
  // Helper to check if a file is video
  const isVideoFile = (filename: string): boolean => {
    const videoExtensions = ['.mp4', '.webm', '.mov', '.avi'];
    return videoExtensions.some(ext => filename.toLowerCase().endsWith(ext));
  };
  
  // Save current state to localStorage
  const saveStateToStorage = () => {
    try {
      const state = {
        batchOrder,
        collapsedPanels,
      };
      localStorage.setItem('recentTabState', JSON.stringify(state));
    } catch (error) {
      console.error('Error saving recent tab state:', error);
    }
  };
  
  // Handle deleting an image
  const handleDeleteImage = async (image: ImageItem) => {
    try {
      const bucketId = image.bucketId || '_recent';
      await apiService.deleteImage(bucketId, image.id);
      toast.success('Image deleted');
      
      // Optimistically update UI
      setBucketImages(prev => prev.filter(img => img.id !== image.id));
      
      // If this was the last image in a batch, clean up the batch
      const batch = batchGroups.find(b => b.batchId === extractBatchId(image.id));
      if (batch && batch.images.length <= 1) {
        // This was the last image in the batch, so we need to update the batch order
        setBatchOrder(prev => prev.filter(id => id !== batch.batchId));
      }
    } catch (error) {
      console.error('Error deleting image:', error);
      toast.error('Failed to delete image');
      fetchRecentImages(); // Refresh to ensure UI is in sync
    }
  };
  
  // Handle deleting an entire batch
  const handleDeleteBatch = async (batchId: string) => {
    try {
      // Get all images in this batch
      const batch = batchGroups.find(b => b.batchId === batchId);
      if (!batch) return;
      
      // Delete each image
      const deletePromises = batch.images.map(img => {
        const bucketId = img.bucketId || '_recent';
        return apiService.deleteImage(bucketId, img.id);
      });
      
      await Promise.all(deletePromises);
      
      toast.success('Batch deleted');
      
      // Optimistically update UI
      setBucketImages(prev => prev.filter(img => extractBatchId(img.id) !== batchId));
      setBatchOrder(prev => prev.filter(id => id !== batchId));
    } catch (error) {
      console.error('Error deleting batch:', error);
      toast.error('Failed to delete batch');
      fetchRecentImages(); // Refresh to ensure UI is in sync
    }
  };
  
  // Handle copying an image to another bucket
  const handleCopyTo = async (img: ImageItem, targetBucketId: string) => {
    try {
      toast.loading(`Copying to ${targetBucketId}...`, { id: 'copy-toast' });
      const bucketId = img.bucketId || '_recent';
      await apiService.copyImageToBucket(bucketId, targetBucketId, img.id, true);
      toast.dismiss('copy-toast');
      toast.success(`Image copied to ${targetBucketId}`);
    } catch (error) {
      console.error('Error copying image:', error);
      toast.dismiss('copy-toast');
      toast.error('Failed to copy image');
    }
  };
  
  // Handle publishing an image
  const handlePublish = async (img: ImageItem, targetBucketId: string) => {
    try {
      toast.loading(`Publishing to ${targetBucketId}...`, { id: 'publish-toast' });
      const bucketId = img.bucketId || '_recent';
      await apiService.publishImageUnified({
        dest_bucket_id: targetBucketId,
        src_bucket_id: bucketId,
        filename: img.id
      });
      toast.dismiss('publish-toast');
      toast.success(`Image published to ${targetBucketId}`);
    } catch (error) {
      console.error('Error publishing image:', error);
      toast.dismiss('publish-toast');
      toast.error('Failed to publish image');
    }
  };
  
  // Handle using an image in the prompt
  const handleUseAsPrompt = (img: ImageItem) => {
    if (window.addImageReferenceToPrompt) {
      const sourceUrl = img.raw_url || img.urlFull;
      const bucketId = img.bucketId || '_recent';
      window.addImageReferenceToPrompt(sourceUrl, bucketId, img.id);
      toast.success('Added to prompt');
    } else {
      console.error('addImageReferenceToPrompt not available');
      toast.error('Could not add to prompt');
    }
  };
  
  // Handle panel expansion/collapse
  const handleTogglePanelCollapse = (batchId: string) => {
    setCollapsedPanels(prev => ({
      ...prev,
      [batchId]: !prev[batchId]
    }));
  };
  
  // Handle image click (open in Loope)
  const handleImageClick = (img: ImageItem) => {
    // Find batch that contains this image
    const batch = batchGroups.find(b => b.images.some(i => i.id === img.id));
    if (!batch || !openLoope) return;
    
    // Find index of clicked image
    const clickedIdx = batch.images.findIndex(i => i.id === img.id);
    
    // Get the prompt text for the title
    const prompt = batch.images[0]?.promptKey || 'No prompt';
    
    // Open Loope with just this batch's images
    openLoope(
      batch.images, 
      clickedIdx, 
      prompt,
      {
        allowFavorites: false,  // No favorites in Recent tab
        type: 'recent-batch',
        batchId: batch.batchId,
        showGenerateAgain: true,
        onGenerateAgain: () => handleGenerateAgain(batch.batchId)
      }
    );
  };
  
  // Handle "Generate Again" from a batch
  const handleGenerateAgain = (batchId: string) => {
    // Find the batch
    const batch = batchGroups.find(b => b.batchId === batchId);
    if (!batch || batch.images.length === 0) return;
    
    // Extract the prompt and settings from the first image
    const firstImage = batch.images[0];
    const imageMetadata = firstImage.metadata as {
      prompt?: string;
      workflow?: string;
      params?: Record<string, any>;
      global_params?: Record<string, any>;
      reference_images?: any[];
    } | undefined;

    // Extract reference images from metadata or from the top-level field
    const referenceImages = imageMetadata?.reference_images || firstImage.reference_images || [];
    // Convert to accessible URLs - use actual bucket ID, fallback to _recent for backward compatibility
    const bucketId = firstImage.bucketId || '_recent';
    const referenceUrls = ReferenceImageService.getReferenceImageUrls(bucketId, referenceImages);

    // Create a single placeholder for this batch
    const placeholderThumb = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs='; 
    const placeholderId = `placeholder-${batchId}-${Date.now()}`;
    const placeholder: BucketImage = {
      id: placeholderId,
      url: placeholderThumb,
      thumbnail_url: placeholderThumb,
      batchId,
      created_at: Date.now()/1000,
      metadata: {
        placeholder: true,
        aspectRatio: 16/9
      }
    };
    setBucketImages(prev => [placeholder, ...prev]);
    setPlaceholders(prev => [...prev, {
      id: placeholderId,
      timestamp: Date.now()
    }]);

    // Create parameters for generation
    // For "Generate Again", we want to use the exact same refined prompt and settings
    // without re-running the refiner
    // Use the actual resolved parameters that were used during the original generation
    // This ensures that "Generate Again" uses the exact same parameters, not just the original request
    const actualParams = imageMetadata?.params || {};
    const actualGlobalParams = imageMetadata?.global_params || {};
    const actualWorkflow = imageMetadata?.workflow || '';
    
    const params: any = {
      prompt: firstImage.promptKey || imageMetadata?.prompt || '',  // Use the refined prompt
      batch_id: batchId, // reuse original batch ID
      workflow: actualWorkflow,  // Use the actual workflow that was resolved
      params: {
        ...actualParams,  // Use the actual parameters that were used
        publish_destination: '_recent', // Add publish_destination for reference image resolution
      },
      global_params: {
        ...actualGlobalParams,  // Use the actual global parameters that were used
        batch_size: 1, // Override batch size for Generate Again
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
      })
      .catch((error: any) => {
        toast.dismiss('generate-toast');
        toast.error('Failed to generate image');
        console.error('Error generating image:', error);
      });
  };
  
  // Listen for batch placeholder events from image-generator.ts
  useEffect(() => {
    const handleBatchPlaceholders = (e: any) => {
      try {
        const { batchId, placeholders, prompt, workflow, params, globalParams, collapsed } = e.detail || {};
        if (!batchId || !placeholders || placeholders.length === 0) return;
        
        // Check for duplicate events (same batch ID within a short time window)
        const now = Date.now();
        const duplicateEvent = recentPlaceholderEvents.find(
          event => event.batchId === batchId && (now - event.timestamp) < 1000
        );
        
        if (duplicateEvent) {
          console.warn(`[recent] DUPLICATE placeholder event detected for batch ${batchId} - ignoring`);
          return;
        }
        
        // Add this event to our tracking
        recentPlaceholderEvents.push({ batchId, timestamp: now });
        // Keep only the last 10 events
        if (recentPlaceholderEvents.length > 10) {
          recentPlaceholderEvents.shift();
        }
        
        console.log('[recent] Received batch placeholders for batch', batchId, 'count:', placeholders.length);

        // Create placeholder images for the batch
        const newPlaceholders: BucketImage[] = placeholders.map(({ placeholderId, batchIndex }: { placeholderId: string, batchIndex: number }) => {
          const placeholderThumb = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
          return {
            id: placeholderId,
            url: placeholderThumb,
            thumbnail_url: placeholderThumb,
            batchId,
            created_at: Date.now()/1000,
            metadata: { 
              placeholder: true, 
              aspectRatio: 16/9,
              prompt,
              workflow,
              params,
              global_params: globalParams,
              batchIndex
            },
            prompt,
          };
        });
        
        // Add placeholders to bucket images with careful deduplication
        setBucketImages(prev => {
          // Filter out any existing placeholders with the same IDs to avoid duplicates
          const filteredPrev = prev.filter(img => 
            !placeholders.some((p: { placeholderId: string }) => p.placeholderId === img.id)
          );

          return [...newPlaceholders, ...filteredPrev];
        });
        
        // Move batch to top of order
        setBatchOrder(prev => {
          const without = prev.filter(id => id !== batchId);
          return [batchId, ...without];
        });
        
        // Set the container collapse state based on the event parameter
        setCollapsedPanels(prev => ({
          ...prev,
          [batchId]: collapsed === true, // explicitly check for true to be safe
        }));
        
        // Track placeholders for auto-expiry
        setPlaceholders(prev => {
          const newPlaceholderTrackers = placeholders.map(
            (p: { placeholderId: string }) => ({ id: p.placeholderId, timestamp: Date.now() })
          );
          
          // Filter out any that already exist to avoid duplicates
          const filteredNew = newPlaceholderTrackers.filter(
            tracker => !prev.some(p => p.id === tracker.id)
          );
          
          return [...prev, ...filteredNew];
        });
      } catch (err) {
        console.warn('recent:batch-placeholders handler error', err);
      }
    };
    
    window.addEventListener('recent:batch-placeholders', handleBatchPlaceholders as EventListener);
    // Removed verbose logging to reduce console noise
    return () => window.removeEventListener('recent:batch-placeholders', handleBatchPlaceholders as EventListener);
  }, []);

  // Add a new useEffect to handle the generation-complete event:
  useEffect(() => {
    const handleGenerationComplete = (e: any) => {
      try {
        const { batchId, count, autoExpand } = e.detail || {};
        if (!batchId) return;
        

        
        if (autoExpand) {
          // Expand this container and collapse others
          setCollapsedPanels(prev => {
            const updated = { ...prev };
            
            // Set all containers as collapsed
            Object.keys(updated).forEach(id => {
              updated[id] = id !== batchId; // true = collapsed for all except this batch
            });
            
            // Make sure this batch is expanded
            updated[batchId] = false;
            
            return updated;
          });
          
          // Also ensure this batch is at the top of the order
          setBatchOrder(prev => {
            if (prev[0] === batchId) return prev; // Already at the top
            const without = prev.filter(id => id !== batchId);
            return [batchId, ...without];
          });
        }
        
        // Clean up any placeholders from localStorage that might be associated with this batch
        try {
          const storedPlaceholders = localStorage.getItem('activePlaceholders');
          if (storedPlaceholders) {
            const parsed = JSON.parse(storedPlaceholders);
            const remaining = parsed.filter((p: any) => p.batchId !== batchId);
            localStorage.setItem('activePlaceholders', JSON.stringify(remaining));

          }
        } catch (error) {
          console.error('[recent] Error cleaning up localStorage placeholders:', error);
        }
      } catch (err) {
        console.warn('recent:generation-complete handler error', err);
      }
    };
    
    window.addEventListener('recent:generation-complete', handleGenerationComplete as EventListener);
    // Removed verbose logging to reduce console noise
    return () => window.removeEventListener('recent:generation-complete', handleGenerationComplete as EventListener);
  }, []);

  // Add cleanup for expired placeholders in localStorage on component mount
  useEffect(() => {
    cleanupExpiredPlaceholders();
  }, []);
  
  // Listen for new images event - update to track placeholders
  useEffect(() => {
    const handleRecentAdd = (e: any) => {
      try {
        const { batchId, files, imagesWithMetadata } = e.detail || {};
        if (!files || files.length === 0) return;
        // If no metadata provided, ignore this event to prevent overwriting existing metadata
        if (!imagesWithMetadata) {
          console.warn('[recent:add] Event without metadata ignored to preserve existing data');
          return;
        }
        
        // Create new image objects using the metadata directly from the API
        const newImgs: BucketImage[] = files.map((fname: string, index: number) => {
          // Find matching metadata for this file
          const fileMetadata = imagesWithMetadata && imagesWithMetadata.find((img: any) => 
            img.fileName === fname
          );
          
          return {
            id: fname,
            url: `/output/_recent/${fname}`,
            thumbnail_url: `/output/_recent/thumbnails/${fname}.jpg`,
            batchId,
            created_at: fileMetadata?.metadata?.timestamp 
              ? fileMetadata.metadata.timestamp / 1000  // Convert from ms to seconds
              : Date.now() / 1000,
            metadata: fileMetadata?.metadata || {},
            prompt: fileMetadata?.metadata?.prompt || "",
            bucketId: '_recent' // This function specifically handles _recent bucket additions
          };
        });
        

        
        // For each real image that arrived, find one placeholder to remove
        setBucketImages(prev => {
          // Find placeholders with matching batch ID (detect via metadata.placeholder flag)
          const matchingPlaceholders = prev.filter(img => 
            img.batchId === batchId && img.metadata?.placeholder === true
          );
          
          // If there are placeholders, remove exactly one for each new image
          if (matchingPlaceholders.length > 0) {
            // Keep track of placeholders we'll remove
            const placeholdersToRemove = matchingPlaceholders.slice(0, Math.min(matchingPlaceholders.length, newImgs.length));
            const placeholderIds = placeholdersToRemove.map(p => p.id);
            
            // Also update our placeholder tracking state
            // Temporarily disabled to fix setState during render warning
            // setPlaceholders(prevPlaceholders => 
            //   prevPlaceholders.filter(p => !placeholderIds.includes(p.id))
            // );
            
            // Filter out the placeholders we're removing, keep everything else
            const remainingImages = prev.filter(img => !placeholderIds.includes(img.id));
            
            // Add the new images ensuring no duplicates by id
            // Create a Map using a combination of id and batchId to ensure true uniqueness
            const uniqueMap = new Map<string, BucketImage>();
            
            // First add all remaining images to the map
            remainingImages.forEach(img => {
              const uniqueKey = `${img.id}_${img.batchId || 'unknown'}`;
              uniqueMap.set(uniqueKey, img);
            });
            
            // Then add new images, potentially overwriting older ones with same id
            newImgs.forEach(img => {
              const uniqueKey = `${img.id}_${img.batchId || 'unknown'}`;
              uniqueMap.set(uniqueKey, img);
            });
            
            return Array.from(uniqueMap.values());
          }
          
          // If no placeholders found, just add the new images with deduplication
          const uniqueMap = new Map<string, BucketImage>();
          
          // First add all existing images
          prev.forEach(img => {
            const uniqueKey = `${img.id}_${img.batchId || 'unknown'}`;
            uniqueMap.set(uniqueKey, img);
          });
          
          // Then add new images, overwriting any with the same id+batchId combination
          newImgs.forEach(img => {
            const uniqueKey = `${img.id}_${img.batchId || 'unknown'}`;
            uniqueMap.set(uniqueKey, img);
          });
          
          return Array.from(uniqueMap.values());
        });
        
        // Move batch to top and update UI states
        setBatchOrder(prev => {
          const without = prev.filter(id => id !== batchId);
          return [batchId, ...without];
        });
        
        // Expand this batch, keep existing collapse state for others unless explicitly collapse
        setCollapsedPanels(prev => ({
          ...prev,
          [batchId]: false,
        }));
        
        // Force select the newest image (assume first of newImgs array)
        if (newImgs.length > 0) {
          // Defer this state update to avoid setState during render warning
          setTimeout(() => {
            setSelectedImageByBatch(prev => ({ ...prev, [batchId]: newImgs[0].id }));
          }, 0);
        }
      } catch (err) {
        console.warn('recent:add handler error', err);
      }
    };
    
    window.addEventListener('recent:add', handleRecentAdd as EventListener);
    // Removed verbose logging to reduce console noise
    return () => window.removeEventListener('recent:add', handleRecentAdd as EventListener);
  }, []);
  
  // -----------------------------
  // Drag and Drop handlers (batch-level)
  // -----------------------------

  // Track the image currently being dragged to render a consistent overlay (thumbnails + selected)
  const [activeDraggedImage, setActiveDraggedImage] = useState<ImageItem | null>(null);

  const handleDragStart = (event: DragStartEvent) => {
    const { active } = event;

    // If this is an image drag (detected via the data payload), capture it for overlay
    const activeData: any = active.data?.current;
    if (activeData?.image) {
      setActiveDraggedImage(activeData.image as ImageItem);
    }

    // Handle batch container drags as before
    if (!batchOrder.includes(active.id as string)) {
      return;
    }


  };

  const handleDragEnd = (event: DragEndEvent) => {
    // Always clear overlay state first
    setActiveDraggedImage(null);

    const { active, over } = event;

    if (!over || active.id === over.id) return;

    // Proceed only if both IDs belong to known batches
    if (!batchOrder.includes(active.id as string) || !batchOrder.includes(over.id as string)) {
      console.warn('[DnD Debug] Drag end ignored – active/over not in batchOrder');
      return;
    }

    const oldIndex = batchOrder.indexOf(active.id as string);
    const newIndex = batchOrder.indexOf(over.id as string);

    if (oldIndex === -1 || newIndex === -1) return;



    // Update batch order
    setBatchOrder((items) => arrayMove(items, oldIndex, newIndex));

    // Mirror the change for batchGroups so the UI updates immediately
    setBatchGroups((groups) => {
      const oldIdx = groups.findIndex((g) => g.batchId === active.id);
      const newIdx = groups.findIndex((g) => g.batchId === over.id);
      if (oldIdx === -1 || newIdx === -1) return groups;
      return arrayMove(groups, oldIdx, newIdx);
    });
  };

  // Draggable wrapper for a batch panel – defined outside component so its identity is stable
  const DraggableBatchPanel = React.memo(({ batch, isCollapsed, collapsedPanels, onCollapseChange, handleDeleteImage, handleDeleteBatch, handleImageClick, handleCopyTo, handlePublish, handleUseAsPrompt, handleGenerateAgain, destinations, selectedImageByBatch, handleSelectImage }: {
    batch: { batchId: string; images: ImageItem[] };
    isCollapsed: boolean;
    collapsedPanels: Record<string, boolean>;
    onCollapseChange: (id: string, collapsed: boolean) => void;
    handleDeleteImage: (img: ImageItem) => void;
    handleDeleteBatch: (batchId: string) => void;
    handleImageClick: (img: ImageItem) => void;
    handleCopyTo: (img: ImageItem, destId: string) => void;
    handlePublish: (img: ImageItem, destId: string) => void;
    handleUseAsPrompt: (img: ImageItem) => void;
    handleGenerateAgain: (batchId: string) => void;
    destinations: { id: string; name: string; headless: boolean }[];
    selectedImageByBatch: Record<string,string>;
    handleSelectImage: (batchId:string, imgId:string)=>void;
  }) => {
    const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: batch.batchId });

    const style: React.CSSProperties = {
      transform: CSS.Transform.toString(transform),
      transition,
      cursor: 'grab',
      opacity: isDragging ? 0.5 : 1,
    };

    return (
      <div ref={setNodeRef} style={style} {...attributes} {...listeners} className="mb-2">
        <RecentBatchPanel
          batchId={batch.batchId}
          images={batch.images}
          initialCollapsed={isCollapsed}
          onToggleFavorite={() => {}}
          onDeleteImage={handleDeleteImage}
          onDeleteBatch={handleDeleteBatch}
          onImageClick={handleImageClick}
          onCopyTo={handleCopyTo}
          onPublish={handlePublish}
          onUseAsPrompt={handleUseAsPrompt}
          onGenerateAgain={handleGenerateAgain}
          publishDestinations={destinations}
          forceSelectId={selectedImageByBatch[batch.batchId] || null}
          onSelectImage={handleSelectImage}
          onCollapseChange={onCollapseChange}
        />
      </div>
    );
  });

  // Add targeted logging to key useEffect hooks
  useEffect(() => {
    saveStateToStorage();
  }, [batchOrder, collapsedPanels]);

  // Add a useEffect to expire placeholders after 5 minutes
  useEffect(() => {
    // Don't run the check if there are no placeholders
    if (placeholders.length === 0) return;
    
    // Run the expiration check once without setting up an interval
    const now = Date.now();
    const expireTime = 5 * 60 * 1000; // 5 minutes
    
    // Find placeholders that should be expired
    const expiredIds = placeholders
      .filter(p => (now - p.timestamp) > expireTime)
      .map(p => p.id);
    
    // If any placeholders need to be expired, update state
    if (expiredIds.length > 0) {

      
      // Remove expired placeholders from the images list
      setBucketImages(prev => prev.filter(img => !expiredIds.includes(img.id)));
      
      // Remove expired placeholders from our tracking list
      setPlaceholders(prev => prev.filter(p => !expiredIds.includes(p.id)));
      
      // Also remove from localStorage
      try {
        const storedPlaceholders = localStorage.getItem('activePlaceholders');
        if (storedPlaceholders) {
          const parsed = JSON.parse(storedPlaceholders);
          const remaining = parsed.filter((p: any) => !expiredIds.includes(p.id));
          localStorage.setItem('activePlaceholders', JSON.stringify(remaining));
        }
      } catch (error) {
        console.error('[recent] Error removing expired placeholders from localStorage:', error);
      }
    }
    
    // No need to clear interval since we're not setting one
  }, [placeholders]);

  // Memoize batch groups to prevent unnecessary re-renders
  const memoizedBatchGroups = useMemo(() => {
    return batchGroups;
  }, [batchGroups]);

  // Add diagnostic logging for component mounting
  useEffect(() => {
    return () => {
      // Component cleanup
    };
  }, [refreshRecent]);

  // Hook into the parent DndContext to listen for drag events without creating a new context
  useDndMonitor({
    onDragStart: handleDragStart,
    onDragEnd: handleDragEnd
  });

  // If loading, show loading state
  if (loading) {
    return <div className="p-4 text-center">Loading recent images...</div>;
  }
  
  // If error, show error state
  if (error) {
    return <div className="p-4 text-center text-red-500">{error}</div>;
  }
  
  // If no batches, show empty state
  if (batchGroups.length === 0) {
    return (
      <div className="p-4 text-center text-gray-500">
        <p>No recent images found.</p>
        <p className="text-sm mt-2">Generated images will appear here.</p>
      </div>
    );
  }
  
  // Render the batch groups with diagnostic info
  return (
    <>
      <div className={styles.recentViewContainer}>
        <SortableContext items={batchOrder} strategy={rectSortingStrategy}>
          {memoizedBatchGroups.map(batch => (
            <DraggableBatchPanel
              key={`batch-panel-${batch.batchId}`}
              batch={batch}
              isCollapsed={collapsedPanels[batch.batchId] || false}
              collapsedPanels={collapsedPanels}
              onCollapseChange={(id, collapsed) => setCollapsedPanels(prev => ({ ...prev, [id]: collapsed }))}
              handleDeleteImage={handleDeleteImage}
              handleDeleteBatch={handleDeleteBatch}
              handleImageClick={handleImageClick}
              handleCopyTo={handleCopyTo}
              handlePublish={handlePublish}
              handleUseAsPrompt={handleUseAsPrompt}
              handleGenerateAgain={handleGenerateAgain}
              destinations={destinations}
              selectedImageByBatch={selectedImageByBatch}
              handleSelectImage={handleSelectImage}
            />
          ))}
        </SortableContext>
      </div>
      <DragOverlay adjustScale={false}>
        {activeDraggedImage ? (
          <div className="rounded-md overflow-hidden shadow-xl transform-gpu scale-105 opacity-70">
            <img
              src={activeDraggedImage.urlThumb || activeDraggedImage.urlFull}
              alt="drag preview"
              className="w-full h-full object-cover"
            />
          </div>
        ) : null}
      </DragOverlay>
    </>
  );
};

export default RecentView; 
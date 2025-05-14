import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { RecentBatchPanel } from './RecentBatchPanel';
import { SortableContext, arrayMove, rectSortingStrategy, useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { DragStartEvent, DragEndEvent } from '@dnd-kit/core';
import { ImageItem } from '@/types/image-types';
import { useLoopeView } from '@/contexts/LoopeViewContext';
import styles from './recent.module.css';
import React from 'react';

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
  
  // DnD state
  const [activeId, setActiveId] = useState<string | null>(null);
  
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
    // Just store the selection without triggering UI updates across batches
    setSelectedImageByBatch(prev => {
      // Skip if no change 
      if (prev[batchId] === imageId) return prev;
      
      // Create minimal update with just this batch's selection
      const updated = {...prev};
      updated[batchId] = imageId;
      
      // Save for persistence only
      try {
        localStorage.setItem('recentTabSelections', JSON.stringify(updated));
      } catch (error) {
        console.error('Error saving selection:', error);
      }
      
      return updated;
    });
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
        console.log('[recent] Loaded placeholders from localStorage:', parsed);
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
          console.log('[recent] Cleaned up expired placeholders, remaining:', activePlaceholders.length);
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
      const images = details.items.map((item: any) => ({
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
        batchId: extractBatchId(item.filename)
      }));
      
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
        
        console.log(`[recent] Preserving ${relevantPlaceholders.length} placeholders through polling`);
        
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
    // Extract "batch-XYZ" pattern from the filename
    const batchMatch = filename.match(/batch-([^_\.]+)/);
    if (batchMatch && batchMatch[1]) {
      return batchMatch[1]; // Return just the batch ID value
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
      groups.forEach(g => { initialCollapse[g.batchId] = g.batchId !== firstId; });
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
  const toImageItem = (img: BucketImage): ImageItem => ({
    id: img.id,
    // Ensure the key for this item is truly unique by using both id and batchId
    uniqueKey: `${img.id}_${img.batchId || 'unknown'}`,
    urlThumb: img.thumbnail_url || img.thumbnail_embedded || '',
    urlFull: img.url || '',
    promptKey: img.prompt || '',
    seed: 0,
    createdAt: img.created_at ? new Date(img.created_at * 1000).toISOString() : new Date().toISOString(),
    isFavourite: false, // Always false in Recent
    mediaType: isVideoFile(img.id) ? 'video' : 'image',
    raw_url: img.raw_url || img.url || '',
    metadata: img.metadata || {},
    bucketId: '_recent',
  });
  
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
      await apiService.deleteImage('_recent', image.id);
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
      const deletePromises = batch.images.map(img => 
        apiService.deleteImage('_recent', img.id)
      );
      
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
      await apiService.copyImageToBucket('_recent', targetBucketId, img.id, true);
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
      await apiService.publishImageUnified({
        dest_bucket_id: targetBucketId,
        src_bucket_id: '_recent',
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
      window.addImageReferenceToPrompt(sourceUrl, '_recent', img.id);
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
    } | undefined;
    
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
    
    // Add a single placeholder to the image list
    setBucketImages(prev => [placeholder, ...prev]);
    
    // Track this placeholder for automatic expiration
    setPlaceholders(prev => [...prev, {
      id: placeholderId,
      timestamp: Date.now()
    }]);
    
    // Create parameters for generation
    const params: any = {
      prompt: firstImage.promptKey || imageMetadata?.prompt || '',
      batch_id: batchId, // reuse original batch ID
      workflow: imageMetadata?.workflow || '',
      params: imageMetadata?.params || {},
      global_params: {},
      placeholders: [] // Required by the API type
    };
    
    // Indicate placeholder already created
    params.__skipPlaceholder = true;
    
    // Call generate using the API service
    toast.loading('Generating image...', { id: 'generate-toast' });
    apiService.generateImage(params)
      .then(() => {
        toast.dismiss('generate-toast');
        toast.success('Image generated');
        // The recent:add event handler will take care of removing the placeholder
      })
      .catch((error: any) => {
        toast.dismiss('generate-toast');
        toast.error('Failed to generate image');
        console.error('Error generating image:', error);
        // Don't remove the placeholder on error - let it expire naturally
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
          console.log(`[recent] Adding ${newPlaceholders.length} placeholders, filtered out ${prev.length - filteredPrev.length} duplicates`);
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
    console.log('[recent] listener added for batch-placeholders');
    return () => window.removeEventListener('recent:batch-placeholders', handleBatchPlaceholders as EventListener);
  }, []);

  // Add a new useEffect to handle the generation-complete event:
  useEffect(() => {
    const handleGenerationComplete = (e: any) => {
      try {
        const { batchId, count, autoExpand } = e.detail || {};
        if (!batchId) return;
        
        console.log(`[recent] Generation complete for batch ${batchId} with ${count} images`);
        
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
            console.log(`[recent] Removed completed placeholders for batch ${batchId} from localStorage`);
          }
        } catch (error) {
          console.error('[recent] Error cleaning up localStorage placeholders:', error);
        }
      } catch (err) {
        console.warn('recent:generation-complete handler error', err);
      }
    };
    
    window.addEventListener('recent:generation-complete', handleGenerationComplete as EventListener);
    console.log('[recent] listener added for generation-complete');
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
            prompt: fileMetadata?.metadata?.prompt || ""
          };
        });
        
        console.log("[handleRecentAdd] Created images with metadata:", newImgs);
        
        // For each real image that arrived, find one placeholder to remove
        setBucketImages(prev => {
          // Find placeholders with matching batch ID
          const matchingPlaceholders = prev.filter(img => 
            img.id.startsWith('placeholder-') && img.batchId === batchId
          );
          
          // If there are placeholders, remove exactly one for each new image
          if (matchingPlaceholders.length > 0) {
            // Keep track of placeholders we'll remove
            const placeholdersToRemove = matchingPlaceholders.slice(0, Math.min(matchingPlaceholders.length, newImgs.length));
            const placeholderIds = placeholdersToRemove.map(p => p.id);
            
            // Also update our placeholder tracking state
            setPlaceholders(prevPlaceholders => 
              prevPlaceholders.filter(p => !placeholderIds.includes(p.id))
            );
            
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
          setSelectedImageByBatch(prev => ({ ...prev, [batchId]: newImgs[0].id }));
        }
      } catch (err) {
        console.warn('recent:add handler error', err);
      }
    };
    
    window.addEventListener('recent:add', handleRecentAdd as EventListener);
    console.log('[recent] listener added for placeholder');
    return () => window.removeEventListener('recent:add', handleRecentAdd as EventListener);
  }, []);
  
  // DnD handlers for batch reordering
  const handleDragStart = (event: DragStartEvent) => {
    console.log('[DnD Debug] Container drag start:', {
      activeId: event.active.id,
      activeRect: event.active.rect
    });
    setActiveId(event.active.id as string);
    
    // Log the current state of batchOrder
    console.log('[DnD Debug] Current batch order at drag start:', [...batchOrder]);
  };
  
  const handleDragEnd = (event: DragEndEvent) => {
    console.log('[DnD Debug] Container drag end:', {
      activeId: event.active.id,
      overId: event.over?.id,
      overRect: event.over?.rect,
      delta: event.delta
    });
    
    const { active, over } = event;
    
    if (over && active.id !== over.id) {
      console.log('[DnD Debug] Valid drop detected between containers:', {
        fromId: active.id,
        toId: over.id,
        currentOrder: [...batchOrder]
      });
      
      // Check if IDs exist in batch order
      const activeExists = batchOrder.includes(active.id as string);
      const overExists = batchOrder.includes(over.id as string);
      
      console.log('[DnD Debug] ID verification:', {
        activeIdExists: activeExists,
        overIdExists: overExists,
        activeIdType: typeof active.id,
        overIdType: typeof over.id
      });
      
      if (!activeExists || !overExists) {
        console.warn('[DnD Debug] ⚠️ One or both IDs not found in batch order');
        setActiveId(null);
        return;
      }
      
      // Reorder batches
      setBatchOrder(items => {
        const oldIndex = items.indexOf(active.id as string);
        const newIndex = items.indexOf(over.id as string);
        
        console.log('[DnD Debug] Reordering indexes:', { 
          oldIndex, 
          newIndex,
          batchOrderCopy: [...items]
        });
        
        if (oldIndex === -1 || newIndex === -1) {
          console.warn('[DnD Debug] Invalid indexes for batchOrder update');
          return items;
        }
        
        const newOrder = arrayMove(items, oldIndex, newIndex);
        console.log('[DnD Debug] New batch order:', newOrder);
        return newOrder;
      });
      
      // Reorder batchGroups array as well for immediate UI update
      setBatchGroups(groups => {
        const oldIndex = groups.findIndex(g => g.batchId === active.id);
        const newIndex = groups.findIndex(g => g.batchId === over.id);
        
        console.log('[DnD Debug] Reordering batch groups:', { 
          oldIndex, 
          newIndex,
          batchIdActive: active.id,
          batchIdOver: over.id
        });
        
        if (oldIndex === -1 || newIndex === -1) {
          console.warn('[DnD Debug] Invalid indexes for batch groups update');
          return groups;
        }
        
        return arrayMove(groups, oldIndex, newIndex);
      });
    } else {
      if (!over) {
        console.log('[DnD Debug] No valid drop target found');
      } else if (active.id === over.id) {
        console.log('[DnD Debug] Dropped on same container, no reordering needed');
      }
    }
    
    setActiveId(null);
  };
  
  // Handle image metadata load
  const handleImageLoad = useCallback(
    async (imageId: string, imageUrl: string) => {
      const metadataUrl = imageUrl.replace(/\.(\w+)$/, '.json');
      try {
        const response = await fetch(metadataUrl);
        if (response.ok) {
          const metadata = await response.json();
          setBucketImages((prev) =>
            prev.map((img) =>
              img.id === imageId ? { ...img, metadata } : img
            )
          );
        }
      } catch (error) {
        console.error('Error loading image metadata:', error);
      }
    },
    []
  );

  // Wrapper to make entire batch panel draggable with memoized props
  const SortableBatchPanel = React.memo(({ batch }: { batch: { batchId: string, images: ImageItem[] } }) => {
    const {
      attributes,
      listeners,
      setNodeRef,
      transform,
      transition,
      isDragging
    } = useSortable({ id: batch.batchId });

    // Only log when actually being dragged to avoid excessive logging
    React.useEffect(() => {
      if (isDragging) {
        console.log(`[DnD Debug] Batch panel ${batch.batchId} is being dragged`);
      }
    }, [isDragging, batch.batchId]);

    // When transform changes during drag, log it (but only when dragging to avoid noise)
    React.useEffect(() => {
      if (isDragging && transform) {
        console.log(`[DnD Debug] Batch ${batch.batchId} transform:`, transform);
      }
    }, [transform, batch.batchId, isDragging]);

    const style = {
      transform: CSS.Transform.toString(transform),
      transition,
      cursor: 'grab',
      opacity: isDragging ? 0.5 : 1, // Add visual feedback when dragging
    };

    return (
      <div ref={setNodeRef} style={style} {...attributes} {...listeners} className="mb-2">
        <RecentBatchPanel
          batchId={batch.batchId}
          images={batch.images}
          initialCollapsed={collapsedPanels[batch.batchId] || false}
          onToggleFavorite={() => {}}
          onDeleteImage={handleDeleteImage}
          onDeleteBatch={handleDeleteBatch}
          onImageClick={handleImageClick}
          onCopyTo={handleCopyTo}
          onPublish={handlePublish}
          onUseAsPrompt={handleUseAsPrompt}
          onGenerateAgain={handleGenerateAgain}
          publishDestinations={destinations}
          
          // Only pass forceSelectId for initial render
          forceSelectId={selectedImageByBatch[batch.batchId] || null}
          
          // Use the selection handler
          onSelectImage={handleSelectImage}
          onCollapseChange={(id,collapsed)=> setCollapsedPanels(prev=>({...prev,[id]:collapsed}))}
        />
      </div>
    );
  });

  // Add targeted logging to key useEffect hooks
  useEffect(() => {
    console.log('[DnD Debug] batchOrder changed:', batchOrder);
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
      console.log(`[recent] Auto-expiring ${expiredIds.length} placeholders after timeout`);
      
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

  // Component-level DnD debugging
  // Track when activeId changes to understand if container or image drag is happening
  useEffect(() => {
    if (activeId) {
      console.log(`[DnD Debug] Container drag active: ${activeId}`);
      
      // Log batch information for the active batch
      const activeBatch = batchGroups.find(group => group.batchId === activeId);
      if (activeBatch) {
        console.log(`[DnD Debug] Active batch has ${activeBatch.images.length} images`);
      } else {
        console.log(`[DnD Debug] ⚠️ Active ID ${activeId} not found in batch groups`);
      }
    }
  }, [activeId, batchGroups]);

  // Debug logging for DnD context
  console.log('[DnD Debug] Rendering with batchOrder:', batchOrder);

  // Add diagnostic logging for component mounting
  useEffect(() => {
    console.log('[DnD Debug] RecentView component mounted');
    console.log('[DnD Debug] Available props:', { refreshRecent });
    
    // Log any parent DndContext info we can access
    try {
      // Check if there's any window-level or DOM-level indication of DndContext
      console.log('[DnD Debug] DndContext detected:', 
        !!document.querySelector('[data-dnd-context]') || 
        !!(window as any).__DND_CONTEXT_ID);
    } catch (err) {
      console.log('[DnD Debug] Error checking for parent DndContext:', err);
    }
  }, [refreshRecent]);

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
    <div className={styles.recentViewContainer}>
      <SortableContext items={batchOrder} strategy={rectSortingStrategy}>
        {memoizedBatchGroups.map(batch => (
          <SortableBatchPanel 
            key={`batch-panel-${batch.batchId}`} 
            batch={batch} 
          />
        ))}
      </SortableContext>
    </div>
  );
};

export default RecentView; 
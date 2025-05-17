import React, { useEffect, useState } from 'react';
import { CardContent } from "@/components/ui/card";
import { toast } from "sonner";
import { getImageMetadata } from "../utils";
import { MetadataHeader } from './MetadataHeader';
import { MetadataSearch } from './MetadataSearch';
import { MetadataEntryList } from './MetadataEntryList';
import { MetadataEntry } from '../types';

interface MetadataTabProps {
  metadataEntries: MetadataEntry[];
  insertMetadataTag: (key: string) => void;
  setActiveTab: (tab: string) => void;
  onRefreshMetadata?: () => Promise<Record<string, string>>;
}

export const MetadataTab: React.FC<MetadataTabProps> = ({
  metadataEntries,
  insertMetadataTag,
  setActiveTab,
  onRefreshMetadata
}) => {
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [searchTerm, setSearchTerm] = useState('');
  
  useEffect(() => {
    console.log('[MetadataTab] Mounted/updated with entries:', metadataEntries);
    
    if (metadataEntries.length === 0) {
      console.log('[MetadataTab] No metadata entries found');
    } else {
      console.log('[MetadataTab] Number of metadata entries:', metadataEntries.length);
      console.log('[MetadataTab] First few entries:', metadataEntries.slice(0, 3));
    }
  }, [metadataEntries]);
  
  const filteredEntries = metadataEntries.filter(entry => 
    entry.key.toLowerCase().includes(searchTerm.toLowerCase()) || 
    entry.value.toLowerCase().includes(searchTerm.toLowerCase())
  );
  
  const handleRefreshMetadata = async () => {
    setLoading(true);
    try {
      const currentImageUrl = localStorage.getItem('currentImageUrl');
      
      if (!currentImageUrl) {
        toast.error("No image URL available");
        setLoading(false);
        return;
      }
      
      console.log('[MetadataTab] Forcing refresh for:', currentImageUrl);
      
      // Use the provided onRefreshMetadata if available
      if (onRefreshMetadata) {
        toast.info("Extracting metadata...");
        console.log('[MetadataTab] Using provided onRefreshMetadata function');
        
        try {
          const metadata = await onRefreshMetadata();
          console.log('[MetadataTab] Fresh metadata from provided function:', metadata);
          
          if (Object.keys(metadata).length > 0) {
            toast.success(`Found ${Object.keys(metadata).length} metadata entries`);
            setRefreshKey(prev => prev + 1);
          } else {
            toast.warning("No metadata found in this image");
          }
        } catch (error) {
          console.error('[MetadataTab] Error from onRefreshMetadata:', error);
          toast.error(`Failed to extract metadata: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
        
        setLoading(false);
        return;
      }
      
      // Fallback to direct extraction if no onRefreshMetadata provided
      const cacheBustUrl = `${currentImageUrl}${currentImageUrl.includes('?') ? '&' : '?'}forcedRefresh=${Date.now()}_${Math.random()}`;
      
      toast.info("Extracting metadata...");
      
      // Skip API endpoint and use utility function directly
      try {
        const metadata = await getImageMetadata(cacheBustUrl);
        
        console.log('[MetadataTab] Fresh metadata from direct extraction:', metadata);
        
        if (Object.keys(metadata).length > 0) {
          toast.success(`Found ${Object.keys(metadata).length} metadata entries`);
          setRefreshKey(prev => prev + 1);
          // Don't reload the page as it breaks the debug mode
          // window.location.reload();
        } else {
          toast.warning("No metadata found in this image");
        }
      } catch (err) {
        console.error('[MetadataTab] Error in fallback extraction:', err);
        toast.error(`Failed to extract metadata: ${err instanceof Error ? err.message : 'Unknown error'}`);
      }
    } catch (err) {
      console.error('[MetadataTab] Error refreshing metadata:', err);
      toast.error(`Failed to refresh metadata: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setLoading(false);
    }
  };
  
  const triggerRefreshAnimation = () => {
    setLoading(true);
    setTimeout(() => setLoading(false), 500);
  };
  
  return (
    <CardContent className="pt-4 pb-2 h-full flex flex-col" key={refreshKey}>
      <MetadataHeader 
        loading={loading} 
        onRefresh={() => {
          triggerRefreshAnimation();
          handleRefreshMetadata();
        }} 
      />
      
      <MetadataSearch 
        searchTerm={searchTerm}
        setSearchTerm={setSearchTerm}
      />
      
      <MetadataEntryList 
        entries={filteredEntries}
        loading={loading}
        searchTerm={searchTerm}
        insertMetadataTag={insertMetadataTag}
        setActiveTab={setActiveTab}
      />
    </CardContent>
  );
};


import React, { useEffect, useState } from 'react';
import { CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Database, RefreshCw, Search, AlertCircle } from "lucide-react";
import { toast } from "sonner";
import { extractImageMetadata } from "../utils";

interface MetadataEntry {
  key: string;
  value: string;
}

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
  
  const forceRefreshMetadata = async () => {
    setLoading(true);
    try {
      const currentImageUrl = localStorage.getItem('currentImageUrl');
      
      if (currentImageUrl) {
        console.log('[MetadataTab] Forcing refresh for:', currentImageUrl);
        
        // Use the provided onRefreshMetadata if available
        if (onRefreshMetadata) {
          toast.info("Extracting metadata...");
          console.log('[MetadataTab] Using provided onRefreshMetadata function');
          
          const metadata = await onRefreshMetadata();
          console.log('[MetadataTab] Fresh metadata from provided function:', metadata);
          
          if (Object.keys(metadata).length > 0) {
            toast.success(`Found ${Object.keys(metadata).length} metadata entries`);
            setRefreshKey(prev => prev + 1);
          } else {
            toast.warning("No metadata found in this image");
          }
          return;
        }
        
        // Fallback to direct extraction if no onRefreshMetadata provided
        const cacheBustUrl = `${currentImageUrl}${currentImageUrl.includes('?') ? '&' : '?'}forcedRefresh=${Date.now()}_${Math.random()}`;
        
        toast.info("Extracting metadata...");
        const metadata = await extractImageMetadata(cacheBustUrl);
        
        console.log('[MetadataTab] Fresh metadata from direct extraction:', metadata);
        
        if (Object.keys(metadata).length > 0) {
          toast.success(`Found ${Object.keys(metadata).length} metadata entries`);
          setRefreshKey(prev => prev + 1);
          window.location.reload();
        } else {
          console.warn('[MetadataTab] No metadata found after refresh');
          toast.warning("No metadata found in this image");
          
          // Try an alternative approach
          const response = await fetch(cacheBustUrl, {
            cache: 'no-store',
            headers: {
              'Cache-Control': 'no-cache, no-store, must-revalidate',
              'Pragma': 'no-cache',
              'Expires': '0'
            }
          });
          
          if (!response.ok) {
            throw new Error(`Failed to fetch image: ${response.status}`);
          }
          
          const blob = await response.blob();
          const imgUrl = URL.createObjectURL(blob);
          
          console.log('[MetadataTab] Trying with blob URL:', imgUrl);
          const retryMetadata = await extractImageMetadata(imgUrl);
          
          URL.revokeObjectURL(imgUrl);
          
          if (Object.keys(retryMetadata).length > 0) {
            toast.success(`Found ${Object.keys(retryMetadata).length} metadata entries on second attempt`);
            window.location.reload();
          } else {
            toast.error("No metadata found after multiple attempts");
          }
        }
      } else {
        toast.error("No image URL available");
      }
    } catch (err) {
      console.error('[MetadataTab] Error refreshing metadata:', err);
      toast.error("Failed to refresh metadata");
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
      <div className="flex justify-between items-center mb-2">
        <h3 className="text-sm font-medium">Image Metadata</h3>
        <Button 
          variant="ghost" 
          size="sm" 
          className="h-7 px-2"
          onClick={() => {
            triggerRefreshAnimation();
            forceRefreshMetadata();
          }}
        >
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
          <span>Refresh</span>
        </Button>
      </div>
      
      <div className="relative mb-2">
        <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search metadata..."
          className="w-full pl-8 py-2 text-sm rounded-md border border-input bg-background"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
      </div>
      
      <ScrollArea className="flex-1 rounded-md border p-2 min-h-[200px] min-w-[200px]">
        {filteredEntries.length > 0 ? (
          <div className="space-y-2">
            {filteredEntries.map((entry, index) => (
              <div 
                key={index}
                className="flex items-center justify-between p-2 rounded-md hover:bg-gray-100"
              >
                <div className="flex-1">
                  <div className="font-medium text-sm">{entry.key}</div>
                  <div className="text-xs text-gray-500 truncate">{entry.value}</div>
                </div>
                <Button 
                  variant="ghost" 
                  size="sm" 
                  className="h-7 px-2"
                  onClick={() => {
                    console.log('Using metadata tag:', entry.key);
                    insertMetadataTag(entry.key);
                    setActiveTab("caption");
                    toast.success(`Added ${entry.key} tag to caption`);
                  }}
                >
                  Use
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-gray-500">
            {loading ? (
              <>
                <RefreshCw className="h-8 w-8 mb-2 animate-spin" />
                <p className="text-sm">Loading metadata...</p>
              </>
            ) : searchTerm ? (
              <>
                <Search className="h-8 w-8 mb-2" />
                <p className="text-sm">No matching metadata found</p>
                <p className="text-xs mt-1">Try a different search term</p>
              </>
            ) : (
              <>
                <AlertCircle className="h-8 w-8 mb-2" />
                <p className="text-sm">No metadata available</p>
                <p className="text-xs mt-1">Click refresh to try again or select an image to view its metadata</p>
              </>
            )}
          </div>
        )}
      </ScrollArea>
    </CardContent>
  );
};

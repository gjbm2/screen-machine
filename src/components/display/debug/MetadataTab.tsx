
import React, { useEffect, useState } from 'react';
import { CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Database, RefreshCw } from "lucide-react";
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
}

export const MetadataTab: React.FC<MetadataTabProps> = ({
  metadataEntries,
  insertMetadataTag,
  setActiveTab
}) => {
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  
  // Enhanced logging to debug metadata issues
  useEffect(() => {
    console.log('MetadataTab mounted/updated with entries:', metadataEntries);
    
    if (metadataEntries.length === 0) {
      console.log('No metadata entries found. This might indicate an extraction issue.');
    } else {
      console.log('Metadata entries found:', metadataEntries.length);
      console.log('First few metadata entries:', metadataEntries.slice(0, 3));
    }
  }, [metadataEntries]);
  
  // Extract and force refresh metadata from current URL in local storage
  const forceRefreshMetadata = async () => {
    setLoading(true);
    try {
      // Get the current URL from localStorage if available
      const currentImageUrl = localStorage.getItem('currentImageUrl');
      
      if (currentImageUrl) {
        console.log('Forcing metadata refresh for URL:', currentImageUrl);
        const metadata = await extractImageMetadata(currentImageUrl);
        console.log('Freshly extracted metadata:', metadata);
        
        if (Object.keys(metadata).length > 0) {
          toast.success(`Refreshed metadata: found ${Object.keys(metadata).length} entries`);
          // Force rerender of component
          setRefreshKey(prev => prev + 1);
        } else {
          toast.warning("No metadata found in this image");
        }
      } else {
        toast.info("No image URL to refresh metadata from");
      }
    } catch (err) {
      console.error('Error refreshing metadata:', err);
      toast.error("Failed to refresh metadata");
    } finally {
      setLoading(false);
    }
  };
  
  // Simple animation to indicate the component is refreshed
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
      
      <ScrollArea className="flex-1 rounded-md border p-2 min-h-[200px] min-w-[200px]">
        {metadataEntries.length > 0 ? (
          <div className="space-y-2">
            {metadataEntries.map((entry, index) => (
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
            <Database className="h-8 w-8 mb-2" />
            <p className="text-sm">No metadata available</p>
            <p className="text-xs mt-1">Click refresh to try again or select an image to view its metadata</p>
          </div>
        )}
      </ScrollArea>
    </CardContent>
  );
};

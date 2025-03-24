
import React from 'react';
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AlertCircle, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import { MetadataEntry } from '../types';

interface MetadataEntryListProps {
  entries: MetadataEntry[];
  loading: boolean;
  searchTerm: string;
  insertMetadataTag: (key: string) => void;
  setActiveTab: (tab: string) => void;
}

export const MetadataEntryList: React.FC<MetadataEntryListProps> = ({
  entries,
  loading,
  searchTerm,
  insertMetadataTag,
  setActiveTab
}) => {
  return (
    <ScrollArea className="flex-1 rounded-md border p-2 min-h-[200px] min-w-[200px]">
      {entries.length > 0 ? (
        <div className="space-y-2">
          {entries.map((entry, index) => (
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
        <EmptyMetadataState loading={loading} searchTerm={searchTerm} />
      )}
    </ScrollArea>
  );
};

const EmptyMetadataState: React.FC<{ loading: boolean; searchTerm: string }> = ({ 
  loading, 
  searchTerm 
}) => {
  return (
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
  );
};

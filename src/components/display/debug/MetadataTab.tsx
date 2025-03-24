
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Database } from "lucide-react";

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
  return (
    <CardContent className="pt-4 pb-2">
      <ScrollArea className="h-[300px] rounded-md border p-2">
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
                    insertMetadataTag(entry.key);
                    setActiveTab("caption");
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
            <p className="text-xs mt-1">Select an image to view its metadata</p>
          </div>
        )}
      </ScrollArea>
    </CardContent>
  );
};

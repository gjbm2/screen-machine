
import React from 'react';
import { Button } from "@/components/ui/button";
import { RefreshCw } from "lucide-react";

interface MetadataHeaderProps {
  loading: boolean;
  onRefresh: () => void;
}

export const MetadataHeader: React.FC<MetadataHeaderProps> = ({
  loading,
  onRefresh
}) => {
  return (
    <div className="flex justify-between items-center mb-2">
      <h3 className="text-sm font-medium">Image Metadata</h3>
      <Button 
        variant="ghost" 
        size="sm" 
        className="h-7 px-2"
        onClick={onRefresh}
      >
        <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
        <span>Refresh</span>
      </Button>
    </div>
  );
};

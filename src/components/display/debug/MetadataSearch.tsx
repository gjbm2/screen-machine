
import React from 'react';
import { Search } from "lucide-react";

interface MetadataSearchProps {
  searchTerm: string;
  setSearchTerm: (value: string) => void;
}

export const MetadataSearch: React.FC<MetadataSearchProps> = ({
  searchTerm,
  setSearchTerm
}) => {
  return (
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
  );
};

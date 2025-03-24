
import React from 'react';
import { CardContent } from "@/components/ui/card";
import { MetadataTab } from '../MetadataTab';
import { MetadataEntry } from '../../types';

interface MetadataPanelProps {
  metadataEntries: MetadataEntry[];
  insertMetadataTag: (key: string) => void;
  setActiveTab: (tab: string) => void;
  onRefreshMetadata: () => Promise<Record<string, string>>;
}

export const MetadataPanel: React.FC<MetadataPanelProps> = ({
  metadataEntries,
  insertMetadataTag,
  setActiveTab,
  onRefreshMetadata
}) => {
  return (
    <CardContent className="mt-0 p-2 flex-1 overflow-hidden h-full flex flex-col">
      <div className="flex-1 overflow-auto">
        <MetadataTab 
          metadataEntries={metadataEntries}
          insertMetadataTag={insertMetadataTag}
          setActiveTab={setActiveTab}
          onRefreshMetadata={onRefreshMetadata}
        />
      </div>
    </CardContent>
  );
};

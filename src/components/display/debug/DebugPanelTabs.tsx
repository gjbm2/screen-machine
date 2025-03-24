
import React from 'react';
import { TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FileImage, Settings, Tag, Type } from "lucide-react";

interface DebugPanelTabsProps {
  activeTab: string;
}

export const DebugPanelTabs: React.FC<DebugPanelTabsProps> = ({ activeTab }) => {
  return (
    <TabsList className="grid grid-cols-4 mx-4">
      <TabsTrigger value="files" className="flex items-center">
        <FileImage className="mr-1 h-4 w-4" />
        Files
      </TabsTrigger>
      <TabsTrigger value="settings" className="flex items-center">
        <Settings className="mr-1 h-4 w-4" />
        Config
      </TabsTrigger>
      <TabsTrigger value="metadata" className="flex items-center">
        <Tag className="mr-1 h-4 w-4" />
        Metadata
      </TabsTrigger>
      <TabsTrigger value="caption" className="flex items-center">
        <Type className="mr-1 h-4 w-4" />
        Caption
      </TabsTrigger>
    </TabsList>
  );
};


import React from 'react';
import { CardContent } from "@/components/ui/card";
import { FilesTab } from '../FilesTab';

interface FilesPanelProps {
  outputFiles: string[];
  imageChanged?: boolean;
  imageUrl: string | null;
  customUrl: string;
  setCustomUrl: (url: string) => void;
  selectFile: (file: string) => () => void;
  selectFileDirectly: (file: string) => () => void; // Updated type to match expected return type
  isCurrentFile: (file: string) => boolean;
  formatFileName: (file: string) => string;
}

export const FilesPanel: React.FC<FilesPanelProps> = ({
  outputFiles,
  imageChanged,
  imageUrl,
  customUrl,
  setCustomUrl,
  selectFile,
  selectFileDirectly,
  isCurrentFile,
  formatFileName
}) => {
  return (
    <CardContent className="mt-0 flex-1 overflow-hidden">
      <FilesTab 
        outputFiles={outputFiles}
        imageChanged={imageChanged}
        imageUrl={imageUrl}
        customUrl={customUrl}
        setCustomUrl={setCustomUrl}
        selectFile={selectFile}
        selectFileDirectly={selectFileDirectly}
        isCurrentFile={isCurrentFile}
        formatFileName={formatFileName}
      />
    </CardContent>
  );
};

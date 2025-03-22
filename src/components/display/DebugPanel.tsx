
import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DisplayParams } from './types';

interface DebugPanelProps {
  params: DisplayParams;
  imageUrl: string | null;
  lastModified: string | null;
  lastChecked: Date | null;
  nextCheckTime: string;
  imageKey: number;
  outputFiles: string[];
}

export const formatDateTime = (date: Date | null) => {
  if (!date) return 'N/A';
  return date.toLocaleTimeString();
};

export const DebugPanel: React.FC<DebugPanelProps> = ({
  params,
  imageUrl,
  lastModified,
  lastChecked,
  nextCheckTime,
  imageKey,
  outputFiles
}) => {
  return (
    <Card className="absolute top-4 left-4 z-10 w-96 bg-white/90 dark:bg-gray-800/90 shadow-lg">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Debug Information</CardTitle>
      </CardHeader>
      <CardContent className="text-sm">
        <h3 className="font-bold mb-1">Parameters:</h3>
        <ul className="space-y-1 mb-3">
          <li><strong>output:</strong> {params.output}</li>
          <li><strong>show:</strong> {params.showMode}</li>
          <li><strong>refresh:</strong> {params.refreshInterval}s</li>
          <li><strong>background:</strong> #{params.backgroundColor}</li>
        </ul>
        <h3 className="font-bold mb-1">Image Info:</h3>
        <ul className="space-y-1 mb-3">
          <li><strong>URL:</strong> {imageUrl}</li>
          <li><strong>Last-Modified:</strong> {lastModified || 'Unknown'}</li>
          <li><strong>Last Checked:</strong> {formatDateTime(lastChecked)}</li>
          <li><strong>Next Check At:</strong> {nextCheckTime}</li>
          <li><strong>Image Key:</strong> {imageKey}</li>
        </ul>
        <h3 className="font-bold mb-1">Available Output Files:</h3>
        <div className="max-h-40 overflow-y-auto border border-gray-200 dark:border-gray-700 rounded p-2">
          {outputFiles.length > 0 ? (
            <ul className="space-y-1">
              {outputFiles.map((file, index) => (
                <li key={index} className="hover:bg-gray-100 dark:hover:bg-gray-700 p-1 rounded">
                  <a 
                    href={`/display?output=${file}&show=${params.showMode}&refresh=${params.refreshInterval}&background=${params.backgroundColor}&debug=true`}
                    className="block text-blue-500 dark:text-blue-400 hover:underline"
                  >
                    {file}
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-gray-500">No files found</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
};

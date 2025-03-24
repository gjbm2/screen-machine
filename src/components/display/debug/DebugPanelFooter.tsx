
import React from 'react';
import { CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Clock } from "lucide-react";

interface DebugPanelFooterProps {
  lastChecked: Date | null;
  applySettings: () => void;
  commitSettings: () => void;
  formatTime: (timeValue: Date | string | null) => string;
}

export const DebugPanelFooter: React.FC<DebugPanelFooterProps> = ({
  lastChecked,
  applySettings,
  commitSettings,
  formatTime
}) => {
  return (
    <CardFooter className="flex justify-between pt-4 pb-4">
      <div className="text-xs text-gray-500 flex items-center">
        <Clock className="h-3 w-3 mr-1" />
        <span>Last checked: {formatTime(lastChecked)}</span>
      </div>
      <div className="flex space-x-2">
        <Button 
          variant="outline" 
          onClick={applySettings}
        >
          Apply Changes
        </Button>
        <Button 
          onClick={() => {
            // Use window.location.href instead of navigate to ensure full page reload
            const url = window.location.pathname.replace('?debugMode=true', '');
            window.location.href = url;
          }}
        >
          Commit
        </Button>
      </div>
    </CardFooter>
  );
};

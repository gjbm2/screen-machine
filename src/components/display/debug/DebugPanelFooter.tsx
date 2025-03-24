
import React from 'react';
import { CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Save, ExternalLink } from "lucide-react";

interface DebugPanelFooterProps {
  applySettings: () => void;
  commitSettings: () => void;
}

export const DebugPanelFooter: React.FC<DebugPanelFooterProps> = ({
  applySettings,
  commitSettings
}) => {
  return (
    <CardFooter className="flex justify-end gap-3 pt-4 pb-4 sticky bottom-0 bg-card z-30 border-t">
      <Button 
        variant="outline" 
        onClick={applySettings}
        className="gap-1"
      >
        <Save className="h-4 w-4" />
        Apply Changes
      </Button>
      <Button 
        onClick={commitSettings}
        className="gap-1"
      >
        <ExternalLink className="h-4 w-4" />
        Commit
      </Button>
    </CardFooter>
  );
};

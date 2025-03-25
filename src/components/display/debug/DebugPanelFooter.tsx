
import React, { useState } from 'react';
import { CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Save, ExternalLink } from "lucide-react";

interface DebugPanelFooterProps {
  applySettings: () => void;
  commitSettings: () => void;
  isBottomFixed?: boolean;
}

export const DebugPanelFooter: React.FC<DebugPanelFooterProps> = ({
  applySettings,
  commitSettings,
  isBottomFixed = true
}) => {
  const [isCommitting, setIsCommitting] = useState(false);
  
  const handleCommit = () => {
    setIsCommitting(true);
    // Call the commitSettings function
    commitSettings();
    
    // Reset after a timeout in case the navigation doesn't happen
    setTimeout(() => {
      setIsCommitting(false);
    }, 3000);
  };
  
  return (
    <CardFooter 
      className={`flex justify-end gap-3 pt-4 pb-4 bg-card z-[60] border-t ${
        isBottomFixed ? 'sticky bottom-0' : ''
      }`}
    >
      <Button 
        variant="outline" 
        onClick={applySettings}
        className="gap-1"
      >
        <Save className="h-4 w-4" />
        Apply Changes
      </Button>
      <Button 
        onClick={handleCommit}
        className="gap-1"
        disabled={isCommitting}
      >
        <ExternalLink className="h-4 w-4" />
        {isCommitting ? 'Committing...' : 'Commit'}
      </Button>
    </CardFooter>
  );
};

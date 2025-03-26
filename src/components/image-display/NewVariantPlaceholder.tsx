
import React, { useState, useEffect } from 'react';
import { Card } from '@/components/ui/card';
import { Plus, Loader } from 'lucide-react';

interface NewVariantPlaceholderProps {
  batchId: string;
  onClick: (batchId: string) => void;
  className?: string;
}

const NewVariantPlaceholder: React.FC<NewVariantPlaceholderProps> = ({
  batchId,
  onClick,
  className = ''
}) => {
  const [isClicked, setIsClicked] = useState(false);
  
  // Reset the clicked state when the batchId prop changes
  // This ensures the spinner disappears when a new image is added
  useEffect(() => {
    setIsClicked(false);
  }, [batchId]);
  
  const handleClick = () => {
    if (isClicked) return; // Prevent multiple clicks
    setIsClicked(true);
    onClick(batchId);
    
    // Add a safety timeout to reset the spinner state after some time
    // This prevents permanent spinners if there's an error
    const timeoutId = setTimeout(() => {
      setIsClicked(false);
    }, 15000); // 15 seconds timeout
    
    return () => clearTimeout(timeoutId);
  };

  return (
    <Card 
      className={`overflow-hidden cursor-pointer border-dashed ${className}`}
      onClick={handleClick}
    >
      <div className="aspect-square relative group flex items-center justify-center bg-muted/30 hover:bg-muted/50 transition-colors">
        {isClicked ? (
          <div className="flex items-center justify-center">
            <Loader className="h-6 w-6 animate-spin text-primary" />
          </div>
        ) : (
          <div className="flex flex-col items-center text-muted-foreground p-4 text-center">
            <Plus className="h-8 w-8 mb-2" />
            <span className="text-xs">Create new variant</span>
          </div>
        )}
      </div>
    </Card>
  );
};

export default NewVariantPlaceholder;

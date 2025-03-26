
import React, { useState } from 'react';
import { Card } from '@/components/ui/card';
import { Plus, Loader } from 'lucide-react';
import { useIsMobile } from '@/hooks/use-mobile';

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
  const isMobile = useIsMobile();
  
  const handleClick = () => {
    if (isClicked) return; // Prevent multiple clicks
    setIsClicked(true);
    onClick(batchId);
    
    // No longer resetting isClicked automatically - it will remain showing the spinner
    // until the parent component re-renders with the new image
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
            <Plus className="h-8 w-8 mb-1" />
            <span className={`${isMobile ? "text-xs" : "text-sm"}`}>
              {isMobile ? "New variant" : "Create new variant"}
            </span>
          </div>
        )}
      </div>
    </Card>
  );
};

export default NewVariantPlaceholder;

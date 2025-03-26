
import React, { useState, useEffect } from 'react';
import { Card } from '@/components/ui/card';
import { Plus, Loader } from 'lucide-react';
import { useIsMobile } from '@/hooks/use-mobile';

interface NewVariantPlaceholderProps {
  batchId: string;
  onClick: (batchId: string) => void;
  className?: string;
  // Add a way to check if generation is active for this batch
  isGenerating?: boolean;
}

const NewVariantPlaceholder: React.FC<NewVariantPlaceholderProps> = ({
  batchId,
  onClick,
  className = '',
  isGenerating = false
}) => {
  const [isClicked, setIsClicked] = useState(false);
  const isMobile = useIsMobile();
  
  // Reset clicked state when generation is complete
  useEffect(() => {
    if (!isGenerating && isClicked) {
      setIsClicked(false);
    }
  }, [isGenerating, isClicked]);
  
  const handleClick = () => {
    if (isClicked) return; // Prevent multiple clicks
    setIsClicked(true);
    onClick(batchId);
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
          <div className="flex flex-col items-center text-muted-foreground p-2 text-center">
            <Plus className="h-8 w-8 mb-1" />
            <span className="text-xs whitespace-nowrap">
              {isMobile ? "New variant" : "Create new variant"}
            </span>
          </div>
        )}
      </div>
    </Card>
  );
};

export default NewVariantPlaceholder;


import React from 'react';
import { Card } from '@/components/ui/card';
import { Plus } from 'lucide-react';
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
  const isMobile = useIsMobile();
  
  const handleClick = () => {
    onClick(batchId);
  };

  return (
    <Card 
      className={`overflow-hidden cursor-pointer border-dashed ${className}`}
      onClick={handleClick}
    >
      <div className="aspect-square relative group flex items-center justify-center bg-muted/30 hover:bg-muted/50 transition-colors">
        <div className="flex flex-col items-center text-muted-foreground p-2 text-center">
          <Plus className="h-8 w-8 mb-1" />
          <span className="text-xs whitespace-nowrap">
            {isMobile ? "New variant" : "Create new variant"}
          </span>
        </div>
      </div>
    </Card>
  );
};

export default NewVariantPlaceholder;

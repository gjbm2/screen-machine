
import React from 'react';
import { Card } from '@/components/ui/card';
import { Plus } from 'lucide-react';

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
  return (
    <Card 
      className={`overflow-hidden cursor-pointer border-dashed ${className}`}
      onClick={() => onClick(batchId)}
    >
      <div className="aspect-square relative group flex items-center justify-center bg-muted/30 hover:bg-muted/50 transition-colors">
        <div className="flex flex-col items-center text-muted-foreground p-4 text-center">
          <Plus className="h-8 w-8 mb-2" />
          <span className="text-xs">Create new variant</span>
        </div>
      </div>
    </Card>
  );
};

export default NewVariantPlaceholder;

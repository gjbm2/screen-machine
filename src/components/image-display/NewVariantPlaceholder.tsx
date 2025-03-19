
import React from 'react';
import { Card } from '@/components/ui/card';
import { Plus } from 'lucide-react';

interface NewVariantPlaceholderProps {
  batchId: string;
  onClick: (batchId: string) => void;
}

const NewVariantPlaceholder: React.FC<NewVariantPlaceholderProps> = ({ batchId, onClick }) => {
  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onClick(batchId);
  };

  return (
    <Card
      className="overflow-hidden cursor-pointer border-dashed border-2 bg-secondary/10 hover:bg-secondary/20 transition-colors"
      onClick={handleClick}
    >
      <div className="aspect-square flex flex-col items-center justify-center p-4 text-muted-foreground">
        <Plus className="h-12 w-12 mb-2 text-primary/60" />
        <p className="text-sm font-medium">Create Another</p>
        <p className="text-xs mt-1 text-center">Click to generate a new image based on this prompt</p>
      </div>
    </Card>
  );
};

export default NewVariantPlaceholder;

import React from 'react';
import { BucketItem } from '@/utils/api';
import { Card } from '@/components/ui/card';
import { Image as ImageIcon } from 'lucide-react';

interface SmallGridViewProps {
  items: BucketItem[];
  onFullScreenView?: (item: BucketItem) => void;
}

const SmallGridView: React.FC<SmallGridViewProps> = ({ items, onFullScreenView }) => {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2">
      {items.map((item) => (
        <Card
          key={item.filename}
          className="aspect-square overflow-hidden cursor-pointer hover:border-primary transition-all"
          onClick={() => onFullScreenView?.(item)}
        >
          <div className="w-full h-full flex items-center justify-center bg-muted">
            {item.thumbnail_url ? (
              <img
                src={item.thumbnail_url}
                alt={item.filename}
                className="w-full h-full object-cover"
              />
            ) : (
              <ImageIcon className="h-8 w-8 text-muted-foreground" />
            )}
          </div>
        </Card>
      ))}
    </div>
  );
};

export default SmallGridView;

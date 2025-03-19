
import React from 'react';
import { ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ImageBatchItemProps {
  image: {
    url: string;
    prompt?: string;
    workflow?: string;
    timestamp?: number;
    params?: Record<string, any>;
    batchId?: string;
    batchIndex?: number;
    status?: 'generating' | 'completed' | 'error';
    refiner?: string;
    referenceImageUrl?: string;
  };
  batchId: string;
  index: number;
  total: number;
  onCreateAgain?: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
  onDeleteImage?: (batchId: string, index: number) => void;
  viewMode?: 'normal' | 'small' | 'table';
}

const ImageBatchItem: React.FC<ImageBatchItemProps> = ({
  image,
  batchId,
  index,
  total,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  viewMode = 'normal'
}) => {
  return (
    <div className="relative rounded-md overflow-hidden group">
      <div className="relative aspect-square">
        <img
          src={image.url}
          alt={image.prompt || `Generated image ${index + 1}`}
          className="w-full h-full object-cover"
        />
        {viewMode === 'small' && (
          <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-all flex items-center justify-center opacity-0 group-hover:opacity-100">
            <Button variant="outline" size="sm" className="bg-black/50 border-white/20 text-white">
              <ExternalLink className="h-4 w-4 mr-1" /> View
            </Button>
          </div>
        )}
        <div className="absolute bottom-2 right-2 bg-black/70 text-white px-2 py-0.5 rounded-full text-xs">
          {index + 1}/{total}
        </div>
      </div>
    </div>
  );
};

export default ImageBatchItem;

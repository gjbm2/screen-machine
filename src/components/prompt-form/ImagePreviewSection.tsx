
import React from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';

export interface ImagePreviewSectionProps {
  previewUrls: string[];
  onRemoveImage: (index: number) => void;
  onClearAllImages: () => void;
}

const ImagePreviewSection: React.FC<ImagePreviewSectionProps> = ({
  previewUrls,
  onRemoveImage,
  onClearAllImages
}) => {
  if (previewUrls.length === 0) {
    return null;
  }

  return (
    <div className="mt-4">
      <div className="flex justify-between items-center mb-2">
        <h3 className="text-sm font-medium">Upload Images</h3>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClearAllImages}
          className="h-6 text-xs text-muted-foreground"
        >
          Clear All
        </Button>
      </div>
      <ScrollArea className="h-[100px]">
        <div className="flex flex-wrap gap-2">
          {previewUrls.map((url, index) => (
            <div key={index} className="relative group">
              <img
                src={url}
                alt={`Preview ${index + 1}`}
                className="h-[80px] w-auto rounded object-cover border"
              />
              <Button
                variant="destructive"
                size="icon"
                className="absolute top-1 right-1 h-5 w-5 opacity-0 group-hover:opacity-100 transition-opacity"
                onClick={() => onRemoveImage(index)}
              >
                <X className="h-3 w-3" />
              </Button>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
};

export default ImagePreviewSection;

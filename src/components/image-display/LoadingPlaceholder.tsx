
import React from 'react';
import { Card } from '@/components/ui/card';
import ImageLoadingState from './ImageLoadingState';

interface LoadingPlaceholderProps {
  prompt: string | null;
}

const LoadingPlaceholder: React.FC<LoadingPlaceholderProps> = ({ prompt }) => {
  return (
    <Card className="overflow-hidden w-full rounded-b-lg rounded-t-none">
      <div className="aspect-square flex flex-col items-center justify-center bg-secondary/10">
        <ImageLoadingState />
        {prompt && (
          <div className="px-4 mt-4 max-w-full">
            <p className="text-sm text-center text-muted-foreground truncate">
              Generating: {prompt}
            </p>
          </div>
        )}
      </div>
    </Card>
  );
};

export default LoadingPlaceholder;

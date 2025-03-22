
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
          <p className="text-sm text-center text-muted-foreground px-4 mt-4">
            Generating: {prompt}
          </p>
        )}
      </div>
    </Card>
  );
};

export default LoadingPlaceholder;

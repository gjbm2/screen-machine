
import React from 'react';
import { Card } from '@/components/ui/card';

interface LoadingPlaceholderProps {
  prompt: string | null;
}

const LoadingPlaceholder: React.FC<LoadingPlaceholderProps> = ({ prompt }) => {
  return (
    <Card className="overflow-hidden w-full rounded-b-lg rounded-t-none">
      <div className="aspect-square flex flex-col items-center justify-center bg-secondary/10">
        <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent mb-4"></div>
        {prompt && (
          <div className="text-sm text-center text-muted-foreground px-4 w-full">
            <div className="truncate max-w-full">
              Generating: {prompt}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
};

export default LoadingPlaceholder;

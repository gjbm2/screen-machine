
import React from 'react';
import { Card } from '@/components/ui/card';

interface LoadingPlaceholderProps {
  prompt: string | null;
}

const LoadingPlaceholder: React.FC<LoadingPlaceholderProps> = ({ prompt }) => {
  return (
    <Card className="overflow-hidden max-w-xs mx-auto">
      <div className="aspect-square flex items-center justify-center bg-secondary/20">
        <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
        {prompt && (
          <p className="text-sm text-center text-muted-foreground absolute mt-20">
            Generating: {prompt}
          </p>
        )}
      </div>
    </Card>
  );
};

export default LoadingPlaceholder;

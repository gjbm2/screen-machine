
import React from 'react';
import { Card } from '@/components/ui/card';
import { Loader } from 'lucide-react';

interface LoadingPlaceholderProps {
  prompt: string; // Make prompt required
}

const LoadingPlaceholder: React.FC<LoadingPlaceholderProps> = ({ prompt }) => {
  return (
    <Card className="overflow-hidden w-full">
      <div className="aspect-square flex flex-col items-center justify-center bg-secondary/10">
        <div className="flex items-center justify-center mb-4">
          <Loader className="h-12 w-12 animate-spin text-primary" />
        </div>
        <p className="text-sm text-center font-medium text-foreground mb-2">
          Generating...
        </p>
        <p className="text-sm text-center text-muted-foreground px-4 max-w-lg">
          {prompt}
        </p>
      </div>
    </Card>
  );
};

export default LoadingPlaceholder;


import React from 'react';
import { Card } from '@/components/ui/card';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface GenerationFailedPlaceholderProps {
  prompt: string | null;
  onRetry?: () => void;
}

const GenerationFailedPlaceholder: React.FC<GenerationFailedPlaceholderProps> = ({ 
  prompt, 
  onRetry 
}) => {
  return (
    <Card className="overflow-hidden w-full rounded-b-lg rounded-t-none">
      <div className="aspect-square flex flex-col items-center justify-center bg-secondary/10">
        <AlertTriangle className="h-12 w-12 text-yellow-500 mb-4" />
        <p className="text-sm text-center font-medium text-foreground mb-2">
          Generation failed
        </p>
        {prompt && (
          <p className="text-sm text-center text-muted-foreground px-4 mb-4 max-w-lg">
            Failed to generate: {prompt}
          </p>
        )}
        {onRetry && (
          <Button 
            variant="outline" 
            size="sm" 
            onClick={onRetry}
            className="mt-2"
          >
            Try again
          </Button>
        )}
      </div>
    </Card>
  );
};

export default GenerationFailedPlaceholder;

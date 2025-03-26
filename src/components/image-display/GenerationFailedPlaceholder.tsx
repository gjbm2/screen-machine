
import React from 'react';
import { AlertTriangle, RotateCcw, Trash } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface GenerationFailedPlaceholderProps {
  errorMessage: string;
  onRemove?: () => void;
  onRetry?: () => void;
  isCompact?: boolean;
}

const GenerationFailedPlaceholder: React.FC<GenerationFailedPlaceholderProps> = ({
  errorMessage,
  onRemove,
  onRetry,
  isCompact = false
}) => {
  if (isCompact) {
    return (
      <div className="flex flex-col items-center justify-center text-center p-2">
        <AlertTriangle className="h-5 w-5 text-destructive mb-1" />
        <p className="text-xs text-destructive-foreground truncate max-w-full">
          Failed
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center h-full text-center p-4">
      <AlertTriangle className="h-10 w-10 text-destructive mb-3" />
      <h3 className="text-lg font-medium text-destructive-foreground mb-2">Generation Failed</h3>
      <p className="text-sm text-muted-foreground mb-4 max-w-xs">{errorMessage}</p>
      
      <div className="flex gap-2">
        {onRetry && (
          <Button 
            variant="outline" 
            size="sm"
            onClick={onRetry}
            className="flex items-center space-x-1"
          >
            <RotateCcw className="h-3.5 w-3.5 mr-1" />
            <span>Try Again</span>
          </Button>
        )}
        
        {onRemove && (
          <Button 
            variant="destructive" 
            size="sm"
            onClick={onRemove}
            className="flex items-center space-x-1"
          >
            <Trash className="h-3.5 w-3.5 mr-1" />
            <span>Remove</span>
          </Button>
        )}
      </div>
    </div>
  );
};

export default GenerationFailedPlaceholder;

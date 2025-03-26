
import React from 'react';
import { Card } from '@/components/ui/card';
import { AlertTriangle, Trash2, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface GenerationFailedPlaceholderProps {
  prompt?: string | null; // Changed to optional
  onRetry?: () => void;
  onRemove?: () => void;
  isCompact?: boolean; // Ensure we use isCompact, not compact
  errorMessage?: string | null; // Added error message property for more detailed errors
}

const GenerationFailedPlaceholder: React.FC<GenerationFailedPlaceholderProps> = ({ 
  prompt = null, // Default to null
  onRetry,
  onRemove,
  isCompact = false,
  errorMessage = null
}) => {
  // Use simpler UI for small/compact view
  if (isCompact) {
    return (
      <div className="aspect-square flex flex-col items-center justify-center bg-secondary/10 relative">
        <AlertTriangle className="h-8 w-8 text-yellow-500" />
        
        <div className="absolute bottom-1 right-1 flex space-x-1">
          {onRetry && (
            <Button 
              variant="outline" 
              size="icon" 
              className="h-7 w-7 bg-background/80"
              onClick={onRetry}
            >
              <RefreshCw className="h-3 w-3 text-blue-500" />
            </Button>
          )}
          
          {onRemove && (
            <Button 
              variant="outline" 
              size="icon" 
              className="h-7 w-7 bg-background/80" 
              onClick={onRemove}
            >
              <Trash2 className="h-3 w-3 text-red-500" />
            </Button>
          )}
        </div>
      </div>
    );
  }

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
        {errorMessage && (
          <p className="text-xs text-center text-red-500 px-4 mb-4 max-w-lg">
            Error: {errorMessage}
          </p>
        )}
        <div className="flex space-x-2">
          {onRetry && (
            <Button 
              variant="outline" 
              size="sm" 
              onClick={onRetry}
              className="flex items-center"
            >
              <RefreshCw className="h-3 w-3 mr-1.5 text-blue-500" />
              Try again
            </Button>
          )}
          
          {onRemove && (
            <Button 
              variant="outline" 
              size="sm" 
              onClick={onRemove}
              className="flex items-center text-red-500 hover:text-red-600 border-red-200 hover:border-red-300 hover:bg-red-50"
            >
              <Trash2 className="h-3 w-3 mr-1.5" />
              Remove
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
};

export default GenerationFailedPlaceholder;


import React, { useState, useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { Image, Info, Copy, ArrowRight, Trash, RefreshCw } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import ImageInfoDialog from './ImageInfoDialog';
import ReferenceImageDialog from './ReferenceImageDialog';

interface ImageActionsPanelProps {
  show: boolean;
  imageUrl: string;
  onCreateAgain?: () => void;
  onUseAsInput?: () => void;
  onDeleteImage?: () => void;
  generationInfo: {
    prompt: string;
    workflow: string;
    params?: Record<string, any>;
  };
  referenceImageUrl?: string;
  title?: string;
}

const ImageActionsPanel: React.FC<ImageActionsPanelProps> = ({
  show,
  imageUrl,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  generationInfo,
  referenceImageUrl,
  title
}) => {
  const [infoDialogOpen, setInfoDialogOpen] = useState(false);
  const [referenceDialogOpen, setReferenceDialogOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  // Process reference images for the info panel
  const referenceImages = referenceImageUrl ? 
    referenceImageUrl.split(',').map(url => url.trim()).filter(url => url !== '') : 
    [];

  // Enhanced image object for the info dialog
  const enhancedImage = {
    url: imageUrl,
    prompt: generationInfo.prompt,
    workflow: generationInfo.workflow,
    params: generationInfo.params,
    referenceImageUrl: referenceImageUrl,
    title: title
  };

  if (!show) return null;

  return (
    <div 
      ref={panelRef}
      className="absolute bottom-0 left-0 right-0 bg-white dark:bg-gray-800 border-t p-2 pt-1 z-10"
    >
      <div className="flex flex-wrap gap-2 justify-center">
        {onCreateAgain && (
          <Button
            variant="outline"
            size="sm"
            onClick={onCreateAgain} 
            className="text-xs gap-1"
            title="Create again with the same settings"
          >
            <RefreshCw size={14} />
            <span className="hidden sm:inline">Go Again</span>
          </Button>
        )}
        
        {onUseAsInput && (
          <Button
            variant="outline"
            size="sm"
            onClick={onUseAsInput}
            className="text-xs gap-1"
            title="Use this image as input for a new generation"
          >
            <ArrowRight size={14} />
            <span className="hidden sm:inline">Use as Input</span>
          </Button>
        )}
        
        {referenceImages.length > 0 && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setReferenceDialogOpen(true)}
            className="text-xs gap-1"
            title="View reference images"
          >
            <Image size={14} />
            <span className="hidden sm:inline">References</span>
          </Button>
        )}
        
        <Button
          variant="outline"
          size="sm"
          onClick={() => setInfoDialogOpen(true)}
          className="text-xs gap-1"
          title="View image details"
        >
          <Info size={14} />
          <span className="hidden sm:inline">Info</span>
        </Button>
        
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            navigator.clipboard.writeText(generationInfo.prompt)
              .then(() => {
                console.log('Prompt copied to clipboard');
              })
              .catch(err => {
                console.error('Failed to copy prompt: ', err);
              });
          }}
          className="text-xs gap-1"
          title="Copy prompt to clipboard"
        >
          <Copy size={14} />
          <span className="hidden sm:inline">Copy Prompt</span>
        </Button>
        
        {onDeleteImage && (
          <Button
            variant="outline"
            size="sm"
            onClick={onDeleteImage}
            className="text-xs gap-1 text-red-500 hover:text-red-700 hover:bg-red-50"
            title="Delete this image"
          >
            <Trash size={14} />
            <span className="hidden sm:inline">Delete</span>
          </Button>
        )}
      </div>
      
      {/* Image Info Dialog */}
      <ImageInfoDialog
        isOpen={infoDialogOpen}
        onOpenChange={setInfoDialogOpen}
        image={enhancedImage}
      />
      
      {/* Reference Image Dialog */}
      {referenceImageUrl && (
        <ReferenceImageDialog
          isOpen={referenceDialogOpen}
          onOpenChange={setReferenceDialogOpen}
          imageUrl={referenceImageUrl}
        />
      )}
    </div>
  );
};

export default ImageActionsPanel;

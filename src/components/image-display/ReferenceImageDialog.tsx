
import React from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

interface ReferenceImageDialogProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  imageUrl: string;
}

const ReferenceImageDialog: React.FC<ReferenceImageDialogProps> = ({
  isOpen,
  onOpenChange,
  imageUrl
}) => {
  return (
    <Dialog open={isOpen} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Reference Image</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col items-center">
          <p className="text-sm mb-2 text-muted-foreground">Reference image used for generation</p>
          <div className="border rounded-md overflow-hidden">
            <img 
              src={imageUrl} 
              alt="Reference image"
              className="w-full h-auto object-contain"
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default ReferenceImageDialog;

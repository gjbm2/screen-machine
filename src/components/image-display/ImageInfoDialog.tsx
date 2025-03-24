
import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { format } from 'date-fns';

interface ImageInfoDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  image?: any;
  onDownload?: () => void;
}

const ImageInfoDialog: React.FC<ImageInfoDialogProps> = ({
  open,
  onOpenChange,
  image,
  onDownload
}) => {
  if (!image) return null;

  const handleDownload = () => {
    if (onDownload) {
      onDownload();
    }
  };

  const formatDate = (date: Date) => {
    return format(date, 'MMM d, yyyy h:mm a');
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Image Information</DialogTitle>
        </DialogHeader>
        
        <div className="grid gap-4 py-4">
          <div className="grid grid-cols-4 items-center gap-4">
            <div className="font-medium">Prompt</div>
            <div className="col-span-3">{image.prompt || "No prompt"}</div>
          </div>
          
          <div className="grid grid-cols-4 items-center gap-4">
            <div className="font-medium">Workflow</div>
            <div className="col-span-3">{image.workflow || "Unknown"}</div>
          </div>
          
          {image.timestamp && (
            <div className="grid grid-cols-4 items-center gap-4">
              <div className="font-medium">Created</div>
              <div className="col-span-3">{formatDate(new Date(image.timestamp))}</div>
            </div>
          )}
        </div>
        
        <DialogFooter>
          {onDownload && (
            <Button onClick={handleDownload}>
              Download
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ImageInfoDialog;

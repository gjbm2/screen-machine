
import React, { useEffect, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Info } from 'lucide-react';

export interface AboutDialogProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

const AboutDialog: React.FC<AboutDialogProps> = ({ 
  open: controlledOpen, 
  onOpenChange: controlledOnOpenChange 
}) => {
  // For uncontrolled usage
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  
  // Determine if we're in controlled or uncontrolled mode
  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : uncontrolledOpen;
  const onOpenChange = isControlled ? controlledOnOpenChange : setUncontrolledOpen;
  
  // Add an effect to ensure we clean up any potential stuck event handlers
  useEffect(() => {
    // This ensures we properly clean up when the dialog is unmounted
    return () => {
      // Force any hidden elements to be removed from the DOM
      document.body.style.pointerEvents = '';
      document.body.style.cursor = '';
    };
  }, []);

  // Create a handler to ensure we clean up when dialog is closed
  const handleOpenChange = (newOpenState: boolean) => {
    // When closing, ensure body pointer events are restored
    if (!newOpenState) {
      // Small delay to ensure everything is cleaned up
      setTimeout(() => {
        document.body.style.pointerEvents = '';
        document.body.style.cursor = '';
      }, 10);
    }
    
    if (onOpenChange) {
      onOpenChange(newOpenState);
    }
  };

  return (
    isControlled ? (
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>About Imagine</DialogTitle>
            <DialogDescription>
              <div className="space-y-4 mt-4">
                <p>
                  Imagine is an AI-powered image generation tool that turns your text prompts into stunning visuals.
                </p>
                <p>
                  Simply describe what you want to see or upload reference images, and watch as artificial intelligence transforms your ideas into art within seconds.
                </p>
                <p className="text-xs text-muted-foreground">
                  Version 1.0.0 • Made with ❤️ by your team
                </p>
              </div>
            </DialogDescription>
          </DialogHeader>
        </DialogContent>
      </Dialog>
    ) : (
      <DropdownMenuItem onSelect={() => setUncontrolledOpen(true)}>
        <Info className="h-4 w-4 mr-2" />
        <span>About</span>
        
        <Dialog open={open} onOpenChange={handleOpenChange}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>About Imagine</DialogTitle>
              <DialogDescription>
                <div className="space-y-4 mt-4">
                  <p>
                    Imagine is an AI-powered image generation tool that turns your text prompts into stunning visuals.
                  </p>
                  <p>
                    Simply describe what you want to see or upload reference images, and watch as artificial intelligence transforms your ideas into art within seconds.
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Version 1.0.0 • Made with ❤️ by your team
                  </p>
                </div>
              </DialogDescription>
            </DialogHeader>
          </DialogContent>
        </Dialog>
      </DropdownMenuItem>
    )
  );
};

export default AboutDialog;

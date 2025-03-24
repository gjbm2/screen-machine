
import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface AboutDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const AboutDialog: React.FC<AboutDialogProps> = ({ open, onOpenChange }) => {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
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
  );
};

export default AboutDialog;

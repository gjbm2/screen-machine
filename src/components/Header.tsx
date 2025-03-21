
import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

const Header = () => {
  return (
    <header className="flex justify-between items-center py-4 px-4 sm:px-6 md:px-8 animate-fade-in">
      <Dialog>
        <DialogTrigger asChild>
          <div className="text-xl font-medium tracking-tight cursor-pointer hover:text-primary transition-colors">
            imagine
          </div>
        </DialogTrigger>
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
    </header>
  );
};

export default Header;
